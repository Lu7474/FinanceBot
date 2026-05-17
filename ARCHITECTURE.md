# Архитектура FinanceBot

Telegram-бот для учёта личных финансов.

## Структура проекта

```
FinanceBot/
├── bot.py                      # Точка входа, настройка Dispatcher
├── config.py                   # Константы и BOT_TOKEN из .env
├── core/
│   ├── utils.py                # format_money, log_exceptions, RU_MONTHS, SYSTEM_KEYWORDS
│   ├── charts.py               # Генерация PNG-графиков (matplotlib)
│   ├── reports.py              # Построение текста отчётов
│   ├── middleware.py           # RateLimitMiddleware, UserMiddleware
│   ├── keyboards.py            # ReplyKeyboard и InlineKeyboard
│   ├── export.py               # Excel export/import/backup: build и validate функции
│   ├── handlers/
│   │   ├── __init__.py         # Сборка всех роутеров в один Router
│   │   ├── common.py           # FSM states, фильтры кнопок, get_user_id_from_event
│   │   ├── menu.py             # /start, /help, /cancel
│   │   ├── records.py          # Добавление записей (кнопка + быстрый ввод)
│   │   ├── history.py          # История с пагинацией
│   │   ├── reports.py          # Отчёты и сравнение периодов
│   │   ├── delete.py           # Удаление записей с подтверждением
│   │   ├── accounts.py         # Управление счетами
│   │   ├── savings.py          # Накопления: снимки баланса
│   │   ├── records_edit.py     # Редактирование отдельных записей из истории
│   │   ├── categories.py       # Пользовательские категории + суггестия при вводе
│   │   ├── budgets.py          # Месячные бюджеты по категориям
│   │   ├── export_import.py    # Экспорт/импорт/бэкап в Excel
│   │   ├── goals.py            # Финансовые цели: CRUD, пополнение, снятие
│   │   ├── admin.py            # Режим администратора
│   │   └── fallback.py         # Fallback для неизвестных сообщений
│   └── database/
│       ├── models.py           # SQLAlchemy модели + миграции (_migrate)
│       └── requests/           # CRUD по доменам (re-export через __init__.py)
│           ├── _common.py      # apply_period_filter, SYSTEM_CATEGORIES, лимиты
│           ├── users.py        # get_user_by_tg_id, set_user
│           ├── records.py      # add/get/update/delete_record, totals, duplicate-check
│           ├── reports.py      # categories_summary, history_data, monthly/weekday/search
│           ├── accounts.py     # CRUD счетов, балансы, переводы
│           ├── categories.py   # UserCategory + suggest/learn keywords + seed_defaults
│           ├── savings.py      # snapshots, items, wealth-items
│           ├── budgets.py      # CRUD бюджетов, alert-логика, сброс флагов
│           ├── goals.py        # CRUD целей, deposit/withdraw, complete
│           ├── admin.py        # admin-выборки, ban, cascade-delete пользователя
│           └── backup.py       # выборки и bulk-insert для экспорта/бэкапа
├── tests/                      # 334 pytest-тестов
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
is_banned: bool (default False)
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

### SavingsSnapshot
```
id: int (PK)
user_id: int (FK → User.id)
date: date (unique per user)
created_at: datetime
items: relationship → SavingsItem (cascade delete)
```

### SavingsItem
```
id: int (PK)
snapshot_id: int (FK → SavingsSnapshot.id, CASCADE)
name: str (max 50)
amount: Decimal(10, 2)
```

### WealthItem
```
id: int (PK)
user_id: int (FK → User.id)
type: str  — "A" (актив) или "P" (пассив)
name: str (max 100)
amount: Decimal(10, 2)
note: str (max 200, nullable)
updated_at: datetime
```

### Budget
```
id: int (PK)
user_id: int (FK → User.id)
category: str (max 50)
amount: Decimal(10, 2)  — месячный лимит
is_active: bool (default True)
alerted_80: bool  — флаг уведомления при 80%
alerted_100: bool  — флаг уведомления при 100%
last_reset_month: int (nullable)  — месяц последнего сброса флагов
```

### Goal
```
id: int (PK)
user_id: int (FK → User.id)
name: str (max 100 — синхронно со схемой и app-валидацией `MAX_GOAL_NAME_LENGTH`)
target_amount: Decimal(10, 2)
current_amount: Decimal(10, 2)  — накоплено (default 0)
deadline: date (nullable)
is_completed: bool (default False)
created_at: datetime
completed_at: datetime (nullable)  — заполняется при complete_goal
deposits: relationship → GoalDeposit (cascade delete)
```

### GoalDeposit
```
id: int (PK)
goal_id: int (FK → Goal.id, CASCADE)
account_id: int (FK → Account.id, nullable, SET NULL)
amount: Decimal(10, 2)  — положительный = пополнение, отрицательный = снятие
note: str (max 200, nullable)
created_at: datetime
```

### UserCategory
```
id: int (PK)
user_id: int (FK → User.id)
name: str (max 50, unique per user)
cat_type: str  — "+" доход, "-" расход, "*" оба
is_active: bool
sort_order: int
created_at: datetime
```

### CategoryKeyword
```
id: int (PK)
user_id: int (FK → User.id)
category_id: int (FK → UserCategory.id, CASCADE)
keyword: str (max 50, unique per user)
```

### Индексы
```
ix_records_user_created         — (user_id, created_at)           выборка по периоду
ix_records_user_operation       — (user_id, operation)            отчёты по типу
ix_records_user_op_cat          — (user_id, operation, category)  GROUP BY категориям
ix_savings_user_date            — (user_id, date, unique)         один снимок в день
ix_budgets_user_category          — (user_id, category, unique)     один бюджет на категорию
ix_goals_user_completed           — (user_id, is_completed)         фильтрация активных целей
ix_goal_deposits_goal_id          — (goal_id)                        операции по цели
ix_user_categories_user_name      — (user_id, name, unique)         дубли категорий
ix_category_keywords_user_keyword — (user_id, keyword, unique)      дубли ключевых слов
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
waiting_for_search_query
waiting_for_search_page
waiting_for_history_category_filter
waiting_for_weekday_type    — выбор типа (Расходы/Доходы) для weekday-отчёта
waiting_for_weekday_period  — выбор периода для weekday-отчёта
```

### BudgetStates
```
choosing_action    — главное меню бюджетов
choosing_category  — выбор категории (добавление / изменение / удаление)
entering_amount    — ввод суммы лимита
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

