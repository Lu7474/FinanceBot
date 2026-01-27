"""
Точка входа бота. Инициализация, настройка middleware, запуск polling.
"""
import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from core.handlers import router
from core.database.models import async_main, engine
from core.utils import RateLimitMiddleware, shutdown_executor
from config import BOT_TOKEN, PROXY_URL


async def main():
    # Создаём таблицы в БД (если не существуют)
    await async_main()

    # Прокси для обхода блокировок (опционально)
    session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None

    # Инициализация бота и диспетчера
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher()

    # Защита от спама: 20 запросов/мин на пользователя
    dp.message.middleware(RateLimitMiddleware())
    dp.callback_query.middleware(RateLimitMiddleware())

    # Подключаем обработчики
    dp.include_router(router)

    try:
        # Запуск бота (skip_updates=True — игнорируем старые сообщения)
        await dp.start_polling(bot, skip_updates=True)
    finally:
        # Graceful shutdown: закрываем все ресурсы
        logging.info("Завершение работы бота...")

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
