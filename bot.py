"""
Точка входа бота. Инициализация, настройка middleware, запуск polling.
"""
import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from core.handlers import router
from core.database.models import async_main
from core.utils import RateLimitMiddleware
from config import BOT_TOKEN


async def main():
    # Создаём таблицы в БД (если не существуют)
    await async_main()

    # Прокси для обхода блокировок (убрать, если не нужен)
    proxy_url = "socks5://127.0.0.1:12334"
    session = AiohttpSession(proxy=proxy_url)

    # Инициализация бота и диспетчера
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher()

    # Защита от спама: 20 запросов/мин на пользователя
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())

    # Подключаем обработчики
    dp.include_router(router)

    # Запуск бота (skip_updates=True — игнорируем старые сообщения)
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    try:
        # Настройка логов: консоль + файл с ротацией (макс 1MB, 3 бэкапа)
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
