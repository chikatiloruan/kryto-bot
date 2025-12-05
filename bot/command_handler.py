# -*- coding: utf-8 -*-
"""
Переработанный command_handler.py с поддержкой JSON-шаблонов (data/templates.json),
командами /profile, /checkpr, /shablon, /addsh, /removesh и улучшенной обработкой ошибок.
"""
from __future__ import annotations

import re
import traceback
import sqlite3
import os
import json
from typing import List, Tuple, Optional, Dict

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

# папка для JSON шаблонов
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
TEMPLATES_FILE = os.path.join(TEMPLATES_DIR, "templates.json")


# ----------------- Утилиты шаблонов (JSON) -----------------
def _ensure_templates_file():
    if not os.path.exists(TEMPLATES_DIR):
        try:
            os.makedirs(TEMPLATES_DIR, exist_ok=True)
        except Exception:
            pass
    if not os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def load_templates() -> Dict[str, Dict[str, str]]:
    _ensure_templates_file()
    try:
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_templates(data: Dict[str, Dict[str, str]]) -> bool:
    _ensure_templates_file()
    try:
        with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add_template_for_peer(peer_id: int, name: str, text: str) -> bool:
    data = load_templates()
    key = str(peer_id)
    if key not in data:
        data[key] = {}
    data[key][name] = text
    return save_templates(data)


def remove_template_for_peer(peer_id: int, name: str) -> bool:
    data = load_templates()
    key = str(peer_id)
    if key in data and name in data[key]:
        del data[key][name]
        # если пусто — удалить ключ
        if not data[key]:
            del data[key]
        return save_templates(data)
    return False


def get_template(peer_id: int, name: str) -> Optional[str]:
    data = load_templates()
    key = str(peer_id)
    if key in data:
        return data[key].get(name)
    return None


def list_templates(peer_id: int) -> List[str]:
    data = load_templates()
    key = str(peer_id)
    if key in data:
        return list(data[key].keys())
    return []


