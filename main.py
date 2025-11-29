
import sys
import time
import threading
from colorama import Fore, Style, init

from config import (
    VK_TOKEN,
    XF_USER,
    XF_TFA_TRUST,
    XF_SESSION,
    XF_CSRF          
)

from bot.vk_bot import VKBot
from bot.forum_tracker import ForumTracker
from bot.forum_tracker import stay_online_loop

init(autoreset=True)

BOT_VERSION = "2.3.1"
AUTHOR = "Создатель: 4ikatilo"
AUTHOR_TG = "Telegram: @c4ikatillo"
AUTHOR_VK = "VK: https://vk.com/ashot.nageroine"



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
    print(Fore.GREEN   + f" 🔥 Версия бота: {BOT_VERSION}" + Style.RESET_ALL)
    print(Fore.CYAN    + f" 👤 {AUTHOR}" + Style.RESET_ALL)
    print(Fore.YELLOW  + f" 💬 {AUTHOR_TG}" + Style.RESET_ALL)
    print(Fore.BLUE    + f" 🌐 {AUTHOR_VK}" + Style.RESET_ALL)
    print(Fore.MAGENTA + "──────────────────────────────────────────────────────────" + Style.RESET_ALL)

    print(Fore.GREEN   + " 🔗 VK Longpoll Bot подключается..." + Style.RESET_ALL)
    print(Fore.CYAN    + " 🛰 Отслеживание форума MatRP активно" + Style.RESET_ALL)
    print(Fore.YELLOW  + " ✉ Ответы с VK прямо в темы форума" + Style.RESET_ALL)
    print(Fore.MAGENTA + "──────────────────────────────────────────────────────────\n" + Style.RESET_ALL)


def check_config():
    missing = []

    if not VK_TOKEN:     missing.append("VK_TOKEN")
    if not XF_USER:      missing.append("XF_USER")
    if not XF_TFA_TRUST: missing.append("XF_TFA_TRUST")
    if not XF_SESSION:   missing.append("XF_SESSION")
    if not XF_CSRF:      missing.append("XF_CSRF")   

    if missing:
        print(Fore.RED + "❌ В config.py отсутствуют параметры:" + Style.RESET_ALL)
        for m in missing:
            print(Fore.YELLOW + f" → {m}" + Style.RESET_ALL)

        print(Fore.CYAN + "\nЗаполни config.py и запусти бота снова.\n" + Style.RESET_ALL)
        sys.exit(1)


def run():
    banner()
    check_config()

    print(Fore.CYAN + "[INIT] Инициализация VK бота..." + Style.RESET_ALL)
    vk = VKBot()

    print(Fore.CYAN + "[INIT] Инициализация форум-трекера..." + Style.RESET_ALL)
    tracker = ForumTracker(
        XF_USER,
        XF_TFA_TRUST,
        XF_SESSION,
        vk,
        XF_CSRF        
    )

    print(Fore.GREEN + "\n✔ Всё готово! Бот запущен и работает.\n" + Style.RESET_ALL)

 
    vk.start()
    tracker.start()

    threading.Thread(target=stay_online_loop, daemon=True).start()


    while True:
        time.sleep(3)


if __name__ == "__main__":
    run()
