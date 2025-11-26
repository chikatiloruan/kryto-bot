# bot/forum_tracker.py
import threading
import time
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse
from .utils import (
    normalize_url, detect_type,
    extract_thread_id, extract_post_id_from_article
)
from .storage import list_all_tracks, update_last
from config import XF_USER, XF_SESSION, XF_TFA_TRUST, FORUM_BASE, POLL_INTERVAL_SEC

DEFAULT_POLL = 20
try:
    POLL = int(POLL_INTERVAL_SEC)
    if POLL <= 0:
        POLL = DEFAULT_POLL
except Exception:
    POLL = DEFAULT_POLL

def build_cookies() -> dict:
    # return cookie dict for requests
    return {
        "xf_user": XF_USER or "",
        "xf_session": XF_SESSION or "",
        "xf_tfa_trust": XF_TFA_TRUST or "",
    }

def build_cookie_header() -> str:
    c = build_cookies()
    return "; ".join([f"{k}={v}" for k, v in c.items() if v])

def fetch_html(url: str, timeout: int = 15) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": FORUM_BASE
    }
    cookies = build_cookies()
    try:
        r = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
        if r.status_code == 200:
            return r.text
        # print minimal log
        print(f"[forum_tracker] HTTP {r.status_code} for {url}")
        return ""
    except Exception as e:
        print(f"[forum_tracker] fetch error for {url}: {e}")
        return ""

