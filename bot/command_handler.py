from .storage import add_track, remove_track, list_tracks, add_warn, get_warns, clear_warns
from .deepseek_ai import ask_ai
from .permissions import is_admin
from .utils import normalize_url, detect_type
import re

class CommandHandler:
    def __init__(self, vk):
        self.vk = vk

    def handle(self, text: str, peer_id: int, user_id: int):
        txt = text.strip()
        parts = txt.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd == "/track":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /track <url>")
                return
            url = normalize_url(parts[1])
            typ = detect_type(url)
            if typ == "unknown":
                self.vk.send(peer_id, "Не распознан тип ссылки. Дай ссылку на тему или раздел.")
                return
            add_track(peer_id, url, typ)
            self.vk.send(peer_id, f"✅ Добавил отслеживание: {url}")
            return

        if cmd == "/untrack":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /untrack <url>")
                return
            url = normalize_url(parts[1])
            remove_track(peer_id, url)
            self.vk.send(peer_id, f"🗑 Убрано отслеживание: {url}")
            return

        if cmd == "/list":
            rows = list_tracks(peer_id)
            if not rows:
                self.vk.send(peer_id, "Нет отслеживаемых ссылок.")
                return
            lines = [f"{r[0]} ({r[1]}) last:{r[2]}" for r in rows]
            self.vk.send(peer_id, "Отслеживаемые:\n" + "\n".join(lines))
            return

        if cmd == "/check":
            # форс чек — вызов poller проверки
            self.vk.send(peer_id, "Запускаю принудительную проверку...")
            try:
                self.vk.trigger_check()
            except Exception:
                self.vk.send(peer_id, "Ошибка при запуске проверки.")
            return

        if cmd == "/ai":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /ai <текст>")
                return
            prompt = parts[1]
            ans = ask_ai(prompt)
            self.vk.send(peer_id, ans)
            return

        # Admin moderation
        if cmd in ("/kick", "/ban", "/mute", "/unmute", "/warn", "/warns", "/clearwarns"):
            if not is_admin(self.vk.api, peer_id, user_id):
                self.vk.send(peer_id, "❌ У вас нет прав для этой команды.")
                return

        if cmd == "/kick":
            if len(parts) < 2:
                self.vk.send(peer_id, "Укажите user id или @link")
                return
            uid = self._parse_user(parts[1])
            # For group chats VK API uses removeChatUser(chat_id=..., user_id=...)
            chat_id = peer_id - 2000000000 if peer_id > 2000000000 else None
            if chat_id:
                try:
                    self.vk.api.messages.removeChatUser(chat_id=chat_id, member_id=uid)
                    self.vk.send(peer_id, f"👢 Выкинут: {uid}")
                except Exception as e:
                    self.vk.send(peer_id, f"Ошибка kick: {e}")
            else:
                self.vk.send(peer_id, "Kick работает только в беседах.")
            return

        if cmd == "/warn":
            if len(parts) < 2:
                self.vk.send(peer_id, "Укажите user id")
                return
            uid = self._parse_user(parts[1])
            add_warn(peer_id, uid)
            c = get_warns(peer_id, uid)
            self.vk.send(peer_id, f"⚠️ Предупреждение выдано. Всего предупреждений: {c}")
            return

        if cmd == "/warns":
            if len(parts) < 2:
                self.vk.send(peer_id, "Укажите user id")
                return
            uid = self._parse_user(parts[1])
            c = get_warns(peer_id, uid)
            self.vk.send(peer_id, f"Предупреждений: {c}")
            return

        if cmd == "/clearwarns":
            if len(parts) < 2:
                self.vk.send(peer_id, "Укажите user id")
                return
            uid = self._parse_user(parts[1])
            clear_warns(peer_id, uid)
            self.vk.send(peer_id, "Предупреждения очищены")
            return

        if cmd == "/help":
            self.vk.send(peer_id,
                "/track <url>\n/untrack <url>\n/list\n/check\n/ai <text>\n"
                "/kick <id>\n/ban <id> (not implemented)\n/mute <id> <sec>\n/warn <id>\n/warns <id>\n/clearwarns <id>")
            return

        self.vk.send(peer_id, "Неизвестная команда. Напиши /help")

    def _parse_user(self, s):
        # accept formats like vk.com/id123, id123, 123, @screenname
        s = s.strip()
        m = re.search(r'id(\d+)', s)
        if m:
            return int(m.group(1))
        m2 = re.search(r'(\d+)', s)
        if m2:
            return int(m2.group(1))
        return 0

