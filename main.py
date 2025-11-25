# main.py
import sys
import time
import threading
from bot.vk_bot import VKBot
from bot.forum_tracker import ForumTracker
from config import VK_TOKEN, FA_COOKIE
from colorama import Fore, Style, init

init(autoreset=True)


def banner():
    print(Fore.CYAN + """
 ███╗   ███╗ █████╗ ████████╗██████╗ ██████╗ 
 ████╗ ████║██╔══██╗╚══██╔══╝██╔══██╗██╔══██╗
 ██╔████╔██║███████║   ██║   ██████╔╝██████╔╝
 ██║╚██╔╝██║██╔══██║   ██║   ██╔═══╝ ██╔══██╗
 ██║ ╚═╝ ██║██║  ██║   ██║   ██║     ██║  ██║
 ╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚═╝  ╚═╝

         MATRP FORUM TRACKER — VK EDITION
    """ + Style.RESET_ALL)

    print(
        Fore.MAGENTA + "─────────────────────────────────────────────" + Style.RESET_ALL
    )
    print(Fore.GREEN + "   🔗 VK Longpoll Bot запущен" + Style.RESET_ALL)
    print(Fore.YELLOW + "   🛰  Отслеживание форума MatRP" + Style.RESET_ALL)
    print(Fore.CYAN + "   ✉  Ответы с VK прямо в тему" + Style.RESET_ALL)
    print(Fore.MAGENTA + "─────────────────────────────────────────────" + Style.RESET_ALL)
    print()


def check_config():
    missing = []
    if not VK_TOKEN: missing.append("VK_TOKEN")
    if not FA_COOKIE: missing.append("FA_COOKIE")

    if missing:
        print(Fore.RED + "❌ В config.py отсутствуют параметры:" + Style.RESET_ALL)
        for m in missing:
            print(Fore.YELLOW + f" → {m}" + Style.RESET_ALL)

        print(Fore.CYAN + "\nЗаполни config.py и перезапусти бота.\n" + Style.RESET_ALL)
        sys.exit(1)


def run():
    banner()
    check_config()

    print(Fore.CYAN + "[INIT] Инициализация VK бота..." + Style.RESET_ALL)
    vk = VKBot(VK_TOKEN)

    print(Fore.CYAN + "[INIT] Инициализация форум-трекера..." + Style.RESET_ALL)
    tracker = ForumTracker(FA_COOKIE, vk)

    print(Fore.GREEN + "\n✔ Всё готово! Бот работает.\n" + Style.RESET_ALL)

    # VK
    threading.Thread(target=vk.longpoll_loop, daemon=True).start()

    # FORUM TRACKER LOOP
    threading.Thread(target=tracker.loop, daemon=True).start()

    # Держим процесс активным
    while True:
        time.sleep(3)


if __name__ == "__main__":
    run()
