import asyncio
import logging
from logging.handlers import RotatingFileHandler

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
        # Логирование в файл с ротацией и в консоль
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                RotatingFileHandler(
                    "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
                ),
            ],
        )
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot off")
