from config import XF_USER, XF_TFA_TRUST, XF_SESSION, FORUM_BASE
import os
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from .storage import list_all_tracks, update_last
from .utils import detect_type, extract_thread_id, extract_forum_id, normalize_url
from typing import Optional, List
import time

# Cookies envs
XF_USER = os.getenv("XF_USER")
XF_SESSION = os.getenv("XF_SESSION")
XF_TFA_TRUST = os.getenv("XF_TFA_TRUST")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "10"))

class ForumTracker:
    def __init__(self, vkbot):
        self.vk = vkbot
        # register trigger so command /check can call immediate check
        self.vk.set_trigger(lambda: asyncio.get_event_loop().create_task(self.check_all()))
        # session per run
        self.cookies = [
            {"xf_user": XF_USER, "xf_session": XF_SESSION, "xf_tfa_trust": XF_TFA_TRUST},
            {"xf_user": XF_USER, "xf_session": XF_SESSION, "xf_tfa_trust": XF_TFA_TRUST},
            {"xf_user": XF_USER, "xf_session": XF_SESSION, "xf_tfa_trust": XF_TFA_TRUST},
        ]
        # we keep them identical as values (user provided single set) — but structure allows different values if you set COOKIE1/2/3 envs separately
        # If multiple cookie sets required, user can set XF_USER_1 etc. and code may be extended.
        self.loop_task = None

    async def start_loop(self):
        # start background periodic check
        await asyncio.create_task(self.check_loop())

    async def check_loop(self):
        while True:
            try:
                await self.check_all()
            except Exception as e:
                print("Tracker loop error:", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def check_all(self):
        rows = list_all_tracks()
        # group by url to avoid duplicate requests
        by_url = {}
        for peer_id, url, typ, last_id in rows:
            by_url.setdefault(url, []).append((peer_id, typ, last_id))

        async with aiohttp.ClientSession() as session:
            tasks = []
            for url, subscribers in by_url.items():
                tasks.append(self._check_url(session, url, subscribers))
            if tasks:
                await asyncio.gather(*tasks)

    async def _fetch_with_all_cookies(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """
        Sends parallel requests with 3 cookie-sets and returns first successful HTML text.
        """
        async def do_req(cookie_set):
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible)"
            }
            # cookie_set is dict with three keys, build cookie header
            cookie_header = "; ".join([f"xf_user={cookie_set.get('xf_user','')}",
                                       f"xf_session={cookie_set.get('xf_session','')}",
                                       f"xf_tfa_trust={cookie_set.get('xf_tfa_trust','')}"])
            try:
                async with session.get(url, headers={**headers, "Cookie": cookie_header}, timeout=30) as resp:
                    text = await resp.text()
                    if resp.status == 200 and text:
                        return text
                    return None
            except Exception:
                return None

        # Launch 3 concurrent
        tasks = [asyncio.create_task(do_req(c)) for c in self.cookies]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=25)
        for d in done:
            res = d.result()
            if res:
                # cancel others
                for p in pending:
                    p.cancel()
                return res
        # if none returned quickly, gather results
        for p in pending:
            try:
                r = await p
                if r:
                    return r
            except Exception:
                pass
        return None

    async def _check_url(self, session: aiohttp.ClientSession, url: str, subscribers):
        # subscribers: list of (peer_id, type, last_id)
        url = normalize_url(url)
        typ = detect_type(url)

        html = await self._fetch_with_all_cookies(session, url)
        if not html:
            # can't fetch
            print("Failed to fetch:", url)
            return

        soup = BeautifulSoup(html, "html.parser")

        if typ == "thread":
            # find posts in thread: use selectors typical for xenforo
            posts = soup.select(".message, .message-body, .bbWrapper")
            if not posts:
                return
            # newest first attempt: site may order oldest first; take last node as newest
            # We'll build list of (id, text) newest->oldest
            parsed = []
            for node in posts:
                tid = node.get("data-message-id") or node.get("id") or None
                text = node.get_text("\n", strip=True)
                if not text:
                    continue
                parsed.append({"id": str(tid) if tid else text[:32], "text": text})
            # assume last element is newest
            if not parsed:
                return
            newest = parsed[-1]  # newest
            # notify subscribers if they haven't seen it
            for peer_id, _, last_id in subscribers:
                if last_id is None or last_id != newest["id"]:
                    # send new post(s) — we will send the last one
                    excerpt = newest["text"][:1500]
                    self.vk.send(peer_id, f"[Новый пост в теме]\n{excerpt}\n\n{url}")
                    update_last(peer_id, url, newest["id"])
        elif typ == "forum":
            # parse topics list in forum page. XenForo lists threads with links containing .<id>/
            threads = []
            # try several selectors
            for sel in ['.structItem--thread', '.structItem', '.title', '.structItem-title']:
                nodes = soup.select(f"{sel} a")
                for a in nodes:
                    href = a.get("href") or a.get("data-url")
                    if not href:
                        continue
                    full = href if href.startswith("http") else "https://forum.matrp.ru" + href
                    # try extract thread id
                    from .utils import extract_thread_id
                    tid = extract_thread_id(full)
                    if tid:
                        threads.append((full, tid, a.get_text(strip=True)))
            # dedupe by id
            seen = {}
            for full, tid, title in threads:
                seen[tid] = (full, title)
            # Now for each subscriber, check new threads vs last_id
            # We'll consider last_id stores last seen thread id
            for peer_id, _, last_id in subscribers:
                # send any threads newer than last_id — we don't have ordering, so we send a few latest
                to_send = []
                for tid, (full, title) in list(seen.items())[:10]:
                    if last_id is None or tid != last_id:
                        to_send.append((tid, full, title))
                # send in reverse so oldest first
                for tid, full, title in reversed(to_send):
                    self.vk.send(peer_id, f"🆕 Новая тема в разделе:\n{title}\n{full}")
                    update_last(peer_id, url, tid)
        else:
            # unknown type — ignore
            return