def parse_thread_posts(html: str, page_url: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    # Try xenforo article/message selectors
    nodes = soup.select("article.message, article.message--post, .message, .message--post, .message-body")
    if not nodes:
        nodes = soup.select(".post, .postMessage")
    out = []
    for n in nodes:
        # extract id from article attributes or fallback to page id
        pid = extract_post_id_from_article(n) or extract_thread_id(page_url)
        author_el = n.select_one(".message-name a, .username, .message-userCard a, .message-author, .message-attribution a")
        author = author_el.get_text(strip=True) if author_el else "Неизвестно"
        time_el = n.select_one("time")
        date = time_el.get("datetime") if time_el and time_el.get("datetime") else (time_el.get_text(strip=True) if time_el else "Неизвестно")
        body_el = n.select_one(".bbWrapper, .message-body, .message-content, .postMessage")
        text = body_el.get_text("\n", strip=True) if body_el else ""
        link = page_url + (f"#post-{pid}" if pid else "")
        out.append({"id": str(pid or ""), "author": author, "date": date, "text": text, "link": link})
    return out

def parse_forum_topics(html: str, page_url: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".structItem--thread, .structItem, .discussionListItem, .structItem-title")
    out = []
    for it in items:
        # find first anchor to thread
        a = it.select_one(".structItem-title a, a[href*='/threads/'], a[href*='index.php?threads=']")
        if not a:
            # try generic link
            a = it.select_one("a")
            if not a:
                continue
        href = a.get("href") or ""
        full = href if href.startswith("http") else urljoin(FORUM_BASE.rstrip("/") + "/", href.lstrip("/"))
        tid = extract_thread_id(full)
        title = a.get_text(strip=True)
        author_node = it.select_one(".structItem-minor a, .username, .structItem-lastPoster a")
        author = author_node.get_text(strip=True) if author_node else "Неизвестно"
        out.append({"tid": str(tid or ""), "title": title, "author": author, "url": full})
    return out

class ForumTracker:
    def __init__(self, vk):
        self.vk = vk
        self.interval = POLL
        self._running = False
        self._worker = None
        # trigger from vk bot (/check)
        self.vk.set_trigger(self.force_check)
        # keepalive thread to ping FORUM_BASE to keep session alive
        self._keepalive_running = True
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

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
        # run one check in background
        threading.Thread(target=self.check_all, daemon=True).start()

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
        # group by url
        by_url = {}
        for peer_id, url, typ, last_id in rows:
            by_url.setdefault(url, []).append((peer_id, typ, last_id))
        for url, subs in by_url.items():
            self._process_url(url, subs)

    def _process_url(self, url: str, subscribers):
        url = normalize_url(url)
        # ensure it's our forum
        if not url.startswith(FORUM_BASE):
            # skip external
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
                    # send one notification with author/date/text snippet
                    msg = (f"📝 Новый пост\n👤 {newest['author']}  •  {newest['date']}\n\n"
                           f"{(newest['text'][:1500] + '...') if len(newest['text'])>1500 else newest['text']}\n\n🔗 {newest['link']}")
                    try:
                        self.vk.send(peer_id, msg)
                    except Exception as e:
                        print("[forum_tracker] vk send error:", e)
                    update_last(peer_id, url, str(newest["id"]))
        # FORUM: watch new topics in section
        elif typ == "forum":
            topics = parse_forum_topics(html, url)
            if not topics:
                return
            # assume topics in page order oldest->newest; take several newest
            latest = topics[-6:]
            for peer_id, _, last in subscribers:
                last_str = str(last) if last is not None else None
                # send only topics that are newer than last (we can't compare order reliably by id alone)
                for t in latest:
                    if last_str != str(t["tid"]):
                        msg = f"🆕 Новая тема\n📄 {t['title']}\n👤 {t['author']}\n🔗 {t['url']}"
                        try:
                            self.vk.send(peer_id, msg)
                        except Exception as e:
                            print("[forum_tracker] vk send error:", e)
                        update_last(peer_id, url, str(t["tid"]))
        # MEMBERS: just show snapshot of list
        elif typ == "members":
            soup = BeautifulSoup(html, "html.parser")
            users = [a.get_text(strip=True) for a in soup.select(".username, .userTitle, .memberUsername a")[:20]]
            if users:
                s = "👥 Участники (часть): " + ", ".join(users)
                for peer_id, _, _ in subscribers:
                    self.vk.send(peer_id, s)
        else:
            print("[forum_tracker] unknown type:", url)

    def _keepalive_loop(self):
        # call base page periodically to keep session/cookies active
        while self._keepalive_running:
            try:
                _ = fetch_html(FORUM_BASE)
            except Exception as e:
                print("[forum_tracker] keepalive error:", e)
            # sleep longer to avoid too many requests
            time.sleep(max(60, self.interval * 3))

    # ---------- manual helper used by command handler ----------
    def manual_fetch_posts(self, url: str) -> List[Dict]:
        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            raise ValueError("URL must start with FORUM_BASE")
        html = fetch_html(url)
        if not html:
            raise RuntimeError("Failed to fetch page (check cookies)")
        return parse_thread_posts(html, url)

    def fetch_latest_post_id(self, url: str) -> Optional[str]:
        # return id of newest post in thread or newest topic id in forum
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
    def post_message(self, url: str, message: str) -> Dict:
        """
        Try to post `message` to the thread at `url`.
        Strategy:
         - GET thread page, find reply form (<form ...>)
         - collect hidden inputs and tokens, find message textarea name (common names: message, message_html, message_text)
         - POST to form action with cookies and headers
        Returns dict: {"ok": True/False, "error": "...", "response": resp_text (short)}
        """
        url = normalize_url(url)
        if not url.startswith(FORUM_BASE):
            return {"ok": False, "error": "URL not on forum base"}
        html = fetch_html(url)
        if not html:
            return {"ok": False, "error": "Cannot fetch page (cookies?)"}
        soup = BeautifulSoup(html, "html.parser")
        # try to find reply form
        form = None
        # common: form with class message-form or form[action*='post'] etc
        possible = soup.select("form.message-form, form#QuickReplyForm, form[action*='post'], form[action*='posts']")
        if possible:
            form = possible[0]
        else:
            # try any form that contains textarea
            forms = soup.select("form")
            for f in forms:
                if f.select_one("textarea"):
                    form = f
                    break
        if not form:
            return {"ok": False, "error": "Reply form not found on page"}

        action = form.get("action") or url
        action = action if action.startswith("http") else urljoin(FORUM_BASE.rstrip("/") + "/", action.lstrip("/"))
        # collect form fields
        payload = {}
        for inp in form.select("input"):
            name = inp.get("name")
            if not name:
                continue
            val = inp.get("value", "")
            payload[name] = val
        # collect any hidden textareas? ignore
        # find textarea name to put message
        textarea = form.select_one("textarea")
        if textarea:
            tname = textarea.get("name")
        else:
            # try common field names
            tname = None
            for k in ("message", "message_html", "message_text", "message_body"):
                if k:
                    tname = k
                    break
        if not tname:
            return {"ok": False, "error": "Cannot find textarea field to post message"}

        # set message
        payload[tname] = message

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ForumPoster/1.0)",
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest"
        }
        cookies = build_cookies()
        try:
            r = requests.post(action, data=payload, headers=headers, cookies=cookies, timeout=20, allow_redirects=True)
            # success heuristics: 200 or 302 and not 403
            if r.status_code in (200, 302):
                # check response for errors
                text = r.text or ""
                # if XenForo returns JSON for quick replies, try to parse it
                if "error" in text.lower() and r.status_code != 302:
                    return {"ok": False, "error": "Server returned error", "response": text[:1000]}
                return {"ok": True, "response": text[:1000]}
            else:
                return {"ok": False, "error": f"HTTP {r.status_code}", "response": r.text[:1000]}
        except Exception as e:
            return {"ok": False, "error": f"Post error: {e}"}

def stay_online_loop():
    """
    Постоянно поддерживает активность аккаунта на форуме,
    отправляя запрос на главную страницу с куками.
    """
    cookies = build_cookies()
    url = FORUM_BASE  # главная форума или профиль

    while True:
        try:
            requests.get(url, cookies=cookies, timeout=10)
            print("[ONLINE] Активность обновлена")
        except Exception as e:
            print("[ONLINE ERROR]", e)

        time.sleep(180)  # каждые 3 минуты (можно уменьшить до 120)
