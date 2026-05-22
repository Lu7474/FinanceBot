"""
Точка входа бота. Инициализация, настройка middleware, запуск polling.
"""

import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from config import BOT_API_BASE_URL, BOT_TOKEN, PROXY_URL
from core.charts import shutdown_executor
from core.database.models import async_main, async_session, engine
from core.handlers import router
from core.middleware import RateLimitMiddleware, UserMiddleware
from core.scheduler import setup_scheduler


async def main():
    # Создаём таблицы в БД (если не существуют)
    await async_main()

    # Nginx reverse proxy → Telegram API (приоритет над SOCKS)
    if BOT_API_BASE_URL:
        session = AiohttpSession(api=TelegramAPIServer.from_base(BOT_API_BASE_URL))
    elif PROXY_URL:
        session = AiohttpSession(proxy=PROXY_URL)
    else:
        session = None

    # Инициализация бота и диспетчера
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher()

    # Защита от спама: 60 запросов/мин на пользователя
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())

    # Кэширование user_id для всех хендлеров
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())

    # Подключаем обработчики
    dp.include_router(router)

    # Запускаем планировщик уведомлений
    scheduler = setup_scheduler(bot, async_session)

    try:
        # Запуск бота (skip_updates=True — игнорируем старые сообщения)
        await dp.start_polling(bot, skip_updates=True)
    finally:
        # Graceful shutdown: закрываем все ресурсы
        logging.info("Завершение работы бота...")

        # Останавливаем планировщик (синхронный метод)
        scheduler.shutdown()

        # Закрываем сессию бота
        if session:
            await session.close()

        # Закрываем соединение с БД
        await engine.dispose()

        # Останавливаем executor для графиков
        shutdown_executor()

        logging.info("Бот остановлен.")


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
