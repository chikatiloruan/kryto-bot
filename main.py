import sys
import os
import time
import threading
import requests
from colorama import Fore, Style, init

from config import (
    VK_TOKEN,
    XF_USER,
    XF_TFA_TRUST,
    XF_SESSION,
    XF_CSRF
)

from bot.vk_bot import VKBot
from bot.forum_tracker import ForumTracker, stay_online_loop

init(autoreset=True)

# ============================================================
# INFO
# ============================================================

BOT_VERSION = "2.3.1"
AUTHOR = "Создатель: 4ikatilo"
AUTHOR_TG = "Telegram: @c4ikatillo"
AUTHOR_VK = "VK: https://vk.com/ashot.nageroine"

FORUM_BASE = "https://forum.matrp.ru"

# ============================================================
# UTILS
# ============================================================

def clear_console():
    os.system("cls" if os.name == "nt" else "clear")


# ============================================================
# SKULL ASCII ANIMATION
# ============================================================

def skull_animation():
    frames = [
r"""
        .
       / \
      |   |
      |   |
      |___|
     /_____\
""",
r"""
        .
       / \
      | ☠ |
      |   |
      |___|
     /_____\
""",
r"""
        .
       / \
      | ☠ |
      | ☠ |
      |___|
     /_____\
"""
    ]

    clear_console()
    for _ in range(2):
        for f in frames:
            clear_console()
            print(Fore.RED + f + Style.RESET_ALL)
            print(Fore.MAGENTA + " MATRP FORUM TRACKER LOADING...\n" + Style.RESET_ALL)
            time.sleep(0.45)


# ============================================================
# STATUS CHECKS
# ============================================================

def check_vk_status():
    try:
        r = requests.get("https://api.vk.com", timeout=5)
        return r.status_code == 200
    except:
        return False


def check_forum_status():
    try:
        r = requests.get(FORUM_BASE, timeout=5)
        return r.status_code == 200
    except:
        return False


# ============================================================
# CONFIG CHECK
# ============================================================

def check_config():
    missing = []

    if not VK_TOKEN:     missing.append("VK_TOKEN")
    if not XF_USER:      missing.append("XF_USER")
    if not XF_TFA_TRUST: missing.append("XF_TFA_TRUST")
    if not XF_SESSION:   missing.append("XF_SESSION")
    if not XF_CSRF:      missing.append("XF_CSRF")

    if missing:
        clear_console()
        print(Fore.RED + "❌ В config.py отсутствуют параметры:\n" + Style.RESET_ALL)
        for m in missing:
            print(Fore.YELLOW + f" → {m}" + Style.RESET_ALL)

        print(Fore.CYAN + "\nЗаполни config.py и запусти бота снова.\n" + Style.RESET_ALL)
        sys.exit(1)


# ============================================================
# LOADER
# ============================================================

def fake_loader():
    skull_animation()

    print(Fore.CYAN + "Инициализация системы...\n" + Style.RESET_ALL)
    time.sleep(0.5)

    steps = [
        ("Проверка конфигурации", True),
        ("Загрузка модулей", True),
        ("Подключение VK API", check_vk_status()),
        ("Подключение форума MatRP", check_forum_status()),
        ("Инициализация Forum Tracker", True),
        ("Запуск сервисов", True),
    ]

    for name, status in steps:
        color = Fore.GREEN if status else Fore.RED
        state = "ONLINE" if status else "OFFLINE"

        print(f"{Fore.YELLOW}[...] {name}{Style.RESET_ALL}", end="")
        time.sleep(0.5)
        print(f" {color}{state}{Style.RESET_ALL}")
        time.sleep(0.25)

    time.sleep(1)


# ============================================================
# BANNER
# ============================================================

def banner():
    print(Fore.CYAN + r"""
 ███╗   ███╗ █████╗ ████████╗██████╗ ██████╗ 
 ████╗ ████║██╔══██╗╚══██╔══╝██╔══██╗██╔══██╗
 ██╔████╔██║███████║   ██║   ██████╔╝██████╔╝
 ██║╚██╔╝██║██╔══██║   ██║   ██╔═══╝ ██╔══██╗
 ██║ ╚═╝ ██║██║  ██║   ██║   ██║     ██║  ██║
 ╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚═╝  ╚═╝

      MATRP FORUM TRACKER — VK EDITION
""" + Style.RESET_ALL)

    print(Fore.MAGENTA + "──────────────────────────────────────────────────────────" + Style.RESET_ALL)
    print(Fore.GREEN  + f" 🔥 Версия: {BOT_VERSION}" + Style.RESET_ALL)
    print(Fore.CYAN   + f" 👤 {AUTHOR}" + Style.RESET_ALL)
    print(Fore.YELLOW + f" 💬 {AUTHOR_TG}" + Style.RESET_ALL)
    print(Fore.BLUE   + f" 🌐 {AUTHOR_VK}" + Style.RESET_ALL)
    print(Fore.MAGENTA + "──────────────────────────────────────────────────────────\n" + Style.RESET_ALL)

    print(Fore.GREEN + "✅ VK Bot: ONLINE" + Style.RESET_ALL)
    print(Fore.GREEN + "✅ Forum Tracker: ONLINE" + Style.RESET_ALL)
    print(Fore.CYAN  + "\nБот работает. Ожидание событий...\n" + Style.RESET_ALL)


# ============================================================
# RUN
# ============================================================

def run():
    check_config()
    fake_loader()
    clear_console()
    banner()

    vk = VKBot()
    tracker = ForumTracker(
        XF_USER,
        XF_TFA_TRUST,
        XF_SESSION,
        vk
    )

    vk.start()
    tracker.start()

    threading.Thread(target=stay_online_loop, daemon=True).start()

    while True:
        time.sleep(3)


if __name__ == "__main__":
    run()