### SavingsStates
```
choosing_names_source    — использовать прошлые названия или ввести новые
entering_amounts         — итеративный ввод сумм по шаблону
confirming_snapshot      — подтверждение перед сохранением
entering_new_field_name  — ввод нового поля
entering_new_field_amount
editing_item_amount
```

### WealthStates
```
choosing_type     — актив или пассив
entering_name
entering_amount
entering_note
editing_amount
```

### RecordEditStates
```
waiting_for_record_edit_value  — ввод нового значения при редактировании записи
```

### CategoryStates
```
choosing_action               — главное меню категорий
choosing_type_for_add         — тип новой категории (+ / - / *)
entering_name_for_add         — название новой категории
choosing_category_to_rename   — выбор категории для переименования
entering_new_name
choosing_category_to_delete   — выбор категории для удаления
confirming_delete
choosing_category_for_record  — выбор категории при добавлении записи
confirming_suggested_category — подтверждение автоподсказки
entering_category_for_record  — ручной ввод категории
```

### AdminStates
```
in_admin
broadcast_text
search_query
```

### ExportImportStates
```
waiting_for_export_period  — выбор периода экспорта
waiting_for_export_type    — выбор типа записей (все / доходы / расходы)
waiting_for_import_file    — ожидание xlsx-файла от пользователя
waiting_for_import_confirm — подтверждение импорта после валидации
```

### GoalStates
```
viewing_list               — список активных целей
viewing_detail             — карточка конкретной цели
viewing_archive            — список завершённых целей
entering_name              — ввод названия новой цели
entering_amount            — ввод целевой суммы
entering_deadline          — ввод дедлайна (или «Без дедлайна»)
selecting_deposit_account  — выбор счёта для пополнения
entering_deposit_amount    — ввод суммы пополнения
entering_deposit_note      — ввод заметки к пополнению
selecting_withdraw_account — выбор счёта для снятия
entering_withdraw_amount   — ввод суммы снятия
entering_withdraw_note     — ввод заметки к снятию
editing_name               — редактирование имени существующей цели
editing_amount             — редактирование целевой суммы
editing_deadline           — редактирование дедлайна
```

