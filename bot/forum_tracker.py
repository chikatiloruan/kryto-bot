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

# Конфигурация: при импорте берётся из config.py (значения XF_USER, XF_SESSION, XF_TFA_TRUST, FORUM_BASE, POLL_INTERVAL_SEC)
try:
    from config import XF_USER, XF_SESSION, XF_TFA_TRUST, FORUM_BASE, POLL_INTERVAL_SEC
except Exception:
    # если config.py не готов — подставляем заглушки, чтобы модуль импортировался
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


def build_cookies() -> dict:
    """
    Возвращает словарь cookies для requests, исходя из модульных переменных.
    В старой системе эти переменные могут быть подменены через globals() в __init__.
    """
    return {
        "xf_user": globals().get("XF_USER", XF_USER) or "",
        "xf_session": globals().get("XF_SESSION", XF_SESSION) or "",
        "xf_tfa_trust": globals().get("XF_TFA_TRUST", XF_TFA_TRUST) or "",
    }


def build_cookie_header() -> str:
    c = build_cookies()
    return "; ".join([f"{k}={v}" for k, v in c.items() if v])


def fetch_html(url: str, timeout: int = 15) -> str:
    """
    GET страницу с установленными cookie и базовыми заголовками.
    Возвращает текст страницы или пустую строку при ошибке.
    """
    if not url:
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": FORUM_BASE or ""
    }
    cookies = build_cookies()
    try:
        r = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
        if r.status_code == 200:
            return r.text
        print(f"[forum_tracker] HTTP {r.status_code} for {url}")
        return ""
    except Exception as e:
        print(f"[forum_tracker] fetch error for {url}: {e}")
        return ""


def parse_thread_posts(html: str, page_url: str) -> List[Dict]:
    """
    Парсит страницу темы, возвращает список сообщений (словарей).
    Поля: id, author, date, text, link
    """
    soup = BeautifulSoup(html or "", "html.parser")
    # Try xenForo and common selectors
    nodes = soup.select("article.message, article.message--post, .message, .message--post, .message-body")
    if not nodes:
        nodes = soup.select(".post, .postMessage, .messageRow, .message-row")
    out: List[Dict] = []
    for n in nodes:
        try:
            # try to extract id from node HTML or fall back to thread id
            raw = str(n)
            pid = extract_post_id_from_article(raw) or extract_thread_id(page_url) or ""
            # author
            author_el = n.select_one(".message-name a, .username a, .username, .message-userCard a, .message-author, .message-attribution a")
            author = author_el.get_text(strip=True) if author_el else "Неизвестно"
            # date/time
            time_el = n.select_one("time")
            date = ""
            if time_el:
                date = time_el.get("datetime") or time_el.get_text(strip=True) or ""
            else:
                date = n.select_one(".date, .Message-time, .message-time")
                date = date.get_text(strip=True) if date else "Неизвестно"
            # body / текст
            body_el = n.select_one(".bbWrapper, .message-body, .message-content, .postMessage, .uix_post_message")
            text = body_el.get_text("\n", strip=True) if body_el else ""
            link = page_url + (f"#post-{pid}" if pid else "")
            out.append({"id": str(pid or ""), "author": author, "date": date, "text": text, "link": link})
        except Exception as e:
            print("[forum_tracker] parse_thread_posts item error:", e)
            traceback.print_exc()
            continue
    return out


def parse_forum_topics(html: str, page_url: str) -> List[Dict]:
    """
    Парсит страницу раздела форума, возвращает список тем.
    Поля: tid, title, author, url
    """
    soup = BeautifulSoup(html or "", "html.parser")
    items = soup.select(".structItem--thread, .structItem, .discussionListItem, .structItem-title, .threadbit")
    out: List[Dict] = []
    for it in items:
        try:
            a = it.select_one(".structItem-title a, a[href*='/threads/'], a[href*='index.php?threads='], a.thread-title, a.topic-title")
            if not a:
                a = it.select_one("a")
                if not a:
                    continue
            href = a.get("href") or ""
            full = href if href.startswith("http") else urljoin((FORUM_BASE.rstrip("/") + "/"), href.lstrip("/"))
            tid = extract_thread_id(full) or ""
            title = a.get_text(strip=True)
            author_node = it.select_one(".structItem-minor a, .username, .structItem-lastPoster a, .lastPoster, .poster")
            author = author_node.get_text(strip=True) if author_node else "Неизвестно"
            out.append({"tid": str(tid or ""), "title": title, "author": author, "url": full})
        except Exception as e:
            print("[forum_tracker] parse_forum_topics item error:", e)
            traceback.print_exc()
            continue
    return out


