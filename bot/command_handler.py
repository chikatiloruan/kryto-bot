# -*- coding: utf-8 -*-
"""
Переработанный command_handler.py

Файл организован в 3 логические части (секции):
  1) структура и инициализация
  2) реализации команд (track, untrack, list, ai, otvet, tlist, tlistall и т.д.)
  3) debug/утилиты и парсинг/вспомогательные методы

Цель: ясная, надёжная и читаемая реализация, исправлены проблемы с
неопределёнными переменными (res/chunks), корректная работа с tracker.fetch_html
и сессией, аккуратная разбивка длинных сообщений под ограничения VK.
"""

from __future__ import annotations

import re
import traceback
import sqlite3
import os
from typing import List, Tuple, Optional

# локальные импорты
from .storage import (
    add_track, remove_track, list_tracks,
    add_warn, get_warns, clear_warns,
    add_ban, remove_ban, is_banned, update_last
)
from .deepseek_ai import ask_ai
from .permissions import is_admin
from .utils import normalize_url, detect_type
from .forum_tracker import ForumTracker, parse_forum_topics
from config import FORUM_BASE

# путь к БД (для stats)
DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot_data.db")


# ==============================================================
#  1) СТРУКТУРА КЛАССА И ИНИЦИАЛИЗАЦИЯ
# ==============================================================
class CommandHandler:
    def __init__(self, vk):
        """vk - экземпляр обёртки VK (с методами send, send_big, api, trigger_check)
        tracker создаётся на основе ForumTracker(vk).
        """
        self.vk = vk
        # ForumTracker ожидает vk или (xf_user, xf_tfa, xf_session, vk)
        try:
            self.tracker = ForumTracker(vk)
        except Exception:
            # попытка с конфигом внутри ForumTracker
            self.tracker = ForumTracker(None) if hasattr(ForumTracker, '__call__') else None
        self._last_msg = None

    # ---------------------------------------------------------
    #                      Основной обработчик
    # ---------------------------------------------------------
    def handle(self, text: str, peer_id: int, user_id: int):
        try:
            txt = (text or "").strip()
            if not txt:
                return

            # анти-дубль
            last = self._last_msg
            cur = f"{peer_id}:{user_id}:{txt}"
            if last == cur:
                return
            self._last_msg = cur

            parts = txt.split(maxsplit=2)
            cmd = parts[0].lower()

            # авто-кик при бане
            try:
                if is_banned(peer_id, user_id):
                    if peer_id > 2000000000 and hasattr(self.vk, 'api'):
                        try:
                            chat_id = peer_id - 2000000000
                            self.vk.api.messages.removeChatUser(chat_id=chat_id, member_id=user_id)
                        except Exception:
                            pass
                    return
            except Exception:
                pass

            # --- команды ---
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
            if cmd == "/debug_otvet":
                return self.cmd_debug_otvet(peer_id, parts)
            if cmd == "/debug_forum":
                return self.cmd_debug_forum(peer_id, parts)
            if cmd == "/tlist":
                return self.cmd_tlist(peer_id, parts)
            if cmd == "/tlistall":
                return self.cmd_tlistall(peer_id, parts)
            if cmd == "/checkcookies":
                return self.cmd_checkcookies(peer_id)

            # --- админ команды ---
            admin_cmds = (
                "/kick","/ban","/unban","/mute","/unmute",
                "/warn","/warns","/clearwarns","/stats"
            )
            if cmd in admin_cmds and not is_admin(getattr(self.vk, 'api', None), peer_id, user_id):
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

            # неизвестная команда
            self.vk.send(peer_id, "Неизвестная команда. Напиши /help")

        except Exception as e:
            # всегда логируем и отправляем пользователю информативную ошибку
            try:
                self.vk.send(peer_id, f"Ошибка: {e}")
            except Exception:
                pass
            traceback.print_exc()