## Middleware

### UserMiddleware
Получает внутренний `user_id` из БД один раз и передаёт в хендлеры через `data["user_id"]`.
Хендлеры читают `kwargs["user_id"]` — без лишних запросов к БД. Если юзер не найден или забанен, middleware блокирует апдейт.

### RateLimitMiddleware
60 запросов/60 с на пользователя. Авточистка неактивных пользователей каждые 5 минут.

## Конвенции

### Транзакции в репозиториях
Функции-репозитории (`core/database/requests/*`) **сами вызывают `await session.commit()`** после write-операций и `rollback()` в `except`. Хендлеры не делают commit поверх — только вызывают функцию репозитория и используют результат.

**Исключение:** `core/database/requests/goals.py` — функции `create_goal`, `update_goal`, `deposit_goal`, `withdraw_goal`, `complete_goal`, `delete_goal` НЕ коммитят сами. Коммит делает вызывающий хендлер (`core/handlers/goals.py`). Это техдолг, требующий выравнивания с остальными модулями.

Read-only функции (`reports.py`, `_common.py`) commit не делают.

## Основные потоки

### Добавление записи (кнопка)
```
[Доход] / [Расход]
    → FSM: waiting_for_amount
    → parse_record_line()
    → _maybe_ask_category()  — если бот знает подходящую категорию
    → если счетов > 1 → FSM: waiting_for_account
    → add_record() → БД
```

### Быстрый ввод
```
"+5000 зарплата" / "-200 кофе"
    → handle_direct_record()
    → parse_record_line()
    → _maybe_ask_category()  — если бот знает подходящую категорию
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
    → фильтр по типу (Все / Расходы / Доходы) и по категории (топ-15)
    → [🔍 Поиск] → ввод запроса (текст / >N / <N / =N) → постраничные результаты
    → тап на запись → редактирование (сумма / категория / дата / счёт / удаление)
```

### Отчёт
```
[Отчёт] → тип → год → месяц
    → get_categories_summary()  — SQL GROUP BY
    → build_report_chart()      — PNG (ThreadPoolExecutor, таймаут 10 с)
    → отправка фото + caption
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

### Накопления
```
[Накопления]
    → использовать прошлые названия или ввести новые
    → итеративный ввод сумм
    → подтверждение → SavingsSnapshot + SavingsItem → БД
    → график динамики роста
```

### Категории
```
[Категории]
    → текстовый список расходных / доходных категорий
    → Добавить / Переименовать / Удалить
    → при добавлении записи: suggest_category() по ключевым словам
    → пользователь подтверждает → learn_keyword() обучает бота
```

### Бюджеты
```
[Бюджеты]
    → get_budget_status()  — факт/лимит + прогресс-бар по текущему месяцу
    → Добавить / Изменить / Удалить лимит по категории расходов
    → при записи расхода: check_budget_alerts() → уведомление при ≥80% и ≥100%
    → автосброс флагов alerted_80/alerted_100 в новом месяце
```

### Отчёт по дням недели
```
[Отчёт] → По дням недели
    → выбор типа (Расходы / Доходы)
    → выбор периода
    → get_weekday_report()  — SQL GROUP BY strftime('%w')
    → format_weekday_report() — таблица среднего по дням
    → build_weekday_chart()   — столбчатый PNG-график
```

### Цели
```
[Цели]
    → get_goals() со smart-sort: достигнутые-не-закрытые → overdue → по дедлайну → по прогрессу
    → format_goals_list(): эмодзи семантический/⚠️/✅, прогресс %, дедлайн
    → goals_list_keyboard(goals, archive_count): цели + [➕ Новая] [📁 Архив (N)]

    Карточка (goal_detail)
        → format_goal_detail(): прогресс, дедлайн, «Откладывать ~X/мес», ETA-прогноз
        → goal_detail_keyboard: [💰 Пополнить] [📤 Снять] [✏️ Редактировать]
                                [✅ Завершить] [🗑 Удалить]

    Новая цель → название → сумма → дедлайн (опционально)
        → create_goal()

    Пополнить → выбор счёта → quick-amounts (10%/25%/50%/ежемес/остаток) или ввод суммы → заметка
        → deposit_goal(): GoalDeposit + account.balance_offset -= amount
        → история и отчёты НЕ засоряются (без Record)
        → если current >= target — nudge [✅ Завершить цель]

    Снять → выбор счёта → quick-amounts (10%/25%/50%/всё) или ввод суммы → заметка
        → withdraw_goal(): GoalDeposit (amount < 0) + account.balance_offset += amount

    Редактировать → имя / сумма / дедлайн
        → update_goal() с валидацией (сумма >= накопленного)
        → промпт показывает текущее значение в <code> для tap-to-copy

    Завершить → complete_goal() → is_completed=True, completed_at=now()

    Архив → завершённые цели с датой закрытия и длительностью накопления
        → ↩️ Переоткрыть → is_completed=False, completed_at=None
        → 🗑 Удалить → delete_goal() (каскад GoalDeposit)
