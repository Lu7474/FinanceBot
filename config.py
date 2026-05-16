import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
# Прокси для обхода блокировок (опционально, None если не нужен)
PROXY_URL = os.getenv("PROXY_URL")

# URL базы данных (по умолчанию SQLite в корне проекта)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///db.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError(
        "Переменная окружения BOT_TOKEN не установлена. Укажите её в .env файле."
    )

# ==================== Константы приложения ====================

# Пагинация
RECORDS_PER_PAGE = 15              # Записей на странице истории/удаления
MAX_SHOW_ALL_RECORDS = 50          # Лимит для "Показать все"

# Ограничения ввода
MAX_CATEGORY_LENGTH = 50           # Макс. длина категории (соответствует БД)
MAX_ACCOUNT_NAME_LENGTH = 40       # Макс. длина названия счёта
MAX_AMOUNT = 1_000_000             # Макс. сумма одной операции
MAX_GOAL_AMOUNT = 10_000_000       # Макс. сумма цели
MAX_GOAL_NAME_LENGTH = 100         # Макс. длина названия цели

# Графики
MAX_CATEGORIES_IN_PIE = 5          # Лимит категорий на графике (остальное — "Прочее")
CHART_TIMEOUT_SECONDS = 10         # Таймаут генерации графика (сек)
CHART_DPI = 150                    # DPI графика (150 достаточно для Telegram)

# Telegram лимиты
MAX_CAPTION_LENGTH = 1024          # Лимит символов caption в Telegram
MAX_MESSAGE_LENGTH = 4096          # Лимит символов сообщения в Telegram

# Часовой пояс
TIMEZONE = "Europe/Moscow"         # Timezone для всех дат в приложении
