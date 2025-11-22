from bot.storage import (
    add_track, remove_track, list_tracks,
    add_warn, get_warns, clear_warns,
    add_ban, remove_ban, is_banned,
    stat_inc, stat_get
)
from bot.deepseek_ai import ask_ai
from bot.permissions import is_admin
from bot.utils import normalize_url, detect_type
import re


class CommandHandler:
    def __init__(self, vk):
        self.vk = vk

    def handle(self, text: str, peer_id: int, user_id: int):
        stat_inc("commands_total")

        # авто-кик забаненных
        if is_banned(peer_id, user_id):
            chat_id = peer_id - 2000000000
            try:
                self.vk.api.messages.removeChatUser(chat_id=chat_id, member_id=user_id)
            except:
                pass
            return

        txt = text.strip()
        parts = txt.split(maxsplit=1)
        cmd = parts[0].lower()

        # ===== TRACK =====
        if cmd == "/track":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /track <url>")
                return

            url = normalize_url(parts[1])
            typ = detect_type(url)

            if typ == "unknown":
                self.vk.send(peer_id, "❌ Дай ссылку на тему или раздел.")
                return

            add_track(peer_id, url, typ)
            self.vk.send(peer_id, f"Добавил отслеживание ({typ}): {url}")
            return

        # ===== UNTRACK =====
        if cmd == "/untrack":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /untrack <url>")
                return
            url = normalize_url(parts[1])
            remove_track(peer_id, url)
            self.vk.send(peer_id, f"Удалено: {url}")
            return

        # ===== LIST =====
        if cmd == "/list":
            rows = list_tracks(peer_id)
            if not rows:
                self.vk.send(peer_id, "Нет треков.")
                return
            msg = "\n".join([f"{r[0]} ({r[1]}) last={r[2]}" for r in rows])
            self.vk.send(peer_id, msg)
            return

        # ===== CHECK =====
        if cmd == "/check":
            ok = self.vk.trigger_check()
            self.vk.send(peer_id, "Проверяю..." if ok else "Ошибка запуска.")
            return

        # ===== AI =====
        if cmd == "/ai":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /ai <текст>")
                return
            self.vk.send(peer_id, ask_ai(parts[1]))
            return

        # ===== ADMIN =====
        if cmd in ("/kick", "/ban", "/unban", "/warn", "/warns", "/clearwarns"):
            if not is_admin(self.vk.api, peer_id, user_id):
                self.vk.send(peer_id, "Нет прав.")
                return

        # ----- KICK -----
        if cmd == "/kick":
            uid = self._parse_user(parts[1])
            chat = peer_id - 2000000000
            try:
                self.vk.api.messages.removeChatUser(chat_id=chat, member_id=uid)
                self.vk.send(peer_id, f"Кикнут {uid}")
            except Exception as e:
                self.vk.send(peer_id, f"Ошибка: {e}")
            return

        # ----- BAN -----
        if cmd == "/ban":
            uid = self._parse_user(parts[1])
            add_ban(peer_id, uid)
            self.vk.send(peer_id, f"🚫 Бан: {uid}")
            return

        # ----- UNBAN -----
        if cmd == "/unban":
            uid = self._parse_user(parts[1])
            remove_ban(peer_id, uid)
            self.vk.send(peer_id, f"Разбанен {uid}")
            return

        # ----- WARN -----
        if cmd == "/warn":
            uid = self._parse_user(parts[1])
            add_warn(peer_id, uid)
            self.vk.send(peer_id, f"Warn → {get_warns(peer_id, uid)}")
            return

        # ----- WARNS -----
        if cmd == "/warns":
            uid = self._parse_user(parts[1])
            c = get_warns(peer_id, uid)
            self.vk.send(peer_id, f"Всего варнов: {c}")
            return

        # ----- CLEARWARNS -----
        if cmd == "/clearwarns":
            uid = self._parse_user(parts[1])
            clear_warns(peer_id, uid)
            self.vk.send(peer_id, "Варны очищены.")
            return

        # ----- STATS -----
        if cmd == "/stats":
            msg = (
                f"📊 Статистика:\n"
                f"Команд выполнено: {stat_get('commands_total')}\n"
            )
            self.vk.send(peer_id, msg)
            return

        # HELP
        if cmd == "/help":
            self.vk.send(peer_id,
                "/track <url>\n"
                "/untrack <url>\n"
                "/list\n"
                "/check\n"
                "/ai <текст>\n"
                "/kick <id>\n"
                "/ban <id>\n"
                "/unban <id>\n"
                "/warn <id>\n"
                "/warns <id>\n"
                "/clearwarns <id>\n"
                "/stats")
            return

        self.vk.send(peer_id, "Неизвестная команда.")

    def _parse_user(self, s):
        m = re.search(r'id(\d+)', s)
        if m:
            return int(m.group(1))
        m2 = re.search(r'(\d+)', s)
        if m2:
            return int(m2.group(1))
        return 0
