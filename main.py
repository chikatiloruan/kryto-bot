import asyncio
from bot.vk_bot import VKBot
from bot.forum_tracker import ForumTracker
import os

async def main():
    vk = VKBot()
    tracker = ForumTracker(vk)

    # старт vk longpoll (в отдельном потоке внутри класса)
    await vk.start()
    # старт трекера (он запускает свои задачи)
    asyncio.create_task(tracker.start_loop())

    print("Bot started. Running forever...")
    # keep main alive
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())

