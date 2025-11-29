# bot/command_handler.py
import re
import traceback
from .storage import (
    add_track, remove_track, list_tracks,
    add_warn, get_warns, clear_warns,
    add_ban, remove_ban, is_banned, update_last
)
from .deepseek_ai import ask_ai
from .permissions import is_admin
from .utils import normalize_url, detect_type
from .forum_tracker import ForumTracker
import sqlite3
import os
from config import FORUM_BASE

DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot_data.db")

class CommandHandler:
    def __init__(self, vk):
        self.vk = vk
        self.tracker = ForumTracker(vk)
        self._last_msg = None

    # ---------------------------------------------------------
    #                       HANDLE
    # ---------------------------------------------------------
    def handle(self, text: str, peer_id: int, user_id: int):
        try:
            txt = (text or "").strip()
            if not txt:
                return

            # ——— анти-дубль ———
            last = self._last_msg
            cur = f"{peer_id}:{user_id}:{txt}"
            if last == cur:
                return
            self._last_msg = cur
            # ——————————————

            parts = txt.split(maxsplit=2)
            cmd = parts[0].lower()

            # ——— авто-кик при бане ———
            try:
                if is_banned(peer_id, user_id):
                    if peer_id > 2000000000:
                        try:
                            chat_id = peer_id - 2000000000
                            self.vk.api.messages.removeChatUser(chat_id=chat_id, member_id=user_id)
                        except:
                            pass
                    return
            except:
                pass

            # ----------- команды -----------

            if cmd == "/track":
                return self.cmd_track(peer_id, parts)

            if cmd == "/untrack":
                return self.cmd_untrack(peer_id, parts)

            if cmd == "/list":
                return self.cmd_list(peer_id)

            if cmd == "/check":
                return self.cmd_check(peer_id)

            if cmd == "/checkfa":
                return self.cmd_checkfa(peer_id, parts)

            if cmd == "/ai":
                return self.cmd_ai(peer_id, parts)

            if cmd == "/otvet":
                return self.cmd_otvet(peer_id, parts)


            # 🔥🔥🔥 НОВАЯ КОМАНДА DEBUG — ДОБАВЛЕНО
            if cmd == "/debug_otvet":
                return self.cmd_debug_otvet(peer_id, parts)
        # 🔥 NEW: Проверка куков
            if cmd == "/checkcookies":
                return self.cmd_checkcookies(peer_id)

            # --- админ команды ---
            admin_cmds = (
                "/kick","/ban","/unban","/mute","/unmute",
                "/warn","/warns","/clearwarns","/stats"
            )

            if cmd in admin_cmds and not is_admin(self.vk.api, peer_id, user_id):
                self.vk.send(peer_id, "❌ У вас нет прав для этой команды.")
                return

            if cmd == "/kick": return self.cmd_kick(peer_id, parts)
            if cmd == "/ban": return self.cmd_ban(peer_id, parts)
            if cmd == "/unban": return self.cmd_unban(peer_id, parts)
            if cmd == "/mute": return self.cmd_mute(peer_id, parts)
            if cmd == "/unmute": return self.cmd_unmute(peer_id, parts)
            if cmd == "/warn": return self.cmd_warn(peer_id, parts)
            if cmd == "/warns": return self.cmd_warns(peer_id, parts)
            if cmd == "/clearwarns": return self.cmd_clearwarns(peer_id, parts)
            if cmd == "/stats": return self.cmd_stats(peer_id)

            if cmd == "/help": return self.cmd_help(peer_id)

            # если нет такой команды
            self.vk.send(peer_id, "Неизвестная команда. Напиши /help")

        except Exception as e:
            self.vk.send(peer_id, f"Ошибка: {e}")
            traceback.print_exc()

    # ---------------------------------------------------------
    # 🔍 НОВАЯ ФУНКЦИЯ DEBUG
    # ---------------------------------------------------------
    def cmd_debug_otvet(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /debug_otvet <url>")

        url = normalize_url(parts[1])
        try:
            res = self.tracker.debug_reply_form(url)
            # VK ограничение 4000 символов
            if len(res) < 3900:
                self.vk.send(peer_id, res)
            else:
                chunks = [res[i:i+3800] for i in range(0, len(res), 3800)]
                for ch in chunks:
                    self.vk.send(peer_id, ch)
        except Exception as e:
            return self.vk.send(peer_id, f"❌ Ошибка debug: {e}")

       
    def cmd_checkcookies(self, peer_id):
        r = self.forum.check_cookies()

        msg = (
            "🔍 Проверка cookies\n"
            f"Статус: {r.get('status')}\n"
            f"Авторизация: {r.get('logged_in')}\n\n"
            f"Cookies:\n{r.get('cookies_sent')}\n\n"
            f"HTML:\n{r.get('html_sample')}"
        )

        self.vk.send(peer_id, msg)


    # ---------------------------------------------------------
    #               ВСЕ ОСТАЛЬНЫЕ КОМАНДЫ (как были)
    # ---------------------------------------------------------

    def cmd_track(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /track <url>")
        url = normalize_url(parts[1])
        if not url.startswith(FORUM_BASE):
            return self.vk.send(peer_id, f"❌ Можно отслеживать только: {FORUM_BASE}")
        try:
            latest = self.tracker.fetch_latest_post_id(url)
        except:
            latest = None
        add_track(peer_id, url, detect_type(url))
        if latest:
            try: update_last(peer_id, url, str(latest))
            except: pass
        self.vk.send(peer_id, f"✅ Отслеживание добавлено: {url}")

    def cmd_untrack(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /untrack <url>")
        url = normalize_url(parts[1])
        remove_track(peer_id, url)
        self.vk.send(peer_id, f"🗑 Отслеживание удалено: {url}")

    def cmd_list(self, peer_id):
        rows = list_tracks(peer_id)
        if not rows:
            return self.vk.send(peer_id, "Нет отслеживаемых ссылок.")
        lines = [f"{u} ({t}) last: {l}" for u,t,l in rows]
        self.vk.send(peer_id, "📌 Отслеживаемые:\n" + "\n".join(lines))

    def cmd_check(self, peer_id):
        self.vk.send(peer_id, "⏳ Запуск проверки…")
        ok = self.vk.trigger_check()
        self.vk.send(peer_id, "✅ Проверка запущена." if ok else "❌ Ошибка.")

    def cmd_checkfa(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /checkfa <url>")
        url = normalize_url(parts[1])
        if not url.startswith(FORUM_BASE):
            return self.vk.send(peer_id, f"❌ Только ссылки {FORUM_BASE}")
        try:
            posts = self.tracker.manual_fetch_posts(url)
        except Exception as e:
            return self.vk.send(peer_id, f"❌ Ошибка загрузки: {e}")
        if not posts:
            return self.vk.send(peer_id, "⚠️ Нет сообщений.")
        batch = []
        for p in posts:
            entry = f"👤 {p['author']} • {p['date']}\n{p['text'][:1200]}\n🔗 {p['link']}"
            batch.append(entry)
            if len(batch) >= 3:
                self.vk.send_big(peer_id, "\n\n".join(batch))
                batch = []
        if batch:
            self.vk.send_big(peer_id, "\n\n".join(batch))

    def cmd_ai(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /ai <текст>")
        ans = ask_ai(parts[1])
        self.vk.send(peer_id, ans)

    def cmd_otvet(self, peer_id, parts):
        if len(parts) < 3:
            return self.vk.send(peer_id, "Использование: /otvet <url> <текст>")
        url = normalize_url(parts[1])
        text = parts[2]
        if not url.startswith(FORUM_BASE):
            return self.vk.send(peer_id, f"❌ Только форум {FORUM_BASE}")
        try:
            res = self.tracker.post_message(url, text)
        except Exception as e:
            return self.vk.send(peer_id, f"Ошибка: {e}")
        if res.get("ok"):
            try:
                latest = self.tracker.fetch_latest_post_id(url)
                if latest: update_last(peer_id, url, str(latest))
            except:
                pass
            return self.vk.send(peer_id, "✅ Сообщение отправлено.")
        else:
            return self.vk.send(peer_id, f"❌ Ошибка: {res.get('error')}")

    def cmd_kick(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /kick <id>")
        if peer_id <= 2000000000:
            return self.vk.send(peer_id, "Kick работает только в беседах.")
        uid = self._parse_user(parts[1])
        try:
            chat = peer_id - 2000000000
            self.vk.api.messages.removeChatUser(chat_id=chat, member_id=uid)
            self.vk.send(peer_id, f"👢 Кикнут: {uid}")
        except Exception as e:
            self.vk.send(peer_id, f"Ошибка kick: {e}")

    def cmd_ban(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /ban <id>")
        uid = self._parse_user(parts[1])
        add_ban(peer_id, uid)
        if peer_id > 2000000000:
            try:
                chat = peer_id - 2000000000
                self.vk.api.messages.removeChatUser(chat_id=chat, member_id=uid)
            except:
                pass
        self.vk.send(peer_id, f"🚫 Забанен: {uid}")

    def cmd_unban(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /unban <id>")
        uid = self._parse_user(parts[1])
        remove_ban(peer_id, uid)
        self.vk.send(peer_id, f"✅ Разбанен: {uid}")

    def cmd_mute(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /mute <id> <sec>")
        args = parts[1].split()
        uid = self._parse_user(args[0])
        sec = int(args[1]) if len(args) > 1 and args[1].isdigit() else 600
        self.vk.send(peer_id, f"🔇 {uid} замьючен на {sec} сек (симуляция).")

    def cmd_unmute(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /unmute <id>")
        uid = self._parse_user(parts[1])
        self.vk.send(peer_id, f"🔊 {uid} размьючен (симуляция).")

    def cmd_warn(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /warn <id>")
        uid = self._parse_user(parts[1])
        add_warn(peer_id, uid)
        self.vk.send(peer_id, f"⚠️ {uid} предупреждён. Всего: {get_warns(peer_id, uid)}")

    def cmd_warns(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /warns <id>")
        uid = self._parse_user(parts[1])
        self.vk.send(peer_id, f"Предупреждений у {uid}: {get_warns(peer_id, uid)}")

    def cmd_clearwarns(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /clearwarns <id>")
        uid = self._parse_user(parts[1])
        clear_warns(peer_id, uid)
        self.vk.send(peer_id, f"♻️ Предупреждения очищены: {uid}")

    def cmd_stats(self, peer_id):
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
                "📊 Статистика:\n"
                f"Отслеживаемых: {total_tracks}\n"
                f"Warn-строк: {total_warns}\n"
                f"Баны: {total_bans}"
            )
            self.vk.send(peer_id, msg)
        except Exception as e:
            self.vk.send(peer_id, f"Ошибка stats: {e}")

    def cmd_help(self, peer_id):
        self.vk.send(peer_id,
            "/track <url>\n/untrack <url>\n/list\n/check\n/checkfa <url>\n"
            "/otvet <url> <text>\n/ai <text>\n"
            "/kick <id>\n/ban <id>\n/unban <id>\n"
            "/mute <id> <sec>\n/unmute <id>\n"
            "/warn <id>\n/warns <id>\n/clearwarns <id>\n/stats"
        )

    def _parse_user(self, s):
        if not s:
            return 0
        s = s.strip()
        m = re.search(r"id(\d+)", s)
        if m:
            return int(m.group(1))
        m2 = re.search(r"(\d+)", s)
        if m2:
            return int(m2.group(1))
        return 0