```

### Экспорт / Импорт
```
[Экспорт]
    → FSM: waiting_for_export_period → выбор периода (месяц / 3 мес / год / всё)
    → FSM: waiting_for_export_type   → выбор типа (все / доходы / расходы)
    → get_all_records_for_export()   — выборка из БД
    → _build_export_sync()           — xlsx: лист «Записи» + лист «Итоги»
    → отправка файла

[Импорт]
    → отправка шаблона _build_template_sync()
    → FSM: waiting_for_import_file   → получение xlsx от пользователя
    → parse_import_file()            — валидация строк, лимит 1000
    → check_duplicate_record()       — подсчёт потенциальных дублей (не блокирующий)
    → FSM: waiting_for_import_confirm → подтверждение
    → bulk_insert_records()          → БД

[/backup]
    → get_all_records_for_export() + get_all_budgets_for_backup()
      + get_latest_snapshot_for_backup() + get_wealth_items_for_backup()
    → _build_backup_sync()           — 4 листа: Записи / Бюджеты / Накопления / Активы
    → отправка файла
```

## Оптимизации

- **CASE WHEN** — доходы и расходы считаются одним запросом
- **get_history_data()** — count + totals + records за один round-trip
- **Составные индексы** — ускорение выборки по периодам и категориям
- **@lru_cache** — статичные клавиатуры (главное меню, периоды, меню категорий)
- **ThreadPoolExecutor** — генерация графиков вне event loop

## Конфигурация (`config.py`)

| Параметр | Значение |
|---|---|
| RECORDS_PER_PAGE | 15 |
| MAX_SHOW_ALL_RECORDS | 50 |
| MAX_CATEGORY_LENGTH | 50 |
| MAX_ACCOUNT_NAME_LENGTH | 40 |
| MAX_AMOUNT | 1 000 000 |
| MAX_GOAL_AMOUNT | 10 000 000 |
| MAX_GOAL_NAME_LENGTH | 100 |
| MAX_CATEGORIES_IN_PIE | 5 |
| MAX_CAPTION_LENGTH | 1024 |
| MAX_MESSAGE_LENGTH | 4096 |
| CHART_TIMEOUT_SECONDS | 10 |
| CHART_DPI | 150 |
| TIMEZONE | Europe/Moscow |

## Тесты (334)

```bash
pytest tests/ -v
```

| Файл | Что покрывает |
|---|---|
| test_db.py | CRUD-операции |
| test_db_extended.py | Граничные случаи БД |
| test_accounts.py | Счета, переводы, балансы |
| test_accounts_extended.py | Расширенные сценарии счетов |
| test_records_edit.py | Редактирование записей |
| test_savings.py | Накопления: снимки, динамика |
| test_categories.py | Категории: CRUD, суггестия, ключевые слова |
| test_utils.py | format_money, графики |
| test_utils_extended.py | Расширенные утилиты |
| test_keyboards.py | Клавиатуры |
| test_parse_record.py | Парсинг быстрого ввода |
| test_period_filters.py | Фильтры периодов и DB-запросы |
| test_queries.py | Сложные SQL-запросы |
| test_search_filter.py | Поиск записей и фильтры истории |
| test_budgets.py | Бюджеты: CRUD, прогресс, уведомления, сброс флагов; weekday-отчёт |
| test_export_import.py | Экспорт/импорт: парсинг xlsx, валидация строк, bulk insert, дубли |
| test_goals.py | Цели: CRUD, deposit/withdraw, edit, archive, smart sort, overdue, ETA, форматтеры, длительность накопления |
