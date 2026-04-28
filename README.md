# FinanceBot

[![Tests](https://github.com/Lu7474/FinanceBot/actions/workflows/tests.yml/badge.svg)](https://github.com/Lu7474/FinanceBot/actions/workflows/tests.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Telegram-бот для учёта личных финансов на aiogram 3 + SQLAlchemy 2.0 async. Записи через текст без кнопок, история за любой период, графики по категориям. 62 теста, модульная архитектура.

## Стек

| | |
|---|---|
| Язык | Python 3.11+ |
| Фреймворк | aiogram 3 |
| ORM / БД | SQLAlchemy 2.0 async + aiosqlite |
| Графики | matplotlib |
| Тесты | pytest + pytest-asyncio |

## Возможности

- `+5000 зарплата` или `-200 кофе` — запись без кнопок, несколько строк сразу
- Несколько счетов с выбором при вводе
- История за 8 периодов + произвольный диапазон дат, постраничная навигация
- Столбчатые диаграммы по категориям, топ-5 + «Прочее»
- Динамика доходов и расходов по месяцам за выбранный год
- Многошаговые сценарии через aiogram FSM

## Команды

| Команда | Действие |
|---|---|
| `/start` | Главное меню |
| `/help` | Справка |
| `/cancel` | Отмена текущего действия |

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env && python bot.py
```

> Токен получить у [@BotFather](https://t.me/BotFather), вставить в `.env`.

## Скриншоты

<!-- TODO: добавить скриншоты -->
| Главное меню | История | График отчёта |
|---|---|---|
| _скоро_ | _скоро_ | _скоро_ |
