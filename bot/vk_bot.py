import os
import threading
import time
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
import vk_api
from .command_handler import CommandHandler
from .storage import init_db
from aiohttp import web
import asyncio

class VKBot:
    def __init__(self):
        init_db()
        token = os.getenv("VK_TOKEN")
        if not token:
            raise RuntimeError("VK_TOKEN not set")
        self.vk_session = vk_api.VkApi(token=token)
        self.api = self.vk_session.get_api()
        # group id:
        gid = self.api.groups.getById()[0]['id']
        self.group_id = gid
        self.longpoll = VkBotLongPoll(self.vk_session, gid)
        self.handler = CommandHandler(self)
        # hook for forum_tracker to trigger immediate check
        self._trigger_check = None

    async def start(self):
        # start longpoll in separate thread (blocking)
        t = threading.Thread(target=self._longpoll_loop, daemon=True)
        t.start()
        # start keep-alive webserver
        self._start_keepalive()

    def _longpoll_loop(self):
        print("VK longpoll loop started")
        for event in self.longpoll.listen():
            try:
                if event.type == VkBotEventType.MESSAGE_NEW:
                    msg = event.object.message
                    text = msg.get("text", "") or ""
                    peer = msg["peer_id"]
                    from_id = msg.get("from_id") or msg.get("from_id", 0)
                    # handle commands only
                    if text.startswith("/"):
                        self.handler.handle(text, peer, from_id)
            except Exception as e:
                print("Longpoll error:", e)

    def send(self, peer_id: int, text: str):
        try:
            self.api.messages.send(peer_id=peer_id, message=text, random_id=0)
        except Exception as e:
            print("VK send error:", e)

    def trigger_check(self):
        # called by command /check to trigger forum tracker immediate check
        if self._trigger_check:
            try:
                self._trigger_check()
                return True
            except Exception:
                return False
        return False

    def set_trigger(self, fn):
        self._trigger_check = fn

    def _start_keepalive(self):
        port = int(os.getenv("KEEP_ALIVE_PORT", "8080"))
        async def handle(request):
            return web.Response(text="VK Forum Bot running")
        app = web.Application()
        app.router.add_get("/", handle)

        def run_app():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            runner = web.AppRunner(app)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, '0.0.0.0', port)
            loop.run_until_complete(site.start())
            print(f"Keep-alive server started on port {port}")
            loop.run_forever()

        t = threading.Thread(target=run_app, daemon=True)
        t.start()

