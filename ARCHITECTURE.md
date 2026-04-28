# Архитектура FinanceBot

Telegram-бот для учёта личных финансов.

## Структура проекта

```
FinanceBot/
├── bot.py                      # Точка входа, настройка Dispatcher
├── config.py                   # Константы и BOT_TOKEN из .env
├── core/
│   ├── utils.py                # format_money, log_exceptions, RU_MONTHS
│   ├── charts.py               # Генерация PNG-графиков (matplotlib)
│   ├── reports.py              # Построение текста отчётов
│   ├── middleware.py           # RateLimitMiddleware, UserMiddleware
│   ├── keyboards.py            # ReplyKeyboard и InlineKeyboard
│   ├── handlers/
│   │   ├── __init__.py         # Сборка всех роутеров в один Router
│   │   ├── common.py           # FSM states, фильтры кнопок, get_user_id_from_event
│   │   ├── menu.py             # /start, /help, /cancel
│   │   ├── records.py          # Добавление записей (кнопка + быстрый ввод)
│   │   ├── history.py          # История с пагинацией
│   │   ├── reports.py          # Отчёты и сравнение периодов
│   │   ├── delete.py           # Удаление записей с подтверждением
│   │   ├── accounts.py         # Управление счетами
│   │   └── fallback.py         # Fallback для неизвестных сообщений
│   └── database/
│       ├── models.py           # SQLAlchemy модели: User, Account, Record
│       └── requests.py         # CRUD-операции с БД
├── tests/                      # 62 pytest-теста
└── requirements.txt
```

## Технологии

| Компонент | Технология |
|---|---|
| Фреймворк бота | aiogram 3 |
| База данных | SQLite + SQLAlchemy 2.0 (async) |
| Графики | matplotlib |
| Тесты | pytest + pytest-asyncio |

## Модели данных

### User
```
id: int (PK)
tg_id: int (BigInteger, unique)
name: str
phone: str (nullable)
created_at: datetime (Moscow TZ)
```

### Account
```
id: int (PK)
user_id: int (FK → User.id)
name: str (max 40 символов)
balance_offset: Decimal(10, 2)  — сдвиг баланса для ручной установки
created_at: datetime
```

### Record
```
id: int (PK)
user_id: int (FK → User.id)
account_id: int (FK → Account.id, nullable, SET NULL при удалении счёта)
operation: str  — "+" (доход) или "-" (расход)
amount: Decimal(10, 2)
category: str (max 50 символов)
created_at: datetime (Moscow TZ)
```

### Индексы
```
ix_records_user_created    — (user_id, created_at)          выборка по периоду
ix_records_user_operation  — (user_id, operation)           отчёты по типу
ix_records_user_op_cat     — (user_id, operation, category) GROUP BY категориям
```

## FSM

### AddRecord
```
waiting_for_amount   — ввод суммы и категории
waiting_for_account  — выбор счёта (если их несколько)
```

### MenuStates
```
waiting_for_history_period
waiting_for_history_page
waiting_for_custom_period
waiting_for_report_type
waiting_for_report_year
waiting_for_report_month
waiting_for_delete_period
waiting_for_delete_record
waiting_for_delete_confirm
```

### AccountStates
```
waiting_for_account_name
waiting_for_rename_name
waiting_for_transfer_amount
waiting_for_set_balance
waiting_for_acc_hist_period
waiting_for_acc_hist_page
```

## Middleware

### UserMiddleware
Получает внутренний `user_id` из БД один раз и передаёт в хендлеры через `data["user_id"]`.
Хендлеры читают `kwargs.get("user_id")` — без лишних запросов к БД.

### RateLimitMiddleware
60 запросов/мин на пользователя. Авточистка неактивных пользователей каждые 5 минут.

## Основные потоки

### Добавление записи (кнопка)
```
[Доход] / [Расход]
    → FSM: waiting_for_amount
    → parse_record_line()
    → если счетов > 1 → FSM: waiting_for_account
    → add_record() → БД
```

### Быстрый ввод
```
"+5000 зарплата" / "-200 кофе"
    → handle_direct_record()
    → parse_record_line()
    → если счетов > 1 → выбор счёта
    → add_record() → БД
```

### История
```
[История]
    → выбор периода
    → get_history_data()  — count + totals + records за один запрос
    → build_history_page() — текст + инлайн-навигация
    → пагинация: ◀ [1/5] ▶
```

### Отчёт
```
[Отчёт] → тип → год → месяц
    → get_categories_summary()  — SQL GROUP BY
    → build_report_chart()      — PNG (ThreadPoolExecutor, таймаут 10 с)
    → отправка фото + caption
```

### Сравнение периодов
```
[Сравнить периоды]
    → get_monthly_totals()   — итоги по месяцам за год
    → build_trend_chart()    — PNG тренда
```

### Управление счетами
```
[Счета]
    → get_account_balances() — балансы всех счетов
    → создать / переименовать / удалить (с переносом записей)
    → перевод между счетами
    → установить баланс (через balance_offset)
    → история конкретного счёта (с пагинацией)
```

## Оптимизации

- **CASE WHEN** — доходы и расходы считаются одним запросом
- **get_history_data()** — count + totals + records за один round-trip
- **Составные индексы** — ускорение выборки по периодам и категориям
- **@lru_cache** — статичные клавиатуры (главное меню, периоды)
- **ThreadPoolExecutor** — генерация графиков вне event loop

## Конфигурация (`config.py`)

| Параметр | Значение |
|---|---|
| RECORDS_PER_PAGE | 15 |
| MAX_SHOW_ALL_RECORDS | 50 |
| MAX_CATEGORY_LENGTH | 50 |
| MAX_ACCOUNT_NAME_LENGTH | 40 |
| MAX_AMOUNT | 1 000 000 |
| MAX_CATEGORIES_IN_PIE | 5 |
| MAX_CAPTION_LENGTH | 1024 |
| MAX_MESSAGE_LENGTH | 4096 |
| CHART_TIMEOUT_SECONDS | 10 |
| CHART_DPI | 150 |
| TIMEZONE | Europe/Moscow |

## Тесты

```bash
pytest tests/ -v
```

| Файл | Что покрывает |
|---|---|
| test_db.py | CRUD-операции |
| test_db_extended.py | Граничные случаи БД |
| test_accounts.py | Счета, переводы, балансы |
| test_utils.py | format_money, графики |
| test_keyboards.py | Клавиатуры |
| test_parse_record.py | Парсинг быстрого ввода |
