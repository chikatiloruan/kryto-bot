# bot/forum_tracker.py
import threading
import time
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from urllib.parse import urljoin
from .utils import (
    normalize_url, detect_type,
    extract_thread_id, extract_post_id_from_article
)
from .storage import list_all_tracks, update_last
import traceback
import datetime

# ======================================================================
#   ГЛАВНЫЙ ФИКС:
#   - Правильные cookies
#   - domain=".matrp.ru"
#   - нормальный User-Agent
#   - debug логирование
#   - фиксы постинга message_html
# ======================================================================

try:
    from config import XF_USER, XF_SESSION, XF_TFA_TRUST, FORUM_BASE, POLL_INTERVAL_SEC
except Exception:
    XF_USER = ""
    XF_SESSION = ""
    XF_TFA_TRUST = ""
    FORUM_BASE = ""
    POLL_INTERVAL_SEC = 20

DEFAULT_POLL = 20
try:
    POLL = int(POLL_INTERVAL_SEC)
    if POLL <= 0:
        POLL = DEFAULT_POLL
except Exception:
    POLL = DEFAULT_POLL


def debug(msg: str):
    """ Красивый timestamp debug """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [DEBUG] {msg}")


def warn(msg: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [WARNING] {msg}")


# ======================================================================
# COOKIE
# ======================================================================
def build_cookies() -> dict:
    """ Возвращает cookies как словарь для requests """
    return {
        "xf_user": XF_USER,
        "xf_session": XF_SESSION,
        "xf_tfa_trust": XF_TFA_TRUST,
    }


def fetch_html(url: str, timeout: int = 15) -> str:
    """
    Загружаем HTML с правильными cookie + UA
    """
    if not url:
        return ""

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": FORUM_BASE or "",
    }

    try:
        r = requests.get(url, headers=headers, cookies=build_cookies(), timeout=timeout)
        if r.status_code == 200:
            return r.text

        warn(f"HTTP {r.status_code} for {url}")
        return ""

    except Exception as e:
        warn(f"fetch_html error: {e}")
        return ""
# ======================================================================
#  ПАРСЕРЫ — темы, посты, разделы
# ======================================================================

