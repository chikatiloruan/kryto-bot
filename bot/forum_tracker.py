# bot/forum_tracker.py
"""
Исправленная и улучшенная версия forum_tracker.py.
Сохраняет весь функционал, который был у тебя — но убраны синтаксические ошибки,
понятно организованы методы, добавлен fetch_latest_post_id, улучшен парсинг тем
и сообщений, добавлен безопасный лог и отладочные хелперы.

Важно: ожидает, что в проекте есть:
 - bot/utils.py с функциями: normalize_url, detect_type, extract_thread_id,
   extract_post_id_from_article, log_info, log_error
 - bot/storage.py с list_all_tracks и update_last
 - config.py (опционально) с FORUM_BASE, XF_USER, XF_SESSION, XF_TFA_TRUST, POLL_INTERVAL_SEC, XF_CSRF

"""
from __future__ import annotations

import re
import threading
import time
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from urllib.parse import urljoin
from .utils import (
    normalize_url, detect_type,
    extract_thread_id, extract_post_id_from_article,
    log_info, log_error
)
from .storage import list_all_tracks, update_last
import traceback
import datetime

# ======================================================================
#   CONFIG / DEFAULTS
# ======================================================================
try:
    from config import XF_USER, XF_SESSION, XF_TFA_TRUST, FORUM_BASE, POLL_INTERVAL_SEC, XF_CSRF
except Exception:
    XF_USER = ""
    XF_SESSION = ""
    XF_TFA_TRUST = ""
    FORUM_BASE = ""
    XF_CSRF = ""
    POLL_INTERVAL_SEC = 20

DEFAULT_POLL = 20
try:
    POLL = int(POLL_INTERVAL_SEC)
    if POLL <= 0:
        POLL = DEFAULT_POLL
except Exception:
    POLL = DEFAULT_POLL

# ======================================================================
#  Simple logging helpers
# ======================================================================

