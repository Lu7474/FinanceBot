import asyncio
import logging

from aiogram import Bot, Dispatcher

from core.handlers import router
from core.database.models import async_main
from config import BOT_TOKEN


async def main():
    await async_main()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    try:
        logging.basicConfig(level=logging.INFO)
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot off")
