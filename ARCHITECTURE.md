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
- created_at: datetime
```

### Record
```python
- id: int (PK)
- user_id: int (FK → User.id, indexed)
- operation: str ("+" или "-")
- amount: Decimal(10, 2)
- category: str (max 50 символов)
- created_at: datetime (Moscow TZ, indexed)
```

### Индексы БД
```python
- ix_records_user_created    # (user_id, created_at) — выборка по периоду
- ix_records_user_operation  # (user_id, operation) — отчёты по типу
- ix_records_user_op_cat     # (user_id, operation, category) — GROUP BY категориям
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
waiting_for_custom_period   — ввод своего периода текстом
waiting_for_report_type     — выбор типа отчёта (Доход/Расход)
waiting_for_report_year     — выбор года для отчёта
waiting_for_report_month    — выбор месяца для отчёта
waiting_for_delete_period   — выбор периода для удаления
waiting_for_delete_record   — выбор записи для удаления
waiting_for_delete_confirm  — подтверждение удаления
```

## Основные потоки

### 1. Регистрация пользователя
```
/start → handle_start() → set_user() → показ главного меню
```

### 2. Добавление записи
```
[Доход] или [Расход]
    → handle_income_expense()
    → FSM: waiting_for_amount
    → handle_amount_and_category()
    → format_added_records_response() — формирование ответа
    → add_record() → БД
```

### 3. Быстрый ввод (без кнопки)
```
"+5000 зарплата" или "-200 кофе"
    → handle_direct_record()
    → parse_record_line() — парсинг строки
    → add_record() → БД
```

### 4. Просмотр истории (с пагинацией)
```
[История]
    → menu_history()
    → выбор периода (сегодня/вчера/7 дней/30 дней/месяц/год/свой)
    → menu_history_period()
    → get_history_data() — один запрос (count + totals + records)
    → build_history_page() — формирует текст + кнопки навигации
    → [◀ Назад] [1/5] [Вперёд ▶] [Показать все]
```

### 5. Построение отчёта
```
[Отчёт]
    → menu_report()
    → выбор типа (Доход/Расход)
    → выбор года → выбор месяца
    → menu_report_month()
    → get_categories_summary() — SQL GROUP BY
    → build_report_pie() — генерация PNG графика
    → make_report_text() — формирование caption
    → отправка фото с отчётом
```

### 6. Удаление записи (с подтверждением)
```
[Удалить запись]
    → menu_delete()
    → выбор периода
    → menu_delete_period()
    → build_delete_keyboard() — список записей с кнопками
    → menu_delete_record() — выбор записи
    → confirm_delete_keyboard() — подтверждение
    → menu_delete_confirm() — удаление + обновление списка
```

## Оптимизации

### SQL-запросы
- **CASE WHEN агрегация** — подсчёт доходов и расходов одним запросом
- **get_history_data()** — комбинированный запрос (count + totals + records)
- **get_categories_summary()** — SQL GROUP BY вместо Python-агрегации
- **Индексы** — ускорение выборки по периодам и категориям

### Кэширование
- **@lru_cache** на статичных клавиатурах (main_menu, period keyboards)
- **years_months в state** — не запрашивать повторно при выборе отчёта

### Производительность
- **ORM-объекты напрямую** — без преобразования в dict для истории
- **ThreadPoolExecutor** — для CPU-bound задач (графики)
- **Таймаут графиков** — 10 секунд, защита от зависания

## Ключевые решения

### Таймзона
Везде используется `Europe/Moscow`:
```python
datetime.now(ZoneInfo("Europe/Moscow"))
```

### Пагинация
- `RECORDS_PER_PAGE = 15` — записей на странице
- `MAX_SHOW_ALL_RECORDS = 50` — лимит для "Показать все"
- Записи загружаются из БД при переходе между страницами (не хранятся в state)

### Rate Limiting
- `RateLimiter` в utils.py — 20 запросов в минуту на пользователя
- Автоочистка неактивных пользователей каждые 5 минут
- Подключается как middleware в bot.py

### Лимиты Telegram
- `MAX_CAPTION_LENGTH = 1024` — автообрезка caption фото
- Проверка длины сообщения истории (4000 символов)

### Категории в графике
Если категорий больше 7, остальные объединяются в "Прочее" (`MAX_CATEGORIES_IN_PIE`).

## Обработка ошибок

### Декоратор @log_exceptions
```python
@log_exceptions("Текст ошибки для пользователя")
async def handler(...):
    ...
```
- Логирует исключение с user_id
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
- Валидация категории: max 50 символов
- Удаление только своих записей (проверка user_id)

## Конфигурация

| Параметр | Где | Значение |
|----------|-----|----------|
| BOT_TOKEN | .env | Токен от @BotFather |
| RECORDS_PER_PAGE | handlers.py | 15 |
| MAX_SHOW_ALL_RECORDS | handlers.py | 50 |
| MAX_CATEGORY_LENGTH | handlers.py | 50 |
| MAX_AMOUNT | handlers.py | 1,000,000 |
| MAX_CATEGORIES_IN_PIE | utils.py | 7 |
| MAX_CAPTION_LENGTH | utils.py | 1024 |
| CHART_TIMEOUT_SECONDS | utils.py | 10 |
| CHART_DPI | utils.py | 150 |
| Rate limit | utils.py | 20 req/min |

## Тестирование

```bash
pytest tests/ -v
```

Покрытие:
- `test_utils.py` — build_report_pie, format_money
- `test_keyboards.py` — клавиатуры
- `test_db.py`, `test_db_extended.py` — CRUD операции
