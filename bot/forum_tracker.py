# bot/forum_tracker.py
import asyncio
import threading
import sys
import time
import traceback
import requests
from bs4 import BeautifulSoup

from .utils import (
    detect_type,
    extract_thread_id,
    extract_post_id_from_anchor,
)
from .storage import list_all_tracks, update_last
from .config import COOKIE

def log(msg):
    print(f"[TRACKER] {msg}", file=sys.stderr)


class ForumTracker:
    def __init__(self, vk):
        self.vk = vk
        self.running = False
        self.interval = 20  # сек

        # callback на /check
        self.vk.set_trigger(self.force_check)

    def start(self):
        t = threading.Thread(target=self.loop, daemon=True)
        t.start()

    def force_check(self):
        log("Forced check triggered")
        self.check()

    def loop(self):
        self.running = True
        log("ForumTracker started")

        while self.running:
            try:
                self.check()
            except Exception as e:
                log(f"CHECK ERROR: {e}\n{traceback.format_exc()}")
            time.sleep(self.interval)

    def check(self):
        tracks = list_all_tracks()

        for peer_id, url, ttype, last in tracks:
            try:
                if ttype == "thread":
                    self.check_thread(peer_id, url, last)
                elif ttype == "forum":
                    self.check_forum(peer_id, url, last)
            except Exception as e:
                log(f"TRACK ERROR for {url}: {e}\n{traceback.format_exc()}")

    # ================= THREAD MODE ====================

    def check_thread(self, peer_id, url, last_id):
        log(f"Checking thread: {url}")

        html = self.fetch(url)
        if not html:
            return

        soup = BeautifulSoup(html, "html.parser")

        posts = soup.select("article")
        if not posts:
            log("No article nodes found in thread")
            return

        last_post = posts[-1]
        pid = extract_post_id_from_anchor(last_post)

        if not pid:
            log(f"No post-id extracted in {url}")
            return

        if last_id and pid == last_id:
            return

        user_el = last_post.select_one(".message-userDetails a")
        user = user_el.text.strip() if user_el else "Неизвестно"

        date_el = last_post.select_one("time")
        date = date_el.text.strip() if date_el else "N/A"

        text_el = last_post.select_one(".bbWrapper")
        text = text_el.text.strip()[:500] if text_el else "(без текста)"

        msg = (
            "🆕 Новое сообщение в теме:\n"
            f"👤 {user}\n"
            f"📅 {date}\n"
            f"💬 {text}\n"
            f"🔗 {url}"
        )
        self.vk.send(peer_id, msg)
        update_last(peer_id, url, pid)

    # ================= FORUM MODE =====================

    def check_forum(self, peer_id, url, last_id):
        log(f"Checking forum: {url}")

        html = self.fetch(url)
        if not html:
            return

        soup = BeautifulSoup(html, "html.parser")

        topics = soup.select("div.structItem--thread")
        if not topics:
            log("No topics found")
            return

        top = topics[0]

        link = top.select_one("a.structItem-title")
        if not link:
            return

        topic_url = link["href"]
        topic_title = link.text.strip()

        pid = extract_thread_id(topic_url)
        if not pid:
            log("Forum topic ID not extracted")
            return

        if last_id and pid == last_id:
            return

        user_el = top.select_one(".username")
        user = user_el.text.strip() if user_el else "Неизвестно"

        msg = (
            "🆕 Новая тема в разделе:\n"
            f"📌 {topic_title}\n"
            f"👤 {user}\n"
            f"🔗 {topic_url}"
        )
        self.vk.send(peer_id, msg)
        update_last(peer_id, url, pid)

    # ================= FETCH ====================

    def fetch(self, url):
        try:
            r = requests.get(url, headers={"Cookie": COOKIE}, timeout=10)
            if r.status_code != 200:
                log(f"HTTP ERROR {r.status_code} for {url}")
                return None
            return r.text
        except Exception as e:
            log(f"REQUEST ERROR: {e}")
            return None