def parse_thread_posts(html: str, page_url: str) -> List[Dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    nodes = soup.select("article.message, article.message--post, .message, .message-body")

    if not nodes:
        nodes = soup.select(".post, .messageRow, .message-row")

    out = []
    for n in nodes:
        try:
            raw = str(n)
            pid = extract_post_id_from_article(raw) or extract_thread_id(page_url) or ""

            author_el = n.select_one(
                ".message-name a, .username a, .username, "
                ".message-userCard a, .message-author, .message-attribution a"
            )
            author = author_el.get_text(strip=True) if author_el else "Неизвестно"

            t = n.select_one("time")
            date = t.get("datetime") if t else "Неизвестно"

            body = n.select_one(".bbWrapper, .message-body, .message-content, .postMessage")
            text = body.get_text("\n", strip=True) if body else ""

            link = page_url + (f"#post-{pid}" if pid else "")

            out.append({
                "id": str(pid),
                "author": author,
                "date": date,
                "text": text,
                "link": link
            })
        except Exception as e:
            warn(f"parse_thread_posts error: {e}")
            continue
    return out


def parse_forum_topics(html: str, page_url: str) -> List[Dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    items = soup.select(".structItem--thread, .structItem, .discussionListItem, .threadbit")

    out = []
    for it in items:
        try:
            a = it.select_one(
                ".structItem-title a, a[href*='/threads/'], "
                "a[href*='index.php?threads='], a.thread-title"
            )
            if not a:
                continue

            href = a.get("href")
            full = href if href.startswith("http") else urljoin(FORUM_BASE + "/", href.lstrip("/"))

            tid = extract_thread_id(full)
            title = a.get_text(strip=True)

            author_el = it.select_one(".structItem-minor a, .username, .poster")
            author = author_el.get_text(strip=True) if author_el else "Неизвестно"

            out.append({
                "tid": str(tid),
                "title": title,
                "author": author,
                "url": full
            })
        except Exception as e:
            warn(f"parse_forum_topics error: {e}")
            continue

    return out
# ======================================================================
#  КЛАСС ForumTracker — мониторинг, keepalive, обработка новых постов
# ======================================================================

class ForumTracker:
    """
    Поддерживает 2 варианта инициализации:
      - ForumTracker(vk)
      - ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, vk)
    """

    def __init__(self, *args):
        self.interval = POLL
        self._running = False
        self.vk = None
        self._keepalive_running = True

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
            "Referer": FORUM_BASE
        })

        # -------------------------
        # СХЕМА 1 ARG — ForumTracker(vk)
        # -------------------------
        if len(args) == 1:
            self.vk = args[0]

            # куки берём из config.py
            for k, v in build_cookies().items():
                if v:
                    self.session.cookies.set(k, v)

        # -------------------------
        # СХЕМА 4 ARGS — старый вид ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, vk)
        # -------------------------
        elif len(args) >= 4:
            xf_user, xf_tfa_trust, xf_session, vk = args[:4]
            self.vk = vk

            # сохраняем в глобалы
            globals()["XF_USER"] = xf_user
            globals()["XF_TFA_TRUST"] = xf_tfa_trust
            globals()["XF_SESSION"] = xf_session

            # ставим куки в session
            if xf_user:
                self.session.cookies.set("xf_user", xf_user, domain="forum.matrp.ru")
            if xf_tfa_trust:
                self.session.cookies.set("xf_tfa_trust", xf_tfa_trust, domain="forum.matrp.ru")
            if xf_session:
                self.session.cookies.set("xf_session", xf_session, domain="forum.matrp.ru")

        else:
            raise TypeError("ForumTracker expected (vk) or (XF_USER, XF_TFA_TRUST, XF_SESSION, vk)")

        # триггер для /check
        if hasattr(self.vk, "set_trigger"):
            try:
                self.vk.set_trigger(self.force_check)
            except:
                pass

        # запуск keepalive
        threading.Thread(target=self._keepalive_loop, daemon=True).start()

    # ===================================================================
    # API управления
    # ===================================================================

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log(f"ForumTracker started (interval={self.interval})")

    def stop(self):
        self._running = False
        self._keepalive_running = False
        log("ForumTracker stopped")

    def force_check(self):
        threading.Thread(target=self.check_all, daemon=True).start()

    # ===================================================================
    # Основной цикл
    # ===================================================================
    def _loop(self):
        while self._running:
            try:
                self.check_all()
            except Exception as e:
                warn(f"Tracker loop error: {e}")
            time.sleep(self.interval)

    # ===================================================================
    # Проверка всех подписок
    # ===================================================================
    def check_all(self):
        rows = list_all_tracks()
        if not rows:
            return

        # группируем по URL
        by_url = {}
        for peer_id, url, typ, last_id in rows:
            by_url.setdefault(url, []).append((peer_id, typ, last_id))

        for url, subs in by_url.items():
            try:
                self._process_url(url, subs)
            except Exception as e:
                warn(f"_process_url error for {url}: {e}")
                traceback.print_exc()

    # ===================================================================
    # Логика обработки одной ссылки
    # ===================================================================
    def _process_url(self, url: str, subs):
        url = normalize_url(url)
        html = fetch_html(url)

        if not html:
            warn(f"HTTP error / no HTML for {url}")
            return

        typ = detect_type(url)

        # ===================================================================
    #  Ручная загрузка всех постов (используется /checkfa)
    # ===================================================================
    def manual_fetch_posts(self, url: str):
        url = normalize_url(url)

        if not url.startswith(FORUM_BASE):
            return {"ok": False, "error": "URL outside FORUM_BASE"}

        html = fetch_html(url)
        if not html:
            return {"ok": False, "error": "Cannot fetch page"}

        try:
            posts = parse_thread_posts(html, url)
            return {"ok": True, "posts": posts}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ===================================================================
    # KEEPALIVE — пинг форума раз в N секунд (держит сессию активной)
    # ===================================================================
    def _keepalive_loop(self):
        while self._keepalive_running:
            try:
                fetch_html(FORUM_BASE)
            except Exception as e:
                warn(f"keepalive error: {e}")
            time.sleep(max(60, self.interval * 3))

        # ---------------------------------------------------------------
        #     THREAD
        # ---------------------------------------------------------------
        if typ == "thread":
            posts = parse_thread_posts(html, url)
            if not posts:
                return

            newest = posts[-1]

            for peer_id, _, last in subs:
                last_str = str(last) if last is not None else None

                if last_str != newest["id"]:
                    # уведомление
                    msg = (
                        "📝 Новый пост\n"
                        f"👤 {newest['author']} • {newest['date']}\n\n"
                        f"{newest['text'][:1500]}\n\n"
                        f"🔗 {newest['link']}"
                    )

                    try:
                        self.vk.send(peer_id, msg)
                    except Exception as e:
                        warn(f"VK send error: {e}")

                    update_last(peer_id, url, newest["id"])

        # ---------------------------------------------------------------
        #     FORUM (новые темы)
        # ---------------------------------------------------------------
        elif typ == "forum":
            topics = parse_forum_topics(html, url)
            if not topics:
                return

            latest = topics[-6:]

            for peer_id, _, last in subs:
                last_str = str(last) if last is not None else None

                for t in latest:
                    if last_str != t["tid"]:
                        msg = (
                            "🆕 Новая тема\n"
                            f"📄 {t['title']}\n"
                            f"👤 {t['author']}\n"
                            f"🔗 {t['url']}"
                        )
                        try:
                            self.vk.send(peer_id, msg)
                        except:
                            pass

                        update_last(peer_id, url, t["tid"])

        # ---------------------------------------------------------------
        #     MEMBERS
        # ---------------------------------------------------------------
        elif typ == "members":
            soup = BeautifulSoup(html, "html.parser")
            users = [a.get_text(strip=True) for a in soup.select(".username")[:20]]
            s = "👥 Пользователи: " + ", ".join(users)

            for peer_id, _, _ in subs:
                try:
                    self.vk.send(peer_id, s)
                except:
                    pass

        # ===================================================================
    # DEBUG: показать, что бот видит на странице
    # ===================================================================
    def debug_reply_form(self, url: str):
        url = normalize_url(url)
        html = fetch_html(url)

        if not html:
            return "❌ Не удалось загрузить страницу"

        soup = BeautifulSoup(html, "html.parser")

        form = (
            soup.select_one("form[action*='add-reply']")
            or soup.select_one("form.js-quickReply")
            or soup.select_one("form[data-xf-init*='quick-reply']")
            or soup.select_one("form[action*='post']")
        )

        textarea = None
        if form:
            textarea = form.select_one("textarea") or form.select_one("textarea[name]")

        # проверяем залогиненность
        logged = "LogOut" in html or "Выйти" in html or "account" in html

        # собираем инфу
        return (
            "🔍 DEBUG REPLY FORM\n"
            f"✔ Logged in: {logged}\n"
            f"✔ Form found: {bool(form)}\n"
            f"✔ Textarea found: {bool(textarea)}\n"
            f"✔ Textarea name: {textarea.get('name') if textarea else '—'}\n"
            f"✔ Action: {form.get('action') if form else '—'}\n"
            "-----------------------------------\n"
            "HTML снизу страницы:\n"
            + html[-2000:]
        )

    # ===================================================================
    #  ОТПРАВКА СООБЩЕНИЙ В ТЕМУ (полностью исправлено)
    # ===================================================================
    def post_message(self, url: str, message: str):
        """
        Универсальная, устойчивая отправка ответа в XenForo.
        Работает на MatRP, учитывает защиту, токены, multipart.
        """

        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            return {"ok": False, "error": "URL outside FORUM_BASE"}

        html = fetch_html(url)
        if not html:
            return {"ok": False, "error": "Cannot fetch page"}

        soup = BeautifulSoup(html, "html.parser")

        # ---------------------------------------------------------------
        # 1) Ищем форму ответа
        # ---------------------------------------------------------------
        form = (
            soup.select_one("form[action*='add-reply']")
            or soup.select_one("form.js-quickReply")
            or soup.select_one("form[data-xf-init*='quick-reply']")
            or soup.select_one("form[action*='post']")
        )

        if not form:
            return {"ok": False, "error": "Reply form not found"}

        action = form.get("action") or url
        if not action.startswith("http"):
            action = urljoin(FORUM_BASE, action.lstrip("/"))

        # ---------------------------------------------------------------
        # 2) Собираем hidden-поля
        # ---------------------------------------------------------------
        payload = {}
        for inp in form.select("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        # токен XenForo
        token = (
            soup.find("input", {"name": "_xfToken"}) 
            or soup.find("input", {"name": "csrf"})
            or None
        )
        if token:
            payload["_xfToken"] = token.get("value", "")

        # ---------------------------------------------------------------
        # 3) Определяем правильное поле message
        # ---------------------------------------------------------------
        textarea = (
            form.select_one("textarea[name='message_html']")
            or form.select_one("textarea[name='message']")
            or form.select_one("textarea[data-original-name='message']")
        )

        if not textarea:
            return {"ok": False, "error": "Textarea not found"}

        textarea_name = textarea.get("name")

        # HTML-контент
        payload[textarea_name] = f"<p>{message}</p>"

        # Материализация XF-запроса
        payload["_xfWithData"] = "1"
        payload["_xfResponseType"] = "json"

        # ---------------------------------------------------------------
        # 4) Заголовки + cookies
        # ---------------------------------------------------------------
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }

        cookies = build_cookies()

        # ---------------------------------------------------------------
        # 5) Пытаемся отправить NORMAL POST
        # ---------------------------------------------------------------
        try:
            r = self.session.post(action, data=payload, headers=headers)
            if r.status_code in (200, 204, 302):
                # проверяем по HTML
                time.sleep(1)
                check = fetch_html(url)
                if message.split()[0] in check:
                    return {"ok": True, "response": "posted (normal mode)"}
            last_normal_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_normal_err = str(e)

        warn(f"Normal POST failed: {last_normal_err}")

        # ---------------------------------------------------------------
        # 6) MULTIPART попытка
        # ---------------------------------------------------------------
        multipart = {textarea_name: (None, f"<p>{message}</p>", "text/html")}

        # добавляем hidden
        for k, v in payload.items():
            if k != textarea_name:
                multipart[k] = (None, v)

        try:
            r = self.session.post(action, files=multipart, headers=headers)
            if r.status_code in (200, 204, 302):
                time.sleep(1)
                check = fetch_html(url)
                if message.split()[0] in check:
                    return {"ok": True, "response": "posted (multipart mode)"}
            last_multi_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_multi_err = str(e)

        warn(f"Multipart failed: {last_multi_err}")

        # ---------------------------------------------------------------
        # 7) Полный фейл
        # ---------------------------------------------------------------
        return {
            "ok": False,
            "error": "Post failed",
            "normal_err": last_normal_err,
            "multipart_err": last_multi_err
        }
# ======================================================================
#  ОСТАВАТЬСЯ ОНЛАЙН (ФУНКЦИЯ ДЛЯ main.py)
# ======================================================================

def stay_online_loop():
    """
    Каждые 3 минуты пингуем форум, чтобы аккаунт был 'Онлайн'.
    """
    import requests
    from .forum_tracker import build_cookies, FORUM_BASE
    import time

    cookies = build_cookies()
    url = FORUM_BASE or ""

    if not url:
        print("[ONLINE] FORUM_BASE пустой — keepalive выключен")
        return

    while True:
        try:
            requests.get(url, cookies=cookies, timeout=10)
            print("[ONLINE] ping OK")
        except Exception as e:
            print("[ONLINE ERROR]", e)
        time.sleep(180)
