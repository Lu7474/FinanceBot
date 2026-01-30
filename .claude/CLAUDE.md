# FinanceBot

Telegram бот для учёта личных финансов на aiogram 3.x.

## Стек
- Python 3.11+
- aiogram 3.20
- SQLAlchemy 2.0 + aiosqlite
- matplotlib для графиков
- pytest + pytest-asyncio для тестов

## Структура
- `bot.py` — точка входа
- `config.py` — конфигурация
- `core/` — основная логика (handlers, keyboards, utils)
- `tests/` — тесты

## Стиль кода
- Следуй PEP8
- Используй type hints
- Async/await везде
- Docstrings на русском языке

## Команды
- Запуск: `python bot.py`
- Тесты: `./env/Scripts/python.exe -m pytest`
- Линтер: `ruff check .`

## Важно
- Не читай .env файлы
- База данных: SQLite (db.sqlite3)
- Все тексты бота на русском языке