# ============================================================== #
#  Основной класс CommandHandler
# ============================================================== #
class CommandHandler:
    def __init__(self, vk):
        self.vk = vk

        try:
            # основной корректный запуск трекера
            self.tracker = ForumTracker(vk)
        except Exception as e:
            print(f"[TRACKER INIT ERROR] {e}")
            # если не удалось — не создаём трекер вообще
            self.tracker = None

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

            if cmd == "/debugtopics":
                return self.cmd_debugtopics(peer_id, parts)

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

            # шаблоны
            if cmd == "/addsh":
                return self.cmd_addsh(peer_id, parts)
            if cmd == "/removesh":
                return self.cmd_removesh(peer_id, parts)
            if cmd == "/shablon":
                return self.cmd_shablon(peer_id, parts)

            # профили
            if cmd == "/profile":
                return self.cmd_profile(peer_id, parts)
            if cmd == "/checkpr":
                return self.cmd_checkpr(peer_id, parts)

            # --- админ команды ---
            admin_cmds = (
                "/kick", "/ban", "/unban", "/mute", "/unmute",
                "/warn", "/warns", "/clearwarns", "/stats"
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
            try:
                self.vk.send(peer_id, f"Ошибка: {e}")
            except Exception:
                pass
            traceback.print_exc()

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
        clean_url = url.split("&")[0]

        if "/index.php?forums/" in clean_url:
            typ = "forum"
        elif "/index.php?threads/" in clean_url:
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
                if hasattr(self.tracker, "fetch_latest_post_id"):
                    latest = self.tracker.fetch_latest_post_id(clean_url)

            # Если это раздел — берём TID самой последней темы
            elif typ == "forum":
                html = self.tracker.fetch_html(clean_url)
                topics = parse_forum_topics(html, clean_url)
                if topics:
                    # сортируем по дате → если нет date, сортируем по tid
                    sortable = []
                    for t in topics:
                        dt = t.get("date") or ""
                        tid = int(t.get("tid", 0))
                        sortable.append((dt, tid, t))
                    
                    sortable.sort(key=lambda x: (x[0], x[1]))

                    last_topic = sortable[-1][2]
                    last_tid = sortable[-1][1]
                    last_date = sortable[-1][0]

                    # сохраняем tid;;date
                    latest = f"{last_tid};;{last_date}"

        except Exception:
            latest = None

        # ---------------------------------------------------------
        #        СОХРАНЯЕМ В БАЗУ
        # ---------------------------------------------------------
        add_track(peer_id, clean_url, typ)

        if latest:
            try:
                update_last(peer_id, clean_url, str(latest))
            except Exception:
                pass

        # ---------------------------------------------------------
        #      УВЕДОМЛЕНИЕ
        # ---------------------------------------------------------
        if typ == "forum":
            self.vk.send(peer_id, f"📁 Отслеживание раздела добавлено:\n{clean_url}")
        else:
            self.vk.send(peer_id, f"📄 Отслеживание темы добавлено:\n{clean_url}")

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
            lines = [f"{u} ({t}) last: {l}" for u, t, l in rows]
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
                    if latest:
                        update_last(peer_id, url, str(latest))
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
        # берём первые 5 (в порядке parse)
        last5 = topics[:5]
        out = "📝 Последние темы раздела:\n\n"
        for t in last5:
            # нормализуем ссылку: если это префикс (contains &prefix_id) — пытаемся превратить в thread
            url_to_send = t['url']
            out += f"📄 {t['title']}\n🔗 {url_to_send}\n👤 {t['author']}\n\n"
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

    # -------------------- ШАБЛОНЫ --------------------
    def cmd_addsh(self, peer_id, parts):
        """
        /addsh <name> <text>
        """
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /addsh <name> <text>")
        # parts[1] may include both name and text if maxsplit=2 wasn't used; parse robustly
        rest = parts[1] if len(parts) == 2 else parts[1] + (" " + (parts[2] if len(parts) > 2 else ""))
        # try split once on space
        m = re.match(r"(\S+)\s+(.+)", rest)
        if not m:
            return self.vk.send(peer_id, "Использование: /addsh <name> <text>")
        name = m.group(1).strip()
        text = m.group(2).strip()
        ok = add_template_for_peer(peer_id, name, text)
        if ok:
            self.vk.send(peer_id, f"✅ Шаблон '{name}' добавлен.")
        else:
            self.vk.send(peer_id, f"❌ Ошибка при сохранении шаблона '{name}'.")

    def cmd_removesh(self, peer_id, parts):
        """
        /removesh <name>
        """
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /removesh <name>")
        name = parts[1].strip()
        ok = remove_template_for_peer(peer_id, name)
        if ok:
            self.vk.send(peer_id, f"✅ Шаблон '{name}' удалён.")
        else:
            self.vk.send(peer_id, f"❌ Шаблон '{name}' не найден.")

    def cmd_shablon(self, peer_id, parts):
        """
        /shablon <name> <thread_url>
        Отправляет шаблон как ответ в указанную тему (uses tracker.post_message).
        """
        if len(parts) < 3:
            return self.vk.send(peer_id, "Использование: /shablon <name> <thread_url>")
        name = parts[1].strip()
        url = normalize_url(parts[2].strip())
        txt = get_template(peer_id, name)
        if not txt:
            return self.vk.send(peer_id, f"❌ Шаблон '{name}' не найден.")
        if not url.startswith(FORUM_BASE):
            return self.vk.send(peer_id, f"❌ URL должен быть на {FORUM_BASE}")
        try:
            res = self.tracker.post_message(url, txt)
        except Exception as e:
            return self.vk.send(peer_id, f"Ошибка отправки: {e}")
        if res.get("ok"):
            # обновляем last (если нужно)
            try:
                if hasattr(self.tracker, "fetch_latest_post_id"):
                    latest = self.tracker.fetch_latest_post_id(url)
                    if latest:
                        update_last(peer_id, url, str(latest))
            except Exception:
                pass
            return self.vk.send(peer_id, f"✅ Шаблон '{name}' отправлен в {url}")
        else:
            return self.vk.send(peer_id, f"❌ Ошибка постинга: {res.get('error')}")

    # -------------------- ПРОФИЛИ --------------------
    def cmd_profile(self, peer_id, parts):
        """
        /profile <url> - показать информацию о профиле (если доступно)
        """
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /profile <profile_url>")
        url = normalize_url(parts[1])
        if not url.startswith(FORUM_BASE):
            return self.vk.send(peer_id, f"❌ URL должен быть на {FORUM_BASE}")
        try:
            info = self._parse_profile(url)
            if not info:
                return self.vk.send(peer_id, "⚠️ Не удалось извлечь информацию о профиле.")
            lines = [
                f"👤 {info.get('username','—')}",
                f"📌 ID: {info.get('user_id','—')}",
                f"🕘 Регистрация: {info.get('registered','—')}",
                f"✉️ О себе: {info.get('about','—')[:800]}",
                f"📝 Постов: {info.get('message_count','—')}"
            ]
            self._send_long(peer_id, "\n".join(lines))
        except Exception as e:
            self.vk.send(peer_id, f"Ошибка profile: {e}")

    def cmd_checkpr(self, peer_id, parts):
        """
        /checkpr <url> - посмотреть чужой профиль (как /profile, алиас)
        """
        return self.cmd_profile(peer_id, parts)

    def _parse_profile(self, url: str) -> Optional[Dict[str, str]]:
        """
        Простой парсер страницы профиля XenForo: пытается извлечь имя, id, registered, message_count, about.
        Если профиль недоступен — возвращает None.
        """
        try:
            html = self.tracker.fetch_html(url)
            if not html:
                return None
            soup = __import__("bs4").BeautifulSoup(html, "html.parser")

            # username
            uname = None
            el = soup.select_one(".p-title-value .username, h1.p-title-value, .block-minor .username")
            if el:
                uname = el.get_text(strip=True)
            else:
                el = soup.select_one(".p-profile-header .username")
                if el:
                    uname = el.get_text(strip=True)

            # user id from data attributes or url
            user_id = None
            m = re.search(r"/members/[^.]+.(\d+)", url)
            if m:
                user_id = m.group(1)
            else:
                a = soup.select_one("[data-user-id], a[data-user-id]")
                if a:
                    user_id = a.get("data-user-id")

            # registered / message count: try common labels
            registered = None
            msg_count = None
            # XenForo often has dl.listPlain or pairs
            txt = soup.get_text(" ", strip=True)
            mreg = re.search(r"Registered\s*[:\s]*([A-Za-z0-9,.\- ]+)", txt, re.IGNORECASE)
            if mreg:
                registered = mreg.group(1).strip()
            mmsg = re.search(r"(Messages|Posts)\s*[:\s]*([0-9,]+)", txt, re.IGNORECASE)
            if mmsg:
                msg_count = mmsg.group(2).strip()

            # about
            about = ""
            about_el = soup.select_one(".p-profile-about, .about, .userAbout, .user-blurb, .message-userContent")
            if about_el:
                about = about_el.get_text(" ", strip=True)

            return {
                "username": uname or "",
                "user_id": user_id or "",
                "registered": registered or "",
                "message_count": msg_count or "",
                "about": about or ""
            }
        except Exception:
            return None

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
            "/addsh <name> <text>\n/removesh <name>\n/shablon <name> <thread_url>\n"
            "/profile <url>\n/checkpr <url>\n"
            "/kick <id>\n/ban <id>\n/unban <id>\n"
            "/mute <id> <sec>\n/unmute <id>\n"
            "/warn <id>\n/warns <id>\n/clearwarns <id>\n/stats"
        )
        
    def cmd_debugtopics(self, peer_id, parts):
        if len(parts) < 2:
            return self.vk.send(peer_id, "Использование: /debugtopics <url-раздела>")

        url = normalize_url(parts[1])
        if "forums" not in url.lower():
            return self.vk.send(peer_id, "❌ Это не ссылка на раздел.")

        try:
            html = self.tracker.fetch_html(url)
        except Exception as e:
            return self.vk.send(peer_id, f"Ошибка fetch_html: {e}")

        if not html:
            return self.vk.send(peer_id, "❌ Не удалось загрузить страницу.")

        topics = parse_forum_topics(html, url)
        if not topics:
            return self.vk.send(peer_id, "⚠️ Темы не найдены.")

        out = "🔍 DEBUG TOPICS\n\n"

        for t in topics[:20]:
            out += (
                f"TID: {t.get('tid')}\n"
                f"TITLE: {t.get('title')}\n"
                f"AUTHOR: {t.get('author')}\n"
                f"PINNED: {t.get('pinned')}\n"
                f"CREATED: {t.get('created')}\n"
                f"URL: {t.get('url')}\n\n"
            )

        # длинный текст → разбиваем
        self._send_long(peer_id, out)

    # ---------------------------------------------------------
    #  УТИЛИТЫ
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
        """Разбивает длинный текст на чанки и отправляет в VK."""
        if not text:
            return
        try:
            if hasattr(self.vk, 'send_big'):
                self.vk.send_big(peer_id, text)
                return
        except Exception:
            pass
        max_chunk = 3800
        chunks = [text[i:i + max_chunk] for i in range(0, len(text), max_chunk)]
        for ch in chunks:
            try:
                self.vk.send(peer_id, ch)
            except Exception:
                print(f"[CMD] Failed to send chunk to {peer_id}")

# --- конец файла ---
