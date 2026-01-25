# Архитектура FinanceBot

Telegram-бот для учёта личных финансов.

## Структура проекта

```
FinanceBot/
├── bot.py                      # Точка входа, настройка Dispatcher
├── config.py                   # Конфигурация (BOT_TOKEN из .env)
├── core/
│   ├── handlers.py             # Обработчики команд и callback (FSM)
│   ├── keyboards.py            # Клавиатуры (ReplyKeyboard, InlineKeyboard)
│   ├── utils.py                # Утилиты, RateLimiter, построение графиков
│   └── database/
│       ├── models.py           # SQLAlchemy модели (User, Record)
│       └── requests.py         # CRUD операции с БД
├── tests/                      # Pytest тесты
└── requirements.txt            # Зависимости
```

## Технологии

| Компонент | Технология |
|-----------|------------|
| Фреймворк бота | aiogram 3.x |
| База данных | SQLite + SQLAlchemy 2.x (async) |
| Графики | matplotlib |
| Тесты | pytest + pytest-asyncio |

## Модели данных

### User
```python
- id: int (PK)
- tg_id: int (Telegram user ID, unique)
- name: str
- phone: str (optional)
```

### Record
```python
- id: int (PK)
- user_id: int (FK → User.id)
- operation: str ("+" или "-")
- amount: Decimal(10, 2)
- category: str
- created_at: datetime (UTC, автоматически)
```

## FSM (Finite State Machine)

Бот использует состояния для многошаговых операций:

### AddRecord
```
waiting_for_amount — ожидание ввода суммы и категории
```

### MenuStates
```
waiting_for_history_period  — выбор периода для истории
waiting_for_history_page    — навигация по страницам истории
waiting_for_report_type     — выбор типа отчёта (Доход/Расход)
waiting_for_report_year     — выбор года для отчёта
waiting_for_report_month    — выбор месяца для отчёта
waiting_for_delete_period   — выбор периода для удаления
waiting_for_delete_record   — выбор записи для удаления
```

## Основные потоки

### 1. Регистрация пользователя
```
/start → handle_start() → set_user() → показ главного меню
```

### 2. Добавление записи
```
[➕ Доход] или [➖ Расход]
    → handle_income_expense()
    → FSM: waiting_for_amount
    → handle_amount_and_category()
    → add_record() → БД
```

### 3. Просмотр истории (с пагинацией)
```
[🕘 История]
    → menu_history()
    → выбор периода (день/месяц/год)
    → menu_history_period()
    → build_history_page() — формирует текст + кнопки навигации
    → [◀ Назад] [1/5] [Вперёд ▶]
```

### 4. Построение отчёта
```
[📊 Отчёт]
    → menu_report()
    → выбор типа (Доход/Расход)
    → выбор года → выбор месяца
    → menu_report_month()
    → build_report_pie() — генерация PNG графика
    → отправка фото + текстовый отчёт
```

### 5. Удаление записи (с пагинацией)
```
[🗑️ Удалить запись]
    → menu_delete()
    → выбор периода
    → menu_delete_period()
    → build_delete_keyboard() — список записей с кнопками
    → menu_delete_record() — удаление + обновление списка
```

## Ключевые решения

### Таймзона
Везде используется `Europe/Moscow`:
```python
datetime.now(ZoneInfo("Europe/Moscow"))
```

### Пагинация
Константа `RECORDS_PER_PAGE = 10` в handlers.py.
Записи сохраняются в FSM state как `list[dict]` (не ORM-объекты), чтобы не держать сессию БД открытой.

### Rate Limiting
`RateLimiter` в utils.py — 20 запросов в минуту на пользователя.
Подключается как middleware в bot.py.

### Таймаут графиков
`build_report_pie()` — async функция с таймаутом 10 секунд.
Matplotlib выполняется в ThreadPoolExecutor.

### Категории в графике
Если категорий больше 7, остальные объединяются в "Прочее" (`MAX_CATEGORIES_IN_PIE`).

## Обработка ошибок

### Декоратор @log_exceptions
```python
@log_exceptions("Текст ошибки для пользователя")
async def handler(...):
    ...
```
- Логирует исключение
- Показывает пользователю сообщение об ошибке
- Очищает FSM state

### Валидация callback_data
Все callback-обработчики оборачивают парсинг в try/except:
```python
try:
    record_id = int(callback.data.split(":")[1])
except (IndexError, ValueError, AttributeError):
    await callback.answer("Некорректные данные.")
    return
```

## Безопасность

- Токен бота в `.env` (не в коде)
- Rate limiting защищает от спама
- Валидация суммы: 0 < amount ≤ 1,000,000
- Удаление только своих записей (проверка user_id)

## Конфигурация

| Параметр | Где | Значение |
|----------|-----|----------|
| BOT_TOKEN | .env | Токен от @BotFather |
| RECORDS_PER_PAGE | handlers.py | 10 |
| MAX_CATEGORIES_IN_PIE | utils.py | 7 |
| CHART_TIMEOUT_SECONDS | utils.py | 10 |
| Rate limit | utils.py | 20 req/min |

## Тестирование

```bash
pytest tests/ -v
```

Покрытие:
- `test_utils.py` — parse_date, build_report_pie, make_history_text
- `test_keyboards.py` — клавиатуры
- `test_db.py`, `test_db_extended.py` — CRUD операции
