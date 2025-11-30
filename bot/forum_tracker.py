# bot/forum_tracker.py
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
        log_info(f"{msg}")
    except Exception:
        print(f"[{now}] [DEBUG] {msg}")

def warn(msg: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        log_error(f"{msg}")
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
def parse_thread_posts(html: str, page_url: str):
    """
    Новый парсер постов MatRP (XenForo)
    Работает с разметкой:
    article.message-body.js-selectToQuote
        div.bbWrapper ← здесь фактический текст поста
    
    + корректно извлекает:
        • id поста
        • автора
        • дату
        • текст БЕЗ подписи
    """

    soup = BeautifulSoup(html or "", "html.parser")

    # Все посты по новому формату
    posts = soup.select("article.message-body.js-selectToQuote")
    out = []

    for msg in posts:
        try:
            # ID поста
            pid = msg.get("data-lb-id") \
               or msg.get("data-id") \
               or msg.get("data-post-id") \
               or ""

            if not pid:
                # fallback на article id="js-post-123"
                art = msg.find_parent("article")
                if art:
                    pid = extract_post_id_from_article(str(art))

            pid = str(pid)

            # Автор
            user = (
                msg.find_previous("a", class_="username")
                or msg.find_previous("h4", class_="message-name")
                or msg.find_previous("span", class_="username")
            )
            author = user.get_text(strip=True) if user else "Неизвестно"

            # Дата
            t = msg.find_previous("time")
            date = t.get("datetime") if t else (t.get_text(strip=True) if t else "")

            # Текст поста — именно ТУТ правильный путь
            body = msg.select_one("div.bbWrapper")
            if body:
                text = body.get_text("\n", strip=True)
            else:
                # fallback
                text = msg.get_text("\n", strip=True)

            # Удаляем подписи/служебные блоки
            text = re.sub(r'\n{2,}', '\n', text).strip()

            # Формируем ссылку
            link = page_url + f"#post-{pid}"

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


def parse_forum_topics(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")

    container = soup.select_one(".structItemContainer-group.js-threadList")
    if not container:
        return []

    items = container.select(".structItem.structItem--thread")

    out = []

    for it in items:
        # thread-id
        tid = it.get("data-thread-id")
        try:
            tid = int(tid)
        except:
            continue

        # Основной линк (ссылка на тему)
        a = it.select_one(".structItem-title a[href*='/threads/']")
        if not a:
            continue

        href = a.get("href", "")
        url = href if href.startswith("http") else base_url + href

        title = a.get_text(strip=True)

        # Автор темы
        author_el = it.select_one(".structItem-parts .username")
        author = author_el.get_text(strip=True) if author_el else "Unknown"

        # pinned? (определяем по prefix)
        classes = it.get("class", [])
        pinned = any(c.startswith("is-prefix") for c in classes)

        out.append({
            "tid": tid,
            "title": title,
            "author": author,
            "url": url,
            "pinned": pinned
        })

    return out




# ======================================================================
#  ForumTracker class
# ======================================================================
class ForumTracker:
    """
    ForumTracker supports:
      - ForumTracker(vk)
      - ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, vk)
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
                    # use domain None to let requests determine; some requests versions require domain - but leave as is
                    try:
                        self.session.cookies.set(k, v)
                    except Exception:
                        # fallback specifying domain
                        try:
                            self.session.cookies.set(k, v, domain=FORUM_BASE.replace("https://", "").replace("http://", "").split("/")[0])
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
    # Утилиты доступа к сети через session (чтобы все запросы шли с
    # одинаковыми cookies и заголовками)
    # -----------------------------------------------------------------
    def fetch_html(self, url: str, timeout: int = 15) -> str:
        """
        Загрузить HTML используя self.session (с куками, которые уже были
        установлены в session). Возвращает текст страницы или пустую строку.
        """
        if not url:
            return ""

        # приводим url к нормальной форме (если нужно)
        try:
            url = normalize_url(url)
        except Exception:
            pass

        debug(f"[FETCH] GET {url}")
        try:
            # используем session.get (чтобы отправлять куки и держать сессию)
            r = self.session.get(url, timeout=timeout)
            debug(f"[FETCH] {url} -> {r.status_code}")
            if r.status_code == 200:
                return r.text
            warn(f"HTTP {r.status_code} for {url}")
            return ""
        except Exception as e:
            warn(f"fetch_html error: {e}")
            return ""

    def get(self, url: str, **kwargs):
        """
        Прокси-метод для self.session.get — нужен, если где-то вызывают
        self.tracker.get(...)
        """
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

    def _process_url(self, url: str, subscribers):
        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            debug(f"[process] skipping non-forum url: {url}")
            return
        html = fetch_html(url)
        if not html:
            warn(f"failed to fetch: {url}")
            return
        typ = detect_type(url)

        # THREAD
        if typ == "thread":
            posts = parse_thread_posts(html, url)
            if not posts:
                return
            newest = posts[-1]
            for peer_id, _, last in subscribers:
                last_str = str(last) if last is not None else None
                if last_str != str(newest["id"]):
                    msg = (
                        f"📝 Новый пост\n👤 {newest['author']}  •  {newest['date']}\n\n"
                        f"{(newest['text'][:1500] + '...') if len(newest['text'])>1500 else newest['text']}\n\n🔗 {newest['link']}"
                    )
                    try:
                        self.vk.send(peer_id, msg)
                    except Exception as e:
                        warn(f"vk send error: {e}")
                    try:
                        update_last(peer_id, url, str(newest["id"]))
                    except Exception as e:
                        warn(f"update_last error: {e}")

        # FORUM (new topics)
        elif typ == "forum":
            topics = parse_forum_topics(html, url)
            if not topics:
                return
            latest = topics[-6:]
            for peer_id, _, last in subscribers:
                last_str = str(last) if last is not None else None
                for t in latest:
                    if last_str != str(t["tid"]):
                        msg = f"🆕 Новая тема\n📄 {t['title']}\n👤 {t['author']}\n🔗 {t['url']}"
                        try:
                            self.vk.send(peer_id, msg)
                        except Exception as e:
                            warn(f"vk send error: {e}")
                        try:
                            update_last(peer_id, url, str(t["tid"]))
                        except Exception as e:
                            warn(f"update_last error: {e}")

        # MEMBERS
        elif typ == "members":
            soup = BeautifulSoup(html, "html.parser")
            users = [a.get_text(strip=True) for a in soup.select(".username, .userTitle, .memberUsername a")[:20]]
            if users:
                s = "👥 Участники (часть): " + ", ".join(users)
                for peer_id, _, _ in subscribers:
                    try:
                        self.vk.send(peer_id, s)
                    except Exception:
                        pass
        else:
            debug(f"[process] unknown type for {url}: {typ}")

    # manual fetch posts — returns list (used by /checkfa)
    def manual_fetch_posts(self, url: str) -> List[Dict]:
        url = normalize_url(url)
        debug(f"[manual_fetch_posts] URL = {url}")
        debug(f"[manual_fetch_posts] Cookies = {build_cookies()}")
        if not url.startswith(FORUM_BASE):
            raise ValueError("URL outside FORUM_BASE")
        html = fetch_html(url)
        if not html:
            raise RuntimeError("Failed to fetch page (check cookies)")
        posts = parse_thread_posts(html, url)
        debug(f"[manual_fetch_posts] Parsed posts = {len(posts)}")
        return posts

    # debug what bot sees for reply form
    def debug_reply_form(self, url: str) -> str:
        url = normalize_url(url)
        html = fetch_html(url)
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

    # Improved post_message: tries normal POST then multipart fallback
    def post_message(self, url: str, message: str) -> Dict:
        """
        Robust post to XenForo thread (tries standard and multipart).
        Returns dict with ok boolean and details.
        """
        debug(f"[POST] Sending to: {url}")
        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            return {"ok": False, "error": "URL outside FORUM_BASE"}

        # brief cookie debug (don't print full tokens)
        try:
            debug(f"[POST] Cookies: xf_user={XF_USER[:6]}..., xf_session={XF_SESSION[:6]}..., xf_tfa={XF_TFA_TRUST[:6]}...")
        except Exception:
            debug("[POST] Cookies: (not available)")

        html = fetch_html(url)
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

        # collect hidden inputs
        payload: Dict[str, str] = {}
        for inp in form.select("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "") or ""

        # ensure XenForo flags
        payload["_xfWithData"] = "1"
        payload["_xfResponseType"] = "json"

        # token
        if not payload.get("_xfToken"):
            t = soup.find("input", {"name": "_xfToken"})
            if t:
                payload["_xfToken"] = t.get("value", "")

        debug(f"[POST] xfToken: {payload.get('_xfToken')}")

        # _xfRequestUri often required
        try:
            payload["_xfRequestUri"] = url.replace(FORUM_BASE, "") or "/"
        except Exception:
            payload["_xfRequestUri"] = "/"

        # find textarea
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

        # populate common fields — XenForo may expect both message and message_html
        payload[textarea_name] = html_msg
        payload["message"] = message
        payload["message_html"] = html_msg

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }

        # Try normal POST
        normal_error = None
        multipart_error = None

        debug("[POST] Trying normal mode...")
        try:
            r = self.session.post(action, data=payload, headers=headers, timeout=25)
            debug(f"[POST] Normal POST code: {getattr(r, 'status_code', 'ERR')}")
            if getattr(r, "status_code", 0) in (200, 204, 302):
                time.sleep(1)
                check = fetch_html(url)
                if check and message.split()[0] in check:
                    return {"ok": True, "response": "posted (normal)"}
            normal_error = f"HTTP {getattr(r, 'status_code', 'ERR')}"
        except Exception as e:
            normal_error = str(e)
        warn(f"[POST] Normal failed: {normal_error}")

        # Try multipart fallback
        debug("[POST] Trying multipart...")
        multipart = {
            textarea_name: (None, html_msg, "text/html"),
            "message": (None, message),
            "message_html": (None, html_msg)
        }
        # include other hidden fields as simple form parts
        for k, v in payload.items():
            if k not in multipart:
                multipart[k] = (None, v if v is not None else "")

        try:
            r = self.session.post(action, files=multipart, headers=headers, timeout=25)
            debug(f"[POST] Multipart code: {getattr(r, 'status_code', 'ERR')}")
            if getattr(r, "status_code", 0) in (200, 204, 302):
                time.sleep(1)
                check = fetch_html(url)
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

    # check cookies: returns dict with status & logged_in flag
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

    # keepalive thread (pings forum periodically)
    def _keepalive_loop(self):
        while self._keepalive_running:
            try:
                fetch_html(FORUM_BASE)
            except Exception as e:
                warn(f"keepalive error: {e}")
            time.sleep(max(60, self.interval * 3))


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