# ==============================================================
#  2) РЕАЛИЗАЦИЯ КОМАНД
# ==============================================================

    # -------------------- DEBUG (ответ-проверка формы) --------------------
    def cmd_debug_otvet(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /debug_otvet <url>")
        url = normalize_url(parts[1])
        try:
            res = self.tracker.debug_reply_form(url)
        except Exception as e:
            return self.vk.send(peer_id, f"❌ Ошибка debug: {e}")
        self._send_long(peer_id, res)

    def cmd_checkcookies(self, peer_id):
        try:
            r = self.tracker.check_cookies()
        except Exception as e:
            return self.vk.send(peer_id, f"Ошибка check_cookies: {e}")
        msg = (
            "🔍 Проверка cookies\n"
            f"Статус: {r.get('status')}\n"
            f"Авторизация: {r.get('logged_in')}\n\n"
            f"Cookies:\n{r.get('cookies_sent')}\n\n"
            f"HTML:\n{r.get('html_sample')}"
        )
        self.vk.send(peer_id, msg)

    def cmd_debug_forum(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /debug_forum <url>")
        url = normalize_url(parts[1])
        if not url.startswith(FORUM_BASE):
            return self.vk.send(peer_id, f"❌ Только {FORUM_BASE}")
        try:
            res = self.tracker.debug_forum(url)
        except Exception as e:
            return self.vk.send(peer_id, f"❌ Ошибка debug_forum: {e}")
        self._send_long(peer_id, res)

    # -------------------- TRACK / UNTRACK / LIST --------------------
    def cmd_track(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /track <url>")

        url = normalize_url(parts[1])

    # Проверяем что ссылка относится к форуму
        if not url.startswith(FORUM_BASE):
            return self.vk.send(peer_id, f"❌ Можно отслеживать только ссылки: {FORUM_BASE}")

    # ---------------------------------------------------------
    #       ДЕТЕКТ КАТЕГОРИИ (forum vs thread)
    # ---------------------------------------------------------
        if "/index.php?forums/" in url:
            typ = "forum"
        elif "/index.php?threads/" in url:
            typ = "thread"
        else:
            return self.vk.send(peer_id, "❌ Эта ссылка не является ни разделом, ни темой.")

    # ---------------------------------------------------------
    #       ПОЛУЧАЕМ ПОСЛЕДНИЙ ID
    # ---------------------------------------------------------
        latest = None

        try:
        # Если это тема — берём ID последнего поста
            if typ == "thread":
                latest = self.tracker.fetch_latest_post_id(url)

        # Если это раздел — берём TID самой последней темы
            elif typ == "forum":
                html = self.tracker.fetch_html(url)
                topics = parse_forum_topics(html, url)
                if topics:
                    latest = max(t["tid"] for t in topics)

        except Exception:
            pass

    # ---------------------------------------------------------
    #        СОХРАНЯЕМ В БАЗУ
    # ---------------------------------------------------------
        add_track(peer_id, url, typ)

        if latest:
            try:
                update_last(peer_id, url, str(latest))
            except:
                pass

    # ---------------------------------------------------------
    #      УВЕДОМЛЕНИЕ
    # ---------------------------------------------------------
        if typ == "forum":
            self.vk.send(peer_id, f"📁 Отслеживание раздела добавлено:\n{url}")
        else:
            self.vk.send(peer_id, f"📄 Отслеживание темы добавлено:\n{url}")


    def cmd_untrack(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /untrack <url>")
        url = normalize_url(parts[1])
        try:
            remove_track(peer_id, url)
            self.vk.send(peer_id, f"🗑 Отслеживание удалено: {url}")
        except Exception as e:
            self.vk.send(peer_id, f"Ошибка remove track: {e}")

    def cmd_list(self, peer_id):
        try:
            rows = list_tracks(peer_id)
            if not rows:
                return self.vk.send(peer_id, "Нет отслеживаемых ссылок.")
            lines = [f"{u} ({t}) last: {l}" for u,t,l in rows]
            self.vk.send(peer_id, "📌 Отслеживаемые:\n" + "\n".join(lines))
        except Exception as e:
            self.vk.send(peer_id, f"Ошибка list: {e}")

    def cmd_check(self, peer_id):
        try:
            self.vk.send(peer_id, "⏳ Запуск проверки…")
            ok = self.vk.trigger_check()
            self.vk.send(peer_id, "✅ Проверка запущена." if ok else "❌ Ошибка.")
        except Exception as e:
            self.vk.send(peer_id, f"Ошибка trigger_check: {e}")

    # -------------------- /checkfa (ручной fetch posts) --------------------
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
            entry = (
                f"👤 {p['author']} • {p['date']}\n"
                f"{p['text'][:1200]}\n"
                f"🔗 {p['link']}"
            )
            batch.append(entry)
            if len(batch) >= 3:
                try:
                    self.vk.send_big(peer_id, "\n\n".join(batch))
                except Exception:
                    for b in batch:
                        self.vk.send(peer_id, b)
                batch = []
        if batch:
            try:
                self.vk.send_big(peer_id, "\n\n".join(batch))
            except Exception:
                for b in batch:
                    self.vk.send(peer_id, b)

    # -------------------- AI --------------------
    def cmd_ai(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /ai <текст>")
        try:
            ans = ask_ai(parts[1])
            self.vk.send(peer_id, ans)
        except Exception as e:
            self.vk.send(peer_id, f"AI Ошибка: {e}")

    # -------------------- POST MESSAGE --------------------
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
                if hasattr(self.tracker, 'fetch_latest_post_id'):
                    latest = self.tracker.fetch_latest_post_id(url)
                    if latest: update_last(peer_id, url, str(latest))
            except Exception:
                pass
            return self.vk.send(peer_id, "✅ Сообщение отправлено.")
        else:
            return self.vk.send(peer_id, f"❌ Ошибка: {res.get('error')}")

    # -------------------- TLIST / TLISTALL --------------------
    def cmd_tlist(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /tlist <url-раздела>")
        url = normalize_url(parts[1])
        if "forums" not in url.lower():
            return self.vk.send(peer_id, "❌ Это не ссылка на раздел.")
        try:
            html = self.tracker.fetch_html(url)
        except Exception as e:
            return self.vk.send(peer_id, f"Ошибка fetch_html: {e}")
        if not html:
            return self.vk.send(peer_id, "❌ Не удалось загрузить HTML раздела.")
        topics = parse_forum_topics(html, url)
        if not topics:
            return self.vk.send(peer_id, "⚠️ Темы не найдены.")
        # берём первые 5 (самые ранние в списке — в зависимости от parse order)
        last5 = topics[:5]
        out = "📝 Последние темы раздела:\n\n"
        for t in last5:
            out += f"📄 {t['title']}\n🔗 {t['url']}\n👤 {t['author']}\n\n"
        self.vk.send(peer_id, out)

    def cmd_tlistall(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /tlistall <url-раздела>")
        url = normalize_url(parts[1])
        if "forums" not in url.lower():
            return self.vk.send(peer_id, "❌ Это не ссылка на раздел.")
        try:
            html = self.tracker.fetch_html(url)
        except Exception as e:
            return self.vk.send(peer_id, f"Ошибка fetch_html: {e}")
        if not html:
            return self.vk.send(peer_id, "❌ Не удалось загрузить раздел.")
        topics = parse_forum_topics(html, url)
        if not topics:
            return self.vk.send(peer_id, "⚠️ Темы не найдены.")
        # отправляем чанками
        max_len = 3500
        block = ""
        chunks = []
        for t in topics:
            line = f"📄 {t['title']}\n🔗 {t['url']}\n👤 {t['author']}\n\n"
            if len(block) + len(line) > max_len:
                chunks.append(block)
                block = ""
            block += line
        if block:
            chunks.append(block)
        for c in chunks:
            self.vk.send(peer_id, c)

    # -------------------- ADMIN COMMANDS --------------------
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
            except Exception:
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
        self.vk.send(
            peer_id,
            "/track <url>\n/untrack <url>\n/list\n/check\n/checkfa <url>\n"
            "/tlist <url>\n/tlistall <url>\n"
            "/otvet <url> <text>\n/ai <text>\n"
            "/kick <id>\n/ban <id>\n/unban <id>\n"
            "/mute <id> <sec>\n/unmute <id>\n"
            "/warn <id>\n/warns <id>\n/clearwarns <id>\n/stats"
        )

    # ---------------------------------------------------------
    # 3) УТИЛИТЫ
    # ---------------------------------------------------------
    def _parse_user(self, s: str) -> int:
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

    def _send_long(self, peer_id: int, text: str):
        """Разбивает длинный текст на чанки и отправляет в VK.
        Используем запасной механизм, если vk.send_big есть — используем его.
        """
        if not text:
            return
        # предпочитаемый метод — send_big (если реализован)
        try:
            if hasattr(self.vk, 'send_big'):
                self.vk.send_big(peer_id, text)
                return
        except Exception:
            pass
        # разбиваем по 3800 символов
        max_chunk = 3800
        chunks = [text[i:i+max_chunk] for i in range(0, len(text), max_chunk)]
        for ch in chunks:
            try:
                self.vk.send(peer_id, ch)
            except Exception:
                # если даже send падает — игнорируем, но печатаем в stdout
                print(f"[CMD] Failed to send chunk to {peer_id}")

# --- конец файла ---
