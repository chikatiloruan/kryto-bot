from config import XF_USER, XF_TFA_TRUST, XF_SESSION, FORUM_BASE
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from .storage import list_all_tracks, update_last
from .utils import detect_type, extract_thread_id, extract_forum_id, normalize_url
from typing import Optional

POLL_INTERVAL = 10  # можно менять через переменную в конфиге, если нужно


class ForumTracker:
    def __init__(self, vkbot):
        self.vk = vkbot
        # trigger для команды /check
        self.vk.set_trigger(lambda: asyncio.get_event_loop().create_task(self.check_all()))
        
        # cookies из config.py
        self.cookies = [
            {"xf_user": XF_USER, "xf_session": XF_SESSION, "xf_tfa_trust": XF_TFA_TRUST},
            {"xf_user": XF_USER, "xf_session": XF_SESSION, "xf_tfa_trust": XF_TFA_TRUST},
            {"xf_user": XF_USER, "xf_session": XF_SESSION, "xf_tfa_trust": XF_TFA_TRUST},
        ]
        self.loop_task = None

    async def start_loop(self):
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
        async def do_req(cookie_set):
            headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
            cookie_header = "; ".join([
                f"xf_user={cookie_set.get('xf_user','')}",
                f"xf_session={cookie_set.get('xf_session','')}",
                f"xf_tfa_trust={cookie_set.get('xf_tfa_trust','')}"
            ])
            try:
                async with session.get(url, headers={**headers, "Cookie": cookie_header}, timeout=30) as resp:
                    text = await resp.text()
                    if resp.status == 200 and text:
                        return text
                    return None
            except Exception:
                return None

        tasks = [asyncio.create_task(do_req(c)) for c in self.cookies]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=25)
        for d in done:
            res = d.result()
            if res:
                for p in pending:
                    p.cancel()
                return res
        for p in pending:
            try:
                r = await p
                if r:
                    return r
            except Exception:
                pass
        return None

    async def _check_url(self, session: aiohttp.ClientSession, url: str, subscribers):
    """
    Проверяет один трек (тему или форум) и уведомляет подписчиков о новых постах/темах.
    subscribers: list of (peer_id, type, last_id)
    """
    url = normalize_url(url)
    typ = "thread" if "/threads/" in url else "forum" if "/forums/" in url else "unknown"
    if typ == "unknown":
        return

    # --- GET с куками ---
    cookies_list = [
        {"xf_user": self.XF_USER, "xf_session": self.XF_SESSION, "xf_tfa_trust": self.XF_TFA_TRUST}
    ]
    html = None
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for cookies in cookies_list:
        try:
            async with session.get(url, headers=headers, cookies=cookies, timeout=30) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    break
        except:
            continue
    if not html:
        print("Failed to fetch:", url)
        return

    soup = BeautifulSoup(html, "html.parser")

    if typ == "thread":
        # --- парсим посты ---
        posts = soup.select("article.message.message--post")
        if not posts:
            return
        parsed = []
        for node in posts:
            post_id = node.get("data-message-id") or node.get("id")
            text_node = node.select_one("div.bbWrapper")
            text = text_node.get_text("\n", strip=True) if text_node else ""
            if post_id and text:
                parsed.append({"id": str(post_id), "text": text})
        if not parsed:
            return
        newest = parsed[-1]  # последний пост
        for peer_id, _, last_id in subscribers:
            if last_id is None or last_id != newest["id"]:
                excerpt = newest["text"][:1500]
                self.vk.send(peer_id, f"[Новый пост]\n{excerpt}\n\n{url}")
                update_last(peer_id, url, newest["id"])

    elif typ == "forum":
        # --- парсим темы ---
        threads = []
        nodes = soup.select("div.structItem.structItem--thread a.structItem-title")
        for a in nodes:
            href = a.get("href")
            if not href:
                continue
            full_url = href if href.startswith("http") else "https://forum.matrp.ru" + href
            tid = extract_thread_id(full_url)
            if tid:
                threads.append((tid, full_url, a.get_text(strip=True)))
        # dedupe
        seen = {tid: (full, title) for tid, full, title in threads}
        for peer_id, _, last_id in subscribers:
            # отправляем только новые темы
            to_send = [ (tid, full, title) for tid, (full, title) in seen.items()
                        if last_id is None or tid != last_id ]
            for tid, full, title in reversed(to_send):
                self.vk.send(peer_id, f"🆕 Новая тема:\n{title}\n{full}")
                update_last(peer_id, url, tid)
        else:
            return
