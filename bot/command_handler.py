# bot/command_handler.py
import re
from .storage import (
    add_track, remove_track, list_tracks,
    add_warn, get_warns, clear_warns,
    add_ban, remove_ban, is_banned
)
from .deepseek_ai import ask_ai
from .permissions import is_admin
from .utils import normalize_url, detect_type
import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot_data.db")

class CommandHandler:
    def __init__(self, vk):
        self.vk = vk

    def handle(self, text: str, peer_id: int, user_id: int):
        txt = (text or "").strip()
        if not txt:
            return
        parts = txt.split(maxsplit=1)
        cmd = parts[0].lower()

        # auto-kick if banned (in chats)
        try:
            if is_banned(peer_id, user_id):
                if peer_id and peer_id > 2000000000:
                    try:
                        chat_id = peer_id - 2000000000
                        self.vk.api.messages.removeChatUser(chat_id=chat_id, member_id=user_id)
                    except Exception:
                        pass
                return
        except Exception:
            pass

        if cmd == "/track":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /track <url>")
                return
            url = normalize_url(parts[1])
            typ = detect_type(url)
            if typ == "unknown":
                self.vk.send(peer_id, "❌ Не распознан тип ссылки. Дай ссылку на тему или раздел.")
                return
            add_track(peer_id, url, typ)
            self.vk.send(peer_id, f"✅ Добавил отслеживание ({typ}): {url}")
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
            lines = [f"{r[0]} ({r[1]}) last: {r[2]}" for r in rows]
            self.vk.send(peer_id, "📌 Отслеживаемые:\n" + "\n".join(lines))
            return

        if cmd == "/check":
            self.vk.send(peer_id, "⏳ Запускаю принудительную проверку...")
            ok = self.vk.trigger_check()
            if ok:
                self.vk.send(peer_id, "✅ Проверка запущена.")
            else:
                self.vk.send(peer_id, "❌ Ошибка при запуске проверки.")
            return

        if cmd == "/ai":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /ai <текст>")
                return
            prompt = parts[1]
            ans = ask_ai(prompt)
            self.vk.send(peer_id, ans)
            return

        admin_cmds = ("/kick", "/ban", "/unban", "/mute", "/unmute", "/warn", "/warns", "/clearwarns", "/stats")
        if cmd in admin_cmds and not is_admin(self.vk.api, peer_id, user_id):
            self.vk.send(peer_id, "❌ У вас нет прав для этой команды.")
            return

        if cmd == "/kick":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /kick <user>")
                return
            uid = self._parse_user(parts[1])
            if peer_id <= 2000000000:
                self.vk.send(peer_id, "Kick работает только в беседах.")
                return
            chat_id = peer_id - 2000000000
            try:
                self.vk.api.messages.removeChatUser(chat_id=chat_id, member_id=uid)
                self.vk.send(peer_id, f"👢 Кикнут пользователь {uid}")
            except Exception as e:
                self.vk.send(peer_id, f"Ошибка kick: {e}")
            return

        if cmd == "/ban":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /ban <user>")
                return
            uid = self._parse_user(parts[1])
            add_ban(peer_id, uid)
            if peer_id > 2000000000:
                try:
                    chat_id = peer_id - 2000000000
                    self.vk.api.messages.removeChatUser(chat_id=chat_id, member_id=uid)
                except Exception:
                    pass
            self.vk.send(peer_id, f"🚫 Пользователь {uid} забанен в этой беседе.")
            return

        if cmd == "/unban":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /unban <user>")
                return
            uid = self._parse_user(parts[1])
            remove_ban(peer_id, uid)
            self.vk.send(peer_id, f"✅ Пользователь {uid} разбанен.")
            return

        if cmd == "/mute":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /mute <user> <sec>")
                return
            args = parts[1].split()
            uid = self._parse_user(args[0])
            sec = int(args[1]) if len(args) > 1 and args[1].isdigit() else 600
            try:
                self.vk.send(peer_id, f"🔇 Пользователь {uid} замьючен на {sec} сек (симуляция).")
            except Exception as e:
                self.vk.send(peer_id, f"Ошибка mute: {e}")
            return

        if cmd == "/unmute":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /unmute <user>")
                return
            uid = self._parse_user(parts[1])
            self.vk.send(peer_id, f"🔊 Пользователь {uid} размьючен (симуляция).")
            return

        if cmd == "/warn":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /warn <user>")
                return
            uid = self._parse_user(parts[1])
            add_warn(peer_id, uid)
            cnt = get_warns(peer_id, uid)
            self.vk.send(peer_id, f"⚠️ {uid} получил предупреждение. Всего: {cnt}")
            return

        if cmd == "/warns":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /warns <user>")
                return
            uid = self._parse_user(parts[1])
            cnt = get_warns(peer_id, uid)
            self.vk.send(peer_id, f"Предупреждений у {uid}: {cnt}")
            return

        if cmd == "/clearwarns":
            if len(parts) < 2:
                self.vk.send(peer_id, "Использование: /clearwarns <user>")
                return
            uid = self._parse_user(parts[1])
            clear_warns(peer_id, uid)
            self.vk.send(peer_id, f"Предупреждения у {uid} очищены.")
            return

        if cmd == "/stats":
            try:
                conn = sqlite3.connect(DB)
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM tracks")
                total_tracks = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM warns")
                total_warns = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM bans")
                total_bans = cur.fetchone()[0]
                conn.close()
                msg = (
                    f"📊 Статистика бота:\n"
                    f"Отслеживаемых записей (rows tracks): {total_tracks}\n"
                    f"Количество записанных предупреждений (rows warns): {total_warns}\n"
                    f"Количество банов (rows bans): {total_bans}\n"
                )
                self.vk.send(peer_id, msg)
            except Exception as e:
                self.vk.send(peer_id, f"Ошибка получения статистики: {e}")
            return

        if cmd == "/help":
            self.vk.send(peer_id,
                "/track <url>\n/untrack <url>\n/list\n/check\n/ai <text>\n"
                "/kick <id>\n/ban <id>\n/unban <id>\n/mute <id> <sec>\n/unmute <id>\n"
                "/warn <id>\n/warns <id>\n/clearwarns <id>\n/stats")
            return

        self.vk.send(peer_id, "Неизвестная команда. Напиши /help")

    def _parse_user(self, s):
        if not s:
            return 0
        s = s.strip()
        m = re.search(r'id(\d+)', s)
        if m:
            return int(m.group(1))
        m2 = re.search(r'(\d+)', s)
        if m2:
            return int(m2.group(1))
        return 0
