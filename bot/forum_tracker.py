# bot/forum_tracker.py
import threading
import time
import requests
from bs4 import BeautifulSoup
from .utils import detect_type, extract_thread_id, extract_post_id_from_anchor, normalize_url
from .storage import list_all_tracks, update_last
from config import XF_USER, XF_SESSION, XF_TFA_TRUST, FORUM_BASE, POLL_INTERVAL_SEC

def log(msg):
    print("[TRACKER]", msg)

def build_cookie_header():
    return f"xf_user={XF_USER}; xf_session={XF_SESSION}; xf_tfa_trust={XF_TFA_TRUST}"

class ForumTracker:
    def __init__(self, vk):
        self.vk = vk
        self.interval = int(POLL_INTERVAL_SEC or 20)
        self.running = False
        self.vk.set_trigger(self.force_check)

    def start(self):
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()

    def force_check(self):
        # run check once in background
        t = threading.Thread(target=self.check_all, daemon=True)
        t.start()

    def loop(self):
        self.running = True
        log("ForumTracker loop started")
        while self.running:
            try:
                self.check_all()
            except Exception as e:
                log(f"Tracker loop error: {e}")
            time.sleep(self.interval)

    def check_all(self):
        rows = list_all_tracks()
        if not rows:
            return
        by_url = {}
        for peer_id, url, typ, last_id in rows:
            by_url.setdefault(url, []).append((peer_id, typ, last_id))

        for url, subscribers in by_url.items():
            try:
                self._check_url(url, subscribers)
            except Exception as e:
                log(f"Error checking {url}: {e}")

    def _fetch(self, url):
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Cookie": build_cookie_header()
        }
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200:
                return r.text
            log(f"HTTP {r.status_code} for {url}")
            return None
        except Exception as e:
            log(f"Fetch error for {url}: {e}")
            return None

    def _check_url(self, url, subscribers):
        url = normalize_url(url)
        typ = detect_type(url)
        html = self._fetch(url)
        if not html:
            return
        soup = BeautifulSoup(html, "html.parser")

        if typ == "thread":
            posts = soup.select("article.message, article.message--post, article.message--post.js-post")
            if not posts:
                return
            newest = posts[-1]
            post_id = extract_post_id_from_anchor(newest) or extract_thread_id(url)
            author_node = newest.select_one(".message-name a, .username, .message-userCard a")
            author = author_node.get_text(strip=True) if author_node else "Неизвестно"
            date_node = newest.select_one("time")
            date = date_node.get("datetime") if date_node and date_node.get("datetime") else (date_node.get_text(strip=True) if date_node else "?")
            body_node = newest.select_one(".bbWrapper, .message-body, .message-content")
            text = body_node.get_text("\n", strip=True) if body_node else "(нет текста)"
            link_to_post = f"{url}#post-{post_id}" if post_id else url
            for peer_id, _, last_id in subscribers:
                if last_id is None or str(last_id) != str(post_id):
                    msg = f"📝 Новый пост в теме\n📅 {date}\n👤 {author}\n\n{text[:1500]}\n\n🔗 {link_to_post}"
                    try:
                        self.vk.send(peer_id, msg)
                    except Exception as e:
                        log(f"VK send error: {e}")
                    update_last(peer_id, url, str(post_id))

        elif typ == "forum":
            items = soup.select(".structItem--thread, .structItem")
            if not items:
                return
            parsed = []
            for it in items:
                link_node = it.select_one(".structItem-title a, a[href*='/threads/'], a[href*='index.php?threads=']")
                if not link_node:
                    continue
                href = link_node.get("href") or ""
                full = href if href.startswith("http") else (FORUM_BASE.rstrip("/") + href)
                tid = extract_thread_id(full)
                title = link_node.get_text(strip=True)
                author_node = it.select_one(".structItem-minor a, .structItem-parts a, .username")
                author = author_node.get_text(strip=True) if author_node else "Неизвестно"
                if tid:
                    parsed.append({"tid": tid, "title": title, "author": author, "url": full})
            # sort by id (as int) to try sending newest last
            try:
                parsed_sorted = sorted(parsed, key=lambda x: int(x["tid"]))
            except Exception:
                parsed_sorted = parsed
            for peer_id, _, last_id in subscribers:
                # send up to last 6 newest threads
                for th in parsed_sorted[-6:]:
                    if last_id is None or str(th["tid"]) != str(last_id):
                        msg = f"🆕 Новая тема\n📄 {th['title']}\n👤 Автор: {th['author']}\n🔗 {th['url']}"
                        try:
                            self.vk.send(peer_id, msg)
                        except Exception as e:
                            log(f"VK send error: {e}")
                        update_last(peer_id, url, th["tid"])