class ForumTracker:
    """
    ForumTracker поддерживает два варианта инициализации:
      - ForumTracker(vk)
      - ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, vk)  (старый вызов из main.py)
    """
    def __init__(self, *args):
        # базовые поля
        self.interval = POLL
        self._running = False
        self._worker: Optional[threading.Thread] = None
        self._keepalive_running = True
        self._keepalive_thread: Optional[threading.Thread] = None

        # поддержка двух сигнатур
        if len(args) == 1:
            # ForumTracker(vk)
            self.vk = args[0]
        elif len(args) >= 4:
            # ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, vk)
            xf_user, xf_tfa_trust, xf_session, vk = args[:4]
            # записываем в глобальные переменные модуля, чтобы build_cookies() увидел их
            globals()["XF_USER"] = xf_user
            globals()["XF_TFA_TRUST"] = xf_tfa_trust
            globals()["XF_SESSION"] = xf_session
            self.vk = vk
        else:
            raise TypeError("ForumTracker expected (vk) or (XF_USER, XF_TFA_TRUST, XF_SESSION, vk)")

        # регистрируем триггер проверки
        try:
            if self.vk:
                self.vk.set_trigger(self.force_check)
        except Exception:
            pass

        # старт keepalive-потока (поддержание сессии)
        try:
            self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._keepalive_thread.start()
        except Exception as e:
            print("[forum_tracker] failed to start keepalive thread:", e)

    # --- API управления ---
    def start(self):
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()
        print("[forum_tracker] started, poll interval:", self.interval)

    def stop(self):
        self._running = False
        self._keepalive_running = False
        print("[forum_tracker] stopped")

    def force_check(self):
        # запустить проверку в фоновом потоке
        threading.Thread(target=self.check_all, daemon=True).start()

    # внутренний цикл
    def _loop(self):
        while self._running:
            try:
                self.check_all()
            except Exception as e:
                print("[forum_tracker] loop error:", e)
            time.sleep(self.interval)

    def check_all(self):
        rows = list_all_tracks()
        if not rows:
            return
        # сгруппировать подписки по url
        by_url = {}
        for peer_id, url, typ, last_id in rows:
            by_url.setdefault(url, []).append((peer_id, typ, last_id))
        for url, subs in by_url.items():
            try:
                self._process_url(url, subs)
            except Exception as e:
                print("[forum_tracker] _process_url error for", url, e)
                traceback.print_exc()

    def _process_url(self, url: str, subscribers):
        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            print("[forum_tracker] skipping non-forum url:", url)
            return
        html = fetch_html(url)
        if not html:
            print("[forum_tracker] failed to fetch:", url)
            return
        typ = detect_type(url)
        # THREAD: watch posts
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
                        print("[forum_tracker] vk send error:", e)
                    try:
                        update_last(peer_id, url, str(newest["id"]))
                    except Exception as e:
                        print("[forum_tracker] update_last error:", e)
        # FORUM: watch new topics in section
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
                            print("[forum_tracker] vk send error:", e)
                        try:
                            update_last(peer_id, url, str(t["tid"]))
                        except Exception as e:
                            print("[forum_tracker] update_last error:", e)
        # MEMBERS: snapshot
        elif typ == "members":
            soup = BeautifulSoup(html, "html.parser")
            users = [a.get_text(strip=True) for a in soup.select(".username, .userTitle, .memberUsername a")[:20]]
            if users:
                s = "👥 Участники (часть): " + ", ".join(users)
                for peer_id, _, _ in subscribers:
                    try:
                        self.vk.send(peer_id, s)
                    except Exception as e:
                        print("[forum_tracker] vk send error:", e)
        else:
            print("[forum_tracker] unknown type:", url)

    def _keepalive_loop(self):
        """
        Периодически пингуем FORUM_BASE, чтобы поддерживать сессию/куки живыми.
        """
        while self._keepalive_running:
            try:
                _ = fetch_html(FORUM_BASE)
            except Exception as e:
                print("[forum_tracker] keepalive error:", e)
            time.sleep(max(60, self.interval * 3))

    # ---------- manual helpers ----------
    def manual_fetch_posts(self, url: str) -> List[Dict]:
        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            raise ValueError("URL must start with FORUM_BASE")
        html = fetch_html(url)
        if not html:
            raise RuntimeError("Failed to fetch page (check cookies)")
        return parse_thread_posts(html, url)

    def fetch_latest_post_id(self, url: str) -> Optional[str]:
        url = normalize_url(url)
        html = fetch_html(url)
        if not html:
            return None
        typ = detect_type(url)
        if typ == "thread":
            posts = parse_thread_posts(html, url)
            if posts:
                return str(posts[-1]["id"])
        elif typ == "forum":
            topics = parse_forum_topics(html, url)
            if topics:
                return str(topics[-1]["tid"])
        return None

    # ---------- posting (reply) ----------
    def post_message(self, url: str, message: str):
    """
    Полностью рабочая отправка ответа в XenForo.
    Ищет форму, парсит hidden inputs, использует message_html,
    формирует правильные данные и отправляет POST.
    """

    url = normalize_url(url)
    if not url.startswith(FORUM_BASE):
        return {"ok": False, "error": "URL not on forum base"}

    html = fetch_html(url)
    if not html:
        return {"ok": False, "error": "Cannot fetch topic page"}

    soup = BeautifulSoup(html, "html.parser")

    # 1. Находим форму ответа
    form = (
        soup.select_one("form[action*='add-reply']")
        or soup.select_one("form[action*='post']")
        or soup.select_one("form.js-quickReply")
        or soup.select_one("form[data-xf-init*='quick-reply']")
        or soup.select_one("form")
    )

    if not form:
        return {"ok": False, "error": "Reply form not found"}

    # 2. Вычисляем полный URL для POST
    action = form.get("action") or url
    if not action.startswith("http"):
        action = urljoin(FORUM_BASE, action.lstrip("/"))

    # 3. Собираем HIDDEN поля
    payload = {}
    for inp in form.select("input"):
        name = inp.get("name")
        value = inp.get("value", "")
        if name:
            payload[name] = value

    # 4. Проверяем ключевое поле — textarea name="message_html"
    textarea = (
        form.select_one("textarea[name='message_html']") or
        form.select_one("textarea[data-original-name='message']")
    )

    if not textarea:
        return {"ok": False, "error": "message_html textarea not found"}

    # 5. Формируем HTML-содержимое
    payload["message_html"] = f"<p>{message}</p>"

    # 6. На всякий случай отключаем предпросмотр
    payload["_xfWithData"] = "1"
    payload["_xfResponseType"] = "json"

    # 7. Отправляем POST
    r = self.session.post(action, data=payload)

    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}", "response": r.text}

    # 8. Быстрая проверка — ответ XenForo должен содержать success:true
    if "success" in r.text or "message" in r.text:
        return {"ok": True, "response": r.text}

    return {"ok": False, "error": "Unexpected reply", "response": r.text}

        def build_payload(candidate_tname):
            payload = {}
            # inputs
            for inp in form.select("input"):
                name = inp.get("name")
                if not name:
                    continue
                payload[name] = inp.get("value", "")
            # include other textareas by name
            for ta in form.select("textarea"):
                name = ta.get("name")
                if name and name not in payload:
                    payload[name] = ta.get_text() or ""
            payload[candidate_tname] = message
            return payload

        # candidates textarea names
        textarea = form.select_one("textarea")
        candidates = []
        if textarea and textarea.get("name"):
            candidates.append(textarea.get("name"))
        candidates += ["message", "message_html", "message_text", "message_body", "message_plain"]

        headers_base = {
            "User-Agent": "Mozilla/5.0 (compatible; ForumPoster/1.0)",
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01"
        }

        cookies = build_cookies()
        last_err = None

        for cname in candidates:
            payload = build_payload(cname)
            # include common XF token if present
            t = soup.find("input", {"name": "_xfToken"}) or soup.find("input", {"name": "_xfToken_"})
            if t:
                payload.setdefault("_xfToken", t.get("value") or "")
            meta_csrf = soup.find("meta", {"name": "csrf-token"})
            headers = dict(headers_base)
            if meta_csrf and meta_csrf.get("content"):
                headers["X-CSRF-Token"] = meta_csrf.get("content")

            try:
                r = requests.post(action, data=payload, headers=headers, cookies=cookies, timeout=25, allow_redirects=True)
                text = r.text or ""
                if r.status_code in (200, 302):
                    # server side error detection
                    if "error" in text.lower() and r.status_code != 302:
                        last_err = f"Server returned error for field {cname}"
                        continue
                    # quick verification: fetch thread and search snippet
                    try:
                        time.sleep(2)
                        new_html = fetch_html(url)
                        if new_html and message.split():
                            snippet = " ".join(message.split()[:6])
                            if snippet and snippet in new_html:
                                return {"ok": True, "response": text[:2000]}
                            else:
                                last_err = f"Posted but not visible (field {cname})"
                                continue
                        else:
                            # can't verify but server responded ok
                            return {"ok": True, "response": text[:2000]}
                    except Exception:
                        return {"ok": True, "response": text[:2000]}
                else:
                    last_err = f"HTTP {r.status_code} for field {cname}"
                    continue
            except Exception as e:
                last_err = f"Post error ({cname}): {e}"
                continue

        return {"ok": False, "error": last_err or "Unknown posting error", "response": ""}


# --- helper: отдельный поток «вечного онлайна» (ping с куками, чтобы аккаунт был online) ---
def stay_online_loop():
    """
    Запускает простой GET на FORUM_BASE с cookie каждые N секунд.
    Этот поток можно запускать в main.py отдельно:
      threading.Thread(target=stay_online_loop, daemon=True).start()
    """
    cookies = build_cookies()
    url = FORUM_BASE or ""
    if not url:
        print("[forum_tracker] stay_online_loop: FORUM_BASE not configured")
        return

    while True:
        try:
            requests.get(url, cookies=cookies, timeout=10)
            print("[ONLINE] Пинг отправлен, аккаунт активен")
        except Exception as e:
            print("[ONLINE ERROR]", e)
        time.sleep(180)