def debug(msg: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_info(str(msg))
    except Exception:
        print(f"[{now}] [DEBUG] {msg}")


def warn(msg: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_error(str(msg))
    except Exception:
        print(f"[{now}] [WARNING] {msg}")

# ======================================================================
# COOKIE helpers and fetch
# ======================================================================

def build_cookies() -> dict:
    """Return cookies dict (for requests)."""
    return {
        "xf_user": globals().get("XF_USER", XF_USER) or "",
        "xf_session": globals().get("XF_SESSION", XF_SESSION) or "",
        "xf_tfa_trust": globals().get("XF_TFA_TRUST", XF_TFA_TRUST) or "",
        "xf_csrf": globals().get("XF_CSRF", XF_CSRF) or "",
    }


# ======================================================================
#  Parsers: thread posts and forum topics
# ======================================================================

def parse_thread_posts(html: str, page_url: str) -> List[Dict]:
    """
    Парсер постов для XenForo-разметки MatRP.
    Ищет article.message-body.* и извлекает ID, автора, дату и текст.
    Поддерживает несколько вариантов контейнера текста: div.bbWrapper и
    div.message-userContent.lbContainer.js-lbContainer и fallback на сам article.
    Возвращает список постов в порядке появления на странице (от первого к последнему).
    """
    soup = BeautifulSoup(html or "", "html.parser")

    # Найдём возможные посты — несколько селекторов на случай разных версий
    posts_nodes = soup.select("article.message-body.js-selectToQuote")
    if not posts_nodes:
        # более общий поиск: article с data-post-id
        posts_nodes = soup.select("article[data-post-id], article[id^='js-post-']")

    out: List[Dict] = []
    for msg in posts_nodes:
        try:
            # ID поста
            pid = (
                msg.get("data-lb-id")
                or msg.get("data-id")
                or msg.get("data-post-id")
                or ""
            )

            if not pid:
                art = msg.find_parent("article")
                if art:
                    pid = extract_post_id_from_article(str(art))

            pid = str(pid)

            # Автор: ищем ближайший элемент username
            user = (
                msg.find_previous("a", class_="username")
                or msg.find_previous("h4", class_="message-name")
                or msg.find_previous("span", class_="username")
            )
            author = user.get_text(strip=True) if user else "Неизвестно"

            # Дата
            t = msg.find_previous("time")
            date = t.get("datetime") if t and t.get("datetime") else (t.get_text(strip=True) if t else "")

            # Текст: пробуем несколько вариантов
            body = (
                msg.select_one("div.bbWrapper")
                or msg.select_one("div.message-userContent.lbContainer.js-lbContainer")
                or msg.select_one("div.message-userContent")
            )
            if body:
                text = body.get_text("\n", strip=True)
            else:
                # fallback — весь узел
                text = msg.get_text("\n", strip=True)

            # Нормализуем пустые строки
            text = re.sub(r"\n{2,}", "\n", text).strip()

            link = page_url.rstrip("/") + f"#post-{pid}"

            out.append({
                "id": pid,
                "author": author,
                "date": date,
                "text": text,
                "link": link,
            })
        except Exception as e:
            warn(f"parse_thread_posts error: {e}")
            continue

    return out


def parse_forum_topics(html: str, base_url: str) -> List[Dict]:
    """
    Парсер списка тем на странице раздела. Возвращает список словарей с
    tid, title, author, url, pinned.

    Работает с текущими классами MatRP/XenForo: .structItem.structItem--thread
    и ищет js-threadListItem-<tid> в классах или извлекает tid из ссылки.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    topics: List[Dict] = []

    # Ищем только элементы-темы
    blocks = soup.select(".structItem.structItem--thread, .structItem--thread")
    if not blocks:
        # fallback — все structItem
        blocks = soup.select(".structItem")

    seen = set()

    for it in blocks:
        try:
            # попробуем извлечь tid из класса js-threadListItem-XXXX
            tid = None
            classes = it.get("class", []) or []
            for c in classes:
                if isinstance(c, str) and c.startswith("js-threadListItem-"):
                    tid = c.replace("js-threadListItem-", "")
                    break

            # fallback: intentar extraer из ссылки
            if not tid:
                a = it.select_one(".structItem-title a[data-preview-url], .structItem-title a[href], a[href*='/threads/']")
                if a:
                    href = a.get("href", "")
                    # ищем ".<tid>/" или "/threads/...<tid>/"
                    m = re.search(r"\.(\d+)(?:/|$)", href)
                    if not m:
                        m = re.search(r"threads/.+\.(\d+)(?:/|$)", href)
                    if m:
                        tid = m.group(1)

            if not tid:
                continue

            tid = int(tid)
            if tid in seen:
                continue
            seen.add(tid)

            # Заголовок и ссылка
            a = it.select_one(".structItem-title a[data-preview-url], .structItem-title a[href], a[href*='/threads/']")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if href.startswith("http"):
                url = href
            else:
                # базовый путь: base_url может быть like https://forum.matrp.ru/index.php?forums/xxx
                base_root = base_url.split("/index.php")[0]
                url = urljoin(base_root + "/", href.lstrip("/"))

            # автор
            auth_el = it.select_one(".username, .structItem-parts .username")
            author = auth_el.get_text(strip=True) if auth_el else "Unknown"

            # pinned detection
            pinned = any((isinstance(c, str) and ("sticky" in c or "pinned" in c or "structItem--pinned" in c)) for c in classes)

            topics.append({
                "tid": tid,
                "title": title,
                "author": author,
                "url": url,
                "pinned": bool(pinned)
            })
        except Exception:
            continue

    return topics


# ======================================================================
#  ForumTracker class
# ======================================================================
class ForumTracker:
    """
    ForumTracker supports:
      - ForumTracker(vk)
      - ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, vk)

    Все сетевые операции идут через self.session, чтобы держать куки.
    """

    def __init__(self, *args):
        self.interval = POLL
        self._running = False
        self._keepalive_running = True
        self.vk = None

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
            "Referer": FORUM_BASE
        })

        # signature 1: ForumTracker(vk)
        if len(args) == 1:
            self.vk = args[0]
            # set cookies from config
            for k, v in build_cookies().items():
                if v:
                    try:
                        self.session.cookies.set(k, v)
                    except Exception:
                        try:
                            domain = FORUM_BASE.replace("https://", "").replace("http://", "").split("/")[0]
                            self.session.cookies.set(k, v, domain=domain)
                        except Exception:
                            pass

        # signature 2: ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, vk)
        elif len(args) >= 4:
            xf_user, xf_tfa_trust, xf_session, vk = args[:4]
            self.vk = vk
            globals()["XF_USER"] = xf_user
            globals()["XF_TFA_TRUST"] = xf_tfa_trust
            globals()["XF_SESSION"] = xf_session
            # set cookies with proper domain
            domain = ""
            try:
                domain = FORUM_BASE.replace("https://", "").replace("http://", "").split("/")[0]
            except Exception:
                domain = None
            if xf_user:
                try:
                    self.session.cookies.set("xf_user", xf_user, domain=domain)
                except Exception:
                    self.session.cookies.set("xf_user", xf_user)
            if xf_tfa_trust:
                try:
                    self.session.cookies.set("xf_tfa_trust", xf_tfa_trust, domain=domain)
                except Exception:
                    self.session.cookies.set("xf_tfa_trust", xf_tfa_trust)
            if xf_session:
                try:
                    self.session.cookies.set("xf_session", xf_session, domain=domain)
                except Exception:
                    self.session.cookies.set("xf_session", xf_session)
        else:
            raise TypeError("ForumTracker expected (vk) or (XF_USER, XF_TFA_TRUST, XF_SESSION, vk)")

        # register trigger
        if hasattr(self.vk, "set_trigger"):
            try:
                self.vk.set_trigger(self.force_check)
            except Exception:
                pass

        # start keepalive thread
        threading.Thread(target=self._keepalive_loop, daemon=True).start()

    # -----------------------------------------------------------------
    # Утилиты доступа к сети через session
    # -----------------------------------------------------------------
    def fetch_html(self, url: str, timeout: int = 15) -> str:
        """
        Загрузить HTML используя self.session (с куками).
        """
        if not url:
            return ""

        try:
            url = normalize_url(url)
        except Exception:
            pass

        debug(f"[FETCH] GET {url}")
        try:
            r = self.session.get(url, timeout=timeout)
            debug(f"[FETCH] {url} -> {getattr(r, 'status_code', 'ERR')}")
            if getattr(r, "status_code", 0) == 200:
                return r.text
            warn(f"HTTP {getattr(r, 'status_code', 'ERR')} for {url}")
            return ""
        except Exception as e:
            warn(f"fetch_html error: {e}")
            return ""

    def get(self, url: str, **kwargs):
        try:
            return self.session.get(url, **kwargs)
        except Exception as e:
            warn(f"session.get error: {e}")
            raise

    # --- API control ---
    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        try:
            log_info(f"ForumTracker started (interval={self.interval})")
        except Exception:
            debug(f"ForumTracker started (interval={self.interval})")

    def stop(self):
        self._running = False
        self._keepalive_running = False
        try:
            log_info("ForumTracker stopped")
        except Exception:
            debug("ForumTracker stopped")

    def force_check(self):
        threading.Thread(target=self.check_all, daemon=True).start()

    def _loop(self):
        while self._running:
            try:
                self.check_all()
            except Exception as e:
                warn(f"loop error: {e}")
                traceback.print_exc()
            time.sleep(self.interval)

    def check_all(self):
        rows = list_all_tracks()
        if not rows:
            return
        by_url = {}
        for peer_id, url, typ, last_id in rows:
            by_url.setdefault(url, []).append((peer_id, typ, last_id))
        for url, subs in by_url.items():
            try:
                self._process_url(url, subs)
            except Exception as e:
                warn(f"_process_url error for {url}: {e}")
                traceback.print_exc()

    # -----------------------------------------------------------------
    # core processor
    # -----------------------------------------------------------------
     def _process_url(self, url: str, subscribers):
        url = normalize_url(url)

        if not url.startswith(FORUM_BASE):
           return

        html = self.fetch_html(url)
        if not html:
            return

        typ = detect_type(url)

    # ============================================================
    # THREAD — новые сообщения
    # ============================================================
        if typ == "thread":
            posts = parse_thread_posts(html, url)
            if not posts:
                return

            newest = posts[-1]
            newest_id = int(newest["id"])

            for peer_id, _, last in subscribers:

            # last_id всегда int
                try:
                    last_id = int(last)
               except:
                    last_id = 0

                if newest_id > last_id:
                # отправляем уведомление
                   msg = (
                        f"📝 Новый пост\n"
                        f"👤 {newest['author']}  •  {newest['date']}\n\n"
                        f"{(newest['text'][:1500] + '...') if len(newest['text'])>1500 else newest['text']}\n\n"
                        f"🔗 {newest['link']}"
                    )
                    try:
                        self.vk.send(peer_id, msg)
                    except:
                        pass

                # обновляем last_id
                    try:
                        update_last(peer_id, url, str(newest_id))
                    except:
                        pass

            return

    # ============================================================
    # FORUM — новые темы (включая pinned)
    # ============================================================
        if typ == "forum":
            topics = parse_forum_topics(html, url)
            if not topics:
                return

        # список всех tid
            tid_list = [int(t["tid"]) for t in topics]
            newest_tid = max(tid_list)

            for peer_id, _, last in subscribers:
 
                try:
                    last_id = int(last)
                except:
                    last_id = 0

            # фильтруем новые темы
                new_topics = [t for t in topics if int(t["tid"]) > last_id]

                if new_topics:
                # порядок от старой к новой
                    for t in sorted(new_topics, key=lambda x: int(x["tid"])):
                        msg = (
                            "🆕 Новая тема!\n\n"
                            f"📄 {t['title']}\n"
                            f"👤 Автор: {t['author']}\n"
                            f"🔗 {t['url']}"
                        )
                        try:
                            self.vk.send(peer_id, msg)
                        except:
                            pass

                # обновляем last_id
                    update_last(peer_id, url, str(newest_tid))

            return

    # ============================================================
    # UNKNOWN
    # ============================================================
        print("[TRACK] Unknown type:", typ)

    # -----------------------------------------------------------------
    # manual fetch posts — returns list (used by /checkfa)
    # -----------------------------------------------------------------
    def manual_fetch_posts(self, url: str) -> List[Dict]:
        url = normalize_url(url)
        debug(f"[manual_fetch_posts] URL = {url}")
        debug(f"[manual_fetch_posts] Cookies = {build_cookies()}")
        if not url.startswith(FORUM_BASE):
            raise ValueError("URL outside FORUM_BASE")
        html = self.fetch_html(url)
        if not html:
            raise RuntimeError("Failed to fetch page (check cookies)")
        posts = parse_thread_posts(html, url)
        debug(f"[manual_fetch_posts] Parsed posts = {len(posts)}")
        return posts

    # -----------------------------------------------------------------
    # debug what bot sees for reply form
    # -----------------------------------------------------------------
    def debug_reply_form(self, url: str) -> str:
        url = normalize_url(url)
        html = self.fetch_html(url)
        cookies = build_cookies()
        if not html:
            return "❌ Не удалось загрузить страницу\nCookies: " + str(cookies)
        soup = BeautifulSoup(html, "html.parser")
        form = (
            soup.select_one("form[action*='add-reply']") or
            soup.select_one("form.js-quickReply") or
            soup.select_one("form[data-xf-init*='quick-reply']") or
            soup.select_one("form[action*='post']")
        )
        textarea = None
        if form:
            textarea = (
                form.select_one("textarea[name='message_html']") or
                form.select_one("textarea[name='message']") or
                form.select_one("textarea")
            )
        logged = (
            ("logout" in html.lower()) or
            ("выйти" in html.lower()) or
            ("data-xf-init=\"member-tooltip\"" in html)
        )
        return (
            "🔍 DEBUG REPLY FORM\n"
            f"✔ Logged in: {logged}\n"
            f"✔ Cookies OK: {bool(cookies)}\n"
            f"✔ Form found: {bool(form)}\n"
            f"✔ Textarea found: {bool(textarea)}\n"
            f"✔ Textarea name: {textarea.get('name') if textarea else '—'}\n"
            f"✔ Action: {form.get('action') if form else '—'}\n"
            "-----------------------------------\n"
            "Cookies:\n"
            f"{cookies}\n"
            "-----------------------------------\n"
            "HTML снизу страницы:\n"
            + html[-2000:]
        )

    # -----------------------------------------------------------------
    # fetch latest post id helper (used by command handler to seed last)
    # -----------------------------------------------------------------
    def fetch_latest_post_id(self, url: str) -> Optional[str]:
        """Возвращает id самого свежего поста на thread-странице или None."""
        try:
            html = self.fetch_html(url)
            if not html:
                return None
            posts = parse_thread_posts(html, url)
            if not posts:
                return None
            return str(posts[-1]["id"]) if posts else None
        except Exception:
            return None

    # -----------------------------------------------------------------
    # Improved post_message: tries normal POST then multipart fallback
    # -----------------------------------------------------------------
    def post_message(self, url: str, message: str) -> Dict:
        debug(f"[POST] Sending to: {url}")
        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            return {"ok": False, "error": "URL outside FORUM_BASE"}

        try:
            debug(f"[POST] Cookies: xf_user={XF_USER[:6]}..., xf_session={XF_SESSION[:6]}..., xf_tfa={XF_TFA_TRUST[:6]}...")
        except Exception:
            debug("[POST] Cookies: (not available)")

        html = self.fetch_html(url)
        if not html:
            return {"ok": False, "error": "Cannot fetch page"}

        soup = BeautifulSoup(html, "html.parser")

        form = (
            soup.select_one("form[action*='add-reply']") or
            soup.select_one("form.js-quickReply") or
            soup.select_one("form[data-xf-init*='quick-reply']") or
            soup.select_one("form[action*='post']")
        )
        debug(f"[POST] Form found: {bool(form)}")
        if not form:
            return {"ok": False, "error": "Reply form not found"}

        action = form.get("action") or url
        if not action.startswith("http"):
            action = urljoin(FORUM_BASE, action.lstrip("/"))
        debug(f"[POST] Form action: {action}")

        payload: Dict[str, str] = {}
        for inp in form.select("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "") or ""

        payload["_xfWithData"] = "1"
        payload["_xfResponseType"] = "json"

        if not payload.get("_xfToken"):
            t = soup.find("input", {"name": "_xfToken"})
            if t:
                payload["_xfToken"] = t.get("value", "")

        try:
            payload["_xfRequestUri"] = url.replace(FORUM_BASE, "") or "/"
        except Exception:
            payload["_xfRequestUri"] = "/"

        textarea = (
            form.select_one("textarea[name='message_html']") or
            form.select_one("textarea[name='message']") or
            form.select_one("textarea[data-original-name='message']") or
            form.select_one("textarea")
        )
        debug(f"[POST] Textarea found: {bool(textarea)}")
        if not textarea:
            return {"ok": False, "error": "Textarea not found"}

        textarea_name = textarea.get("name") or "message"
        html_msg = f"<p>{message}</p>"

        payload[textarea_name] = html_msg
        payload["message"] = message
        payload["message_html"] = html_msg

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }

        normal_error = None
        multipart_error = None

        debug("[POST] Trying normal mode...")
        try:
            r = self.session.post(action, data=payload, headers=headers, timeout=25)
            debug(f"[POST] Normal POST code: {getattr(r, 'status_code', 'ERR')}")
            if getattr(r, "status_code", 0) in (200, 204, 302):
                time.sleep(1)
                check = self.fetch_html(url)
                if check and message.split()[0] in check:
                    return {"ok": True, "response": "posted (normal)"}
            normal_error = f"HTTP {getattr(r, 'status_code', 'ERR')}"
        except Exception as e:
            normal_error = str(e)
        warn(f"[POST] Normal failed: {normal_error}")

        debug("[POST] Trying multipart...")
        multipart = {
            textarea_name: (None, html_msg, "text/html"),
            "message": (None, message),
            "message_html": (None, html_msg)
        }
        for k, v in payload.items():
            if k not in multipart:
                multipart[k] = (None, v if v is not None else "")

        try:
            r = self.session.post(action, files=multipart, headers=headers, timeout=25)
            debug(f"[POST] Multipart code: {getattr(r, 'status_code', 'ERR')}")
            if getattr(r, "status_code", 0) in (200, 204, 302):
                time.sleep(1)
                check = self.fetch_html(url)
                if check and message.split()[0] in check:
                    return {"ok": True, "response": "posted (multipart)"}
            multipart_error = f"HTTP {getattr(r, 'status_code', 'ERR')}"
        except Exception as e:
            multipart_error = str(e)
        warn(f"[POST] Multipart failed: {multipart_error}")

        return {
            "ok": False,
            "error": "Post failed",
            "normal_err": normal_error,
            "multipart_err": multipart_error
        }

    # -----------------------------------------------------------------
    # check cookies: returns dict with status & logged_in flag
    # -----------------------------------------------------------------
    def check_cookies(self) -> Dict:
        test_url = (FORUM_BASE.rstrip("/") + "/index.php") if FORUM_BASE else "/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }
        cookies = build_cookies()
        try:
            r = self.session.get(test_url, headers=headers, cookies=cookies, timeout=15)
            html = r.text or ""
            logged = ("logout" in html.lower()) or ("выйти" in html.lower()) or ('data-logged-in="true"' in html)
            return {
                "ok": True,
                "logged_in": bool(logged),
                "status": getattr(r, "status_code", None),
                "cookies_sent": cookies,
                "html_sample": html[:500]
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # -----------------------------------------------------------------
    # keepalive thread (pings forum periodically)
    # -----------------------------------------------------------------
    def _keepalive_loop(self):
        while self._keepalive_running:
            try:
                self.fetch_html(FORUM_BASE)
            except Exception as e:
                warn(f"keepalive error: {e}")
            time.sleep(max(60, self.interval * 3))

    # -----------------------------------------------------------------
    # debug_forum — detailed diagnostic for forum pages
    # -----------------------------------------------------------------
    def debug_forum(self, url: str) -> str:
        out_lines = []
        try:
            url = normalize_url(url)
        except Exception:
            pass

        out_lines.append(f"🔍 DEBUG FORUM\nURL: {url}\n")

        try:
            html = self.fetch_html(url)
            if not html:
                return "❌ Не удалось загрузить страницу. Проверь cookies / FORUM_BASE."
        except Exception as e:
            return f"❌ Ошибка fetch_html: {e}"

        soup = BeautifulSoup(html, "html.parser")

        selectors = [
            ".uix_stickyContainerOuter .structItem",
            ".uix_stickyContainerInner .structItem",
            ".structItemContainer-group .structItem",
            ".block-body .structItem",
            ".structItem",
            ".structItem--thread",
            ".structItem.js-threadListItem"
        ]

        out_lines.append("Селекторы и найденные количества:")
        for sel in selectors:
            try:
                nodes = soup.select(sel)
                out_lines.append(f"  {sel} -> {len(nodes)}")
            except Exception as e:
                out_lines.append(f"  {sel} -> ERR ({e})")

        try:
            all_items = soup.select(".structItem")
            out_lines.append(f"\nВсего .structItem: {len(all_items)}")
            for i, it in enumerate(all_items[:3]):
                snippet = str(it)[:1200].replace("\n", " ")
                out_lines.append(f"\n--- structItem #{i+1} ---\n{snippet}\n")
        except Exception as e:
            out_lines.append(f"\nОшибка при выводе structItem: {e}")

        try:
            parsed = parse_forum_topics(html, url)
            out_lines.append(f"\nparse_forum_topics -> найдено {len(parsed)} элементов:")
            for p in parsed[:10]:
                out_lines.append(
                    f"  tid={p.get('tid')} | {p.get('title')[:70]} | {p.get('author')} | pinned={p.get('pinned')}"
                )
        except Exception as e:
            out_lines.append(f"\nparse_forum_topics error: {e}")

        try:
            area = (
                soup.select_one(".structItemContainer-group")
                or soup.select_one(".block-body")
                or soup.select_one(".p-body")
            )
            if area:
                out_lines.append("\n--- HTML блока тем (2000 chars) ---")
                out_lines.append(str(area)[:2000].replace("\n", " "))
            else:
                out_lines.append("\nНе найден основной контейнер.")
        except Exception as e:
            out_lines.append(f"\nОшибка при выводе блока тем: {e}")

        out_lines.append("\nПодсказки:")
        out_lines.append(" • Если селекторы возвращают 0 — форум грузит темы через JS/Ajax.")
        out_lines.append(" • Если structItem есть — скинь первый structItem, я напишу точный парсер.")
        out_lines.append(" • Если parse пустой — не совпадают классы MatRP.")

        return "\n".join(out_lines)



# ======================================================================
#  stay_online_loop — helper for main.py (external use)
# ======================================================================
def stay_online_loop():
    """
    Simple loop to ping FORUM_BASE every 3 minutes to keep session alive.
    """
    cookies = build_cookies()
    url = FORUM_BASE or ""
    if not url:
        print("[ONLINE] FORUM_BASE not configured")
        return
    while True:
        try:
            requests.get(url, cookies=cookies, timeout=10)
            print("[ONLINE] ping OK")
        except Exception as e:
            print("[ONLINE ERROR]", e)
        time.sleep(180)
