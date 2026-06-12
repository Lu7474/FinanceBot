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
│   ├── error_tracker.py        # In-memory счётчик ERROR/CRITICAL за 24ч для admin-статистики
│   ├── reports.py              # Построение текста отчётов
│   ├── middleware.py           # RateLimitMiddleware, UserMiddleware
│   ├── keyboards/              # ReplyKeyboard и InlineKeyboard, разбито по доменам (пакет, re-export через __init__.py)
│   ├── export.py               # Excel export/import/backup: build и validate функции
│   ├── exceptions.py           # Доменные исключения (GoalError и наследники)
│   ├── scheduler.py            # APScheduler: рассылки сводок и напоминаний + форматтеры
│   ├── handlers/
│   │   ├── __init__.py         # Сборка всех роутеров в один Router
│   │   ├── common.py           # FSM states, фильтры кнопок, get_user_id_from_event
│   │   ├── menu.py             # /start, /help, /cancel
│   │   ├── more.py             # Подменю «Ещё»: переключение главного и второго экранов reply-меню
│   │   ├── records.py          # Добавление записей (кнопка + быстрый ввод)
│   │   ├── history.py          # История с пагинацией
│   │   ├── reports.py          # Отчёты: по категориям / структура по месяцам / годовой; сравнение и переключение периода
│   │   ├── delete.py           # Удаление записей с подтверждением
│   │   ├── accounts.py         # Управление счетами
│   │   ├── capital.py          # Капитал: живой список активов/пассивов (+ виртуальные строки счетов/долгов), снимки и история
│   │   ├── records_edit.py     # Редактирование отдельных записей из истории
│   │   ├── categories.py       # Пользовательские категории + суггестия при вводе
│   │   ├── budgets.py          # Месячные бюджеты по категориям
│   │   ├── export_import.py    # Экспорт/импорт/бэкап в Excel
│   │   ├── goals.py            # Финансовые цели: CRUD, пополнение, снятие
│   │   ├── debts.py            # Долги и займы: создание, частичное погашение, архив
│   │   ├── payments.py         # Платежи: напоминания о разовых/периодических платежах, оплата с записью расхода
│   │   ├── notifications.py    # /notifications, тоггл флагов, онбординг
│   │   ├── settings.py         # Настройки: режим описания записей + переход к уведомлениям
│   │   ├── family.py           # Семейный бюджет: создание/вступление, общая история и отчёты, управление
│   │   ├── admin.py            # Режим администратора
│   │   └── fallback.py         # Fallback для неизвестных сообщений
│   └── database/
│       ├── models.py           # SQLAlchemy модели + миграции (_migrate)
│       └── requests/           # CRUD по доменам (re-export через __init__.py)
│           ├── _common.py      # apply_period_filter, SYSTEM_CATEGORIES, лимиты
│           ├── users.py        # get_user_by_tg_id, set_user, notifiable-users, last-reminded
│           ├── records.py      # add/get/update/delete_record, delete_records_bulk, totals, duplicate-check
│           ├── reports.py      # categories_summary, history_data, monthly/yearly/stacked/search
│           ├── accounts.py     # CRUD счетов, балансы, переводы
│           ├── categories.py   # UserCategory + suggest/learn keywords + seed_defaults
│           ├── savings.py      # snapshots, items (type A/P), wealth-items, collect_capital_items, create_snapshot_from_wealth
│           ├── budgets.py      # CRUD бюджетов, alert-логика, сброс флагов
│           ├── goals.py        # CRUD целей, deposit/withdraw, complete
│           ├── debts.py        # CRUD долгов, частичные платежи, выборка для напоминаний
│           ├── payments.py     # CRUD платежей, mark_paid (перенос цикла), выборка для напоминаний
│           ├── notifications.py # weekly/monthly/daily summary-выборки
│           ├── family.py       # семьи: membership, инвайт-коды, общие сводки и разбивки по категориям
│           ├── admin.py        # admin-выборки, ban, cascade-delete пользователя
│           └── backup.py       # выборки и bulk-insert для экспорта/бэкапа
├── tests/                      # 692 pytest-теста
└── requirements.txt
```

## Технологии

| Компонент | Технология |
|---|---|
| Фреймворк бота | aiogram 3 |
| База данных | SQLite + SQLAlchemy 2.0 (async); опц. PostgreSQL через asyncpg (`DATABASE_URL`) |
| Графики | matplotlib |
| Планировщик | APScheduler (AsyncIOScheduler) |
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
last_reminded_at: datetime (nullable)  — когда отправлено последнее напоминание
notify_weekly: bool (default False)   — еженедельная сводка
notify_monthly: bool (default False)  — ежемесячная сводка
notify_daily: bool (default False)    — ежедневные итоги
notify_reminder: bool (default False) — напоминание при простое
notify_debts: bool (default False)    — напоминания о приближении срока долга
notify_payments: bool (default False) — напоминания о приближении срока платежа
description_mode: str (default "off") — режим ввода описания записей: off | brackets | button | auto
```

### Account
```
id: int (PK)
user_id: int (FK → User.id)
name: str (max 50 символов; валидация ввода ограничивает 40 — MAX_ACCOUNT_NAME_LENGTH)
balance_offset: Decimal(14, 2)  — сдвиг баланса для ручной установки
created_at: datetime
```

### Record
```
id: int (PK)
user_id: int (FK → User.id)
account_id: int (FK → Account.id, nullable, SET NULL при удалении счёта)
operation: str  — "+" (доход) или "-" (расход)
amount: Decimal(14, 2)
category: str (max 50 символов)
created_at: datetime (Moscow TZ)
description: str (max 255, nullable)  — заметка к записи
transfer_id: int (nullable, indexed)  — связывает пару записей перевода (expense+income = id расходной строки); NULL для обычных записей
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
type: str  — "A" (актив) или "P" (пассив), default "A"
name: str (max 120)
amount: Decimal(14, 2)
```

### WealthItem
```
id: int (PK)
user_id: int (FK → User.id)
type: str  — "A" (актив) или "P" (пассив)
name: str (max 100)
amount: Decimal(14, 2)
note: str (max 200, nullable)
updated_at: datetime
```

### Budget
```
id: int (PK)
user_id: int (FK → User.id)
category: str (max 50)
amount: Decimal(14, 2)  — месячный лимит
is_active: bool (default True)
alerted_80: bool  — флаг уведомления при 80%
alerted_100: bool  — флаг уведомления при 100%
last_reset_month: int (nullable)  — месяц последнего сброса флагов
```

### Goal
```
id: int (PK)
user_id: int (FK → User.id)  — создатель цели; для семейной = owner семьи
family_id: int (FK → Family.id, nullable, SET NULL)  — NULL = личная цель; задан = общая семейная. SET NULL при роспуске семьи (цель становится личной у ex-owner, деньги целы)
name: str (max 100 — синхронно со схемой и app-валидацией `MAX_GOAL_NAME_LENGTH`)
target_amount: Decimal(14, 2)
current_amount: Decimal(14, 2)  — накоплено (default 0)
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
user_id: int (FK → User.id, nullable, SET NULL)  — кто внёс/снял (атрибуция вклада в семейной цели); NULL у старых строк = взнос владельца
account_id: int (FK → Account.id, nullable, SET NULL)
amount: Decimal(14, 2)  — положительный = пополнение, отрицательный = снятие
note: str (max 200, nullable)
created_at: datetime
```

### Debt
Изолированная сущность: на баланс/отчёты/цели не влияет, погашение НЕ создаёт Record.
```
id: int (PK)
user_id: int (FK → User.id, CASCADE)
direction: str  — "I" (мне должны) / "O" (я должен)
person_name: str (max 100 — MAX_DEBT_PERSON_NAME)
amount: Decimal(14, 2)  — исходная сумма долга
remaining: Decimal(14, 2)  — текущий остаток
description: str (max 200, nullable)
due_date: date (nullable)  — срок возврата
is_closed: bool (default False)
last_reminded_at: datetime (nullable)  — антиспам для напоминаний о сроке
created_at: datetime
closed_at: datetime (nullable)
payments: relationship → DebtPayment (cascade delete)
```

### DebtPayment
```
id: int (PK)
debt_id: int (FK → Debt.id, CASCADE)
amount: Decimal(14, 2)  — сумма частичного погашения
note: str (max 200, nullable)
paid_at: datetime
```

### Payment
Напоминание о платеже (налоги, страховки, ОСАГО, коммуналка, подписки). При оплате бот предлагает записать расход (Record) на сумму платежа в категорию `category`; можно отказаться — тогда баланс не трогается.
```
id: int (PK)
user_id: int (FK → User.id, CASCADE)
title: str (max 100 — MAX_PAYMENT_TITLE)
amount: Decimal(14, 2, nullable)  — NULL = плавающая сумма (напоминаем без точной суммы)
category: str (max 50, nullable)  — категория расхода для записи при оплате; NULL → «не указано»
due_date: date  — дата платежа
period: str (default "none")  — none (разовый) | month | year (периодический)
is_active: bool (default True)  — разовый после оплаты → False; периодический остаётся активным
last_paid_at: datetime (nullable)  — когда последний раз отмечен оплаченным
last_reminded_at: datetime (nullable)  — антиспам для напоминаний о сроке
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

### Family
Группа пользователей с общим доступом к истории и отчётам. Записи остаются личными (`Record.user_id`) — семья только агрегирует их в scope.
```
id: int (PK)
name: str (max 100)
owner_id: int (FK → User.id, CASCADE)  — владелец, быстрый чек прав управления
invite_code: str (max 8, unique)  — код приглашения (алфавит без похожих 0 O 1 I L)
created_at: datetime
members: relationship → FamilyMember (cascade delete)
```

### FamilyMember
Членство пользователя в семье — единственный источник правды о составе. Один юзер состоит максимум в одной семье (unique на user_id). Лимит — 5 участников (`MAX_FAMILY_MEMBERS`).
```
id: int (PK)
family_id: int (FK → Family.id, CASCADE)
user_id: int (FK → User.id, CASCADE, unique)
role: str (max 10)  — "owner" | "member"
joined_at: datetime
```

### Индексы
```
ix_records_user_created         — (user_id, created_at)           выборка по периоду
ix_records_user_operation       — (user_id, operation)            отчёты по типу
ix_records_user_op_cat          — (user_id, operation, category)  GROUP BY категориям
ix_records_transfer_id          — (transfer_id)                   пара записей перевода
ix_savings_user_date            — (user_id, date, unique)         один снимок в день
ix_budgets_user_category          — (user_id, category, unique)     один бюджет на категорию
ix_goals_user_completed           — (user_id, is_completed)         фильтрация активных целей
ix_goals_family_id                — (family_id)                      цели семьи
ix_goal_deposits_goal_id          — (goal_id)                        операции по цели
ix_goal_deposits_user_id          — (user_id)                        вклады участника в семейной цели
ix_debts_user_closed              — (user_id, is_closed)             активные / архивные долги
ix_debts_due_date                 — (due_date)                       выборка для напоминаний о сроке
ix_debt_payments_debt_id          — (debt_id)                        история платежей по долгу
ix_payments_user_active           — (user_id, is_active)             активные / закрытые платежи
ix_payments_due_date              — (due_date)                       выборка для напоминаний о сроке
ix_user_categories_user_name      — (user_id, name, unique)         дубли категорий
ix_category_keywords_user_keyword — (user_id, keyword, unique)      дубли ключевых слов
ix_family_members_family_id       — (family_id)                     состав семьи
```

## FSM

### AddRecord
```
waiting_for_amount       — ввод суммы и категории
waiting_for_account      — выбор счёта (если их несколько)
waiting_for_description  — ввод описания после сохранения (режим описания «кнопкой»)
```

### MenuStates
```
waiting_for_history_period
waiting_for_history_page
waiting_for_custom_period
waiting_for_report_year
waiting_for_report_month
waiting_for_delete_period
waiting_for_delete_record
waiting_for_delete_confirm
waiting_for_delete_bulk_confirm  — подтверждение массового удаления «все за период»
waiting_for_search_query
waiting_for_search_page
waiting_for_history_category_filter
waiting_for_yearly_type     — выбор типа (Доходы/Расходы) для годового отчёта
waiting_for_yearly_year     — выбор года (или «за всё время») для годового отчёта
waiting_for_yearly_cats     — мультивыбор категорий для годового отчёта
waiting_for_balance_year    — выбор года для графика динамики баланса
waiting_for_balance_month   — выбор месяца для графика динамики баланса
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
waiting_for_transfers_page  — пагинация журнала переводов
```

### CapitalStates
```
choosing_type            — добавление: актив или пассив
entering_name            — добавление: название
entering_amount          — добавление: сумма
entering_note            — добавление: необязательная заметка
editing_amount           — изменение суммы ручного актива/пассива
editing_snapshot_amount  — изменение суммы строки снимка
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
confirming_merge              — подтверждение слияния при переименовании в существующее имя
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
choosing_scope             — личная или общая семейная цель (если юзер в семье)
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

### DebtStates
```
viewing_list            — список активных долгов
viewing_detail          — карточка конкретного долга
viewing_archive         — список закрытых долгов
waiting_direction       — выбор направления (мне должны / я должен)
waiting_person          — ввод имени должника / кредитора
waiting_amount          — ввод суммы долга
waiting_description      — ввод описания (опционально)
waiting_due_date        — ввод срока возврата (или «Без срока»)
waiting_payment_amount  — ввод суммы частичного погашения
waiting_payment_note    — ввод заметки к погашению
```

### PaymentStates
```
viewing_list         — список активных платежей
viewing_detail       — карточка конкретного платежа
waiting_title        — ввод названия платежа
waiting_amount       — ввод суммы (или «Сумма не задана»)
waiting_due_date     — ввод даты платежа
waiting_period       — выбор периодичности (разовый / месяц / год)
waiting_category     — выбор категории расхода (или «Без категории»)
editing_title        — редактирование названия
editing_amount       — редактирование суммы
editing_due_date     — редактирование даты
editing_category     — редактирование категории
waiting_pay_amount   — оплата: ввод фактической суммы для записи в баланс
choosing_pay_account — оплата: выбор счёта для записи расхода
```

### FamilyStates
```
summary         — экран сводки семьи (доходы/расходы по участникам за месяц)
creating_name   — ввод названия при создании семьи
joining_code    — ввод кода приглашения
viewing_history — постраничная общая история с фильтром по участнику
renaming        — владелец вводит новое название
```

## Middleware

### UserMiddleware
Получает внутренний `user_id` из БД один раз и передаёт в хендлеры через `data["user_id"]`.
Хендлеры читают `kwargs["user_id"]` — без лишних запросов к БД. Если юзер не найден или забанен, middleware блокирует апдейт.

### RateLimitMiddleware
60 запросов/60 с на пользователя. Авточистка неактивных пользователей каждые 5 минут.

## Конвенции

### Транзакции: коммитят хендлеры
Хендлеры открывают сессию (`async with async_session() as session:`) и **сами вызывают `await session.commit()`** после write-операций. Функции-репозитории (`core/database/requests/*`) только добавляют/изменяют объекты (`session.add`, `session.flush`, `delete`) и не коммитят — транзакцией владеет вызывающий хендлер.

**Исключение:** `set_user` (`users.py`) коммитит сам — вызывается при `/start` для регистрации пользователя и передаёт `commit=False` в `seed_default_categories`, чтобы избежать вложенных коммитов.

Read-only функции (`reports.py`, `_common.py`) commit не делают.

## Основные потоки

### Меню
```
Главное меню (main_menu_keyboard):
  [Доход] [Расход]
  [История] [Отчёт]
  [Счета] [Удалить запись]
  [Ещё]

Подменю «Ещё» (more_menu_keyboard, more.py) — второй экран reply-клавиатуры:
  [Капитал] [Категории]
  [Бюджеты] [Цели]
  [Долги] [Платежи]
  [Семья] [Настройки]
  [Экспорт] [Импорт]
  [Назад]

Кнопка «Накопления» остаётся легаси-алиасом «Капитала» (is_capital).
```

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
[Отчёт] → report_section_keyboard — четыре раздела:

  📊 По категориям → тип (Доход / Расход) → год → месяц
      → get_categories_summary()  — SQL GROUP BY
      → build_report_pie()        — PNG (ThreadPoolExecutor, таймаут 10 с)
      → под графиком chart_period_keyboard: ◀ [месяц] ▶,
        переключатель Месяц / Квартал / Год, «Сравнить с прошлым месяцем» (build_trend_chart)

  📈 Структура по месяцам → тип → период (3 / 6 / 12 мес)
      → get_stacked_data() → build_stacked_bar_chart() — stacked PNG

  📅 Годовой отчёт → тип → год (или «за всё время») → мультивыбор категорий
      → get_yearly_report() → format_yearly_report() + build_yearly_chart()

  💰 Динамика баланса → год → месяц
      → линейный график изменения баланса по дням за выбранный месяц
```

### Управление счетами
```
[Счета]
    → get_account_balances() — балансы всех счетов
    → создать / переименовать / удалить (с переносом записей)
    → перевод между счетами (↔️) + журнал переводов (🔁) с просмотром и отменой
    → установить баланс (через balance_offset)
    → история конкретного счёта (с пагинацией и фильтром по типу: все / расходы / доходы)
```

### Капитал
```
[Капитал]
    → живой список: ручные WealthItem (A/P)
      + виртуальные строки 💳 из балансов счетов (>0 актив, <0 пассив) и открытых долгов (I актив, O пассив)
      → format_capital(): итоги активов/пассивов, чистый капитал, дифф к последнему снимку
    → ➕/✏️/🗑 — CRUD только ручных WealthItem; счета/долги read-only
    → 📸 Снимок → collect_capital_items() → create_snapshot_from_wealth()
      (замораживает срез в SavingsSnapshot + SavingsItem с type A/P; перезапись за сегодня — через подтверждение)
    → 🕘 История → навигация ◀▶ по датам, format_capital_snapshot() с диффом по (type, name), итогам и чистому капиталу
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

    Новая цель → название → сумма → дедлайн (опционально) → scope (личная / общая семейная, если юзер в семье)
        → create_goal() (family_id задаётся для семейной цели)

    Семейная цель: видна всем участникам, взносы атрибутируются (GoalDeposit.user_id);
        при роспуске семьи family_id → NULL, цель и деньги остаются у бывшего владельца

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
    → _build_backup_sync()           — 4 листа: Записи / Бюджеты / Снимок капитала (Тип/Название/Сумма) / Активы
    → отправка файла
```

### Долги и займы
```
[Долги]
    → get_active_debts() + count_closed_debts()
    → format_debts_list(): «мне должны» / «я должен», остаток, дедлайн, индикатор просрочки
    → debts_menu_keyboard(has_active, has_archive)

    Карточка (debt_detail)
        → format_debt_detail(): остаток / исходная сумма, история платежей, дедлайн
        → debt_detail_keyboard: [💵 Погасить] [🗑 Удалить]

    Новый долг → направление (мне должны / я должен) → имя → сумма → описание → срок (опц.)
        → create_debt()

    Погасить → сумма частичного платежа → заметка
        → add_payment(): DebtPayment + remaining -= amount; при remaining == 0 → is_closed=True, closed_at=now()
        → история и отчёты НЕ засоряются (без Record)

    Архив → закрытые долги с датой закрытия
        → 🗑 Удалить → delete_debt() (каскад DebtPayment)
```

### Семья (`core/handlers/family.py`)
```
[Ещё] → [Семья]
    Нет семьи → family_join_or_create_keyboard: [Создать] / [Присоединиться]
        Создать → ввод названия → create_family() (owner + invite_code 8 симв.)
        Присоединиться → ввод кода (или /join <код>) → join_family()
            (проверки: код валиден, < 5 участников, юзер ещё без семьи)

    Есть семья → сводка get_family_summary(): доходы/расходы по участникам за месяц
        family_menu_keyboard(is_owner):
          [📋 Общая история] → get_family_member_ids() + get_history_data(scope)
              → постранично, фильтр по участнику (цветовые маркеры)
          [📊 Общий отчёт] → тип → период (месяц / 3 мес / год)
              → get_family_category_breakdown() → build_family_stacked_chart() + текст
          [⚙️ Управление] (только владелец): regenerate_invite_code / rename_family /
              kick_member / dissolve_family
          [Покинуть] (участник) → leave_family() (владелец не может — только расформировать)

Записи остаются личными (Record.user_id), семья только агрегирует их в scope.
Семья НЕ кэшируется в UserMiddleware — хендлеры вызывают get_family() напрямую.
```

### Уведомления (`core/scheduler.py`)
```
APScheduler (AsyncIOScheduler, TZ=Europe/Moscow), запускается в bot.py → setup_scheduler()
    ├─ weekly_report   — вс 20:00  → send_weekly_report   → format_weekly_summary
    ├─ monthly_report  — last 20:00 → send_monthly_report → format_monthly_summary (сравнение с пред. месяцем)
    ├─ daily_summary   — ежедн. 21:00 → send_daily_summary → format_daily_summary
    ├─ reminders       — ежедн. 20:00 → send_reminders (простой 2+ дня, антиспам через last_reminded_at)
    ├─ debt_reminders  — ежедн. 10:00 → send_debt_reminders (приближение/наступление due_date, антиспам через Debt.last_reminded_at)
    └─ payment_reminders — ежедн. 09:00 → send_payment_reminders (приближение/наступление due_date, антиспам через Payment.last_reminded_at)

Аудитория: get_notifiable_users() — не забаненные, с ≥1 записью; каждый тип фильтруется флагом notify_*.
Долговые напоминания: get_debts_to_remind() — открытые долги с due_date у пользователей с notify_debts.
Платёжные напоминания: get_payments_to_remind() — активные платежи с due_date у пользователей с notify_payments.
Выборки сумм/топ-категорий — core/database/requests/notifications.py (исключает SYSTEM_CATEGORIES).

Настройки (core/handlers/notifications.py)
    /notifications → notification_settings_keyboard(user): тоггл каждого флага (notify_toggle:<key>, включая notify_toggle:debts и notify_toggle:payments)
    Также доступно из раздела «Настройки» (settings:notifications) с кнопкой возврата
    Онбординг: после первой записи (_maybe_send_onboarding) → [Включить всё] / [Пропустить]
        notify_enable_all — включает все флаги; notify_skip — закрывает без изменений
```

### Платежи (`core/handlers/payments.py`)
```
[Ещё] → [Платежи]
    → get_active_payments() → format_payments_list(): название, сумма (или «~»), дата, периодичность, индикатор просрочки
    → payments_list_keyboard(active): платежи + [➕ Добавить]

    Карточка (payment_view)
        → format_payment_detail(): сумма, дата, периодичность, история оплат
        → payment_detail_keyboard: [✅ Оплачено] [✏️ Изменить] [🗑 Удалить]

    Новый платёж → название → сумма (или «Сумма не задана») → дата → периодичность (разовый / месяц / год)
        → категория расхода (или «Без категории») → create_payment()

    Оплатил (pay:done, из карточки или напоминания) → предложение записать расход в баланс:
        фикс. сумма → [✅ Да, −N₽] [✏️ Другая сумма] [Нет, только отметить]
        плавающая   → ввод фактической суммы (или «Пропустить запись»)
        → счёт: 0 счетов — без счёта, 1 — автоматически, 2+ — выбор (pay:acc, не пересекается с acc_select)
        → одна транзакция: add_record("-", сумма, payment.category или «не указано») + mark_paid()
        → после записи — проверка бюджета категории (check_and_alert_budget, как у обычной записи)
        «Нет» → просто mark_paid(): разовый → is_active=False; периодический → due_date на следующий цикл
        Идемпотентность: кнопки rec_yes/rec_no/rec_amt/acc несут токен due_date; mark_paid(expected_due=…)
        отвергает двойной тап и устаревшие клавиатуры (PaymentAlreadyPaid → «Платёж уже отмечен оплаченным»)

    Изменить → название / сумма / дата / периодичность / категория → update_payment()
    Удалить → delete_payment()
```

### Настройки (`core/handlers/settings.py`)
```
[Ещё] → [Настройки]
    → settings_menu_keyboard(user): радио-выбор режима описания записей
        off | brackets (в скобках) | button (кнопкой после записи) | auto (категория + описание)
        → settings:mode:<mode> → User.description_mode
    → [🔔 Уведомления] (settings:notifications) → экран уведомлений с кнопкой «Назад»
```

### Админ-статистика (`core/handlers/admin.py`)
```
/admin → [📊 Статистика] (adm_stats)
    → get_bot_stats(): пользователи, баны, счета, записи, новые сегодня/неделя,
      активные за неделю, DAU за сегодня (distinct авторы несистемных записей)
    → get_retention_stats(): когорта 7д+ (зареганы ≥7д назад), retained (из них
      ≥1 несистемная запись за 7д), churned, retention %
    → get_error_count_24h(): счётчик ERROR/CRITICAL за 24ч (in-memory deque,
      core/error_tracker.py; ✅/🔴; обнуляется при рестарте = деплое)

    [📈 Динамика] (adm_growth) → отдельное фото:
        get_daily_registrations() + get_daily_active_users() (func.date по МСК-дню,
        непрерывный ряд за 30 дней) → build_admin_growth_chart(): бары регистраций
        + линия DAU на двух осях Y (ThreadPoolExecutor, таймаут 10 с)
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
| MAX_DEBT_AMOUNT | 10 000 000 |
| MAX_DEBT_PERSON_NAME | 100 |
| MAX_PAYMENT_AMOUNT | 10 000 000 |
| MAX_PAYMENT_TITLE | 100 |
| MAX_CATEGORIES_IN_PIE | 5 |
| MAX_CAPTION_LENGTH | 1024 |
| MAX_MESSAGE_LENGTH | 4096 |
| CHART_TIMEOUT_SECONDS | 10 |
| CHART_DPI | 150 |
| TIMEZONE | Europe/Moscow |

## Тесты (692)

```bash
pytest tests/ -v
```

| Файл | Что покрывает |
|---|---|
| test_db.py | CRUD-операции |
| test_db_extended.py | Граничные случаи БД |
| test_records_bulk_delete.py | Массовое удаление записей за период, исключение системных категорий, изоляция пользователей |
| test_accounts.py | Счета, переводы, балансы |
| test_accounts_extended.py | Расширенные сценарии счетов |
| test_records_edit.py | Редактирование записей |
| test_capital.py | Капитал: активы/пассивы, виртуальные строки счетов/долгов, снимки, история+дифф |
| test_categories.py | Категории: CRUD, суггестия, ключевые слова |
| test_utils.py | format_money, графики |
| test_utils_extended.py | Расширенные утилиты |
| test_keyboards.py | Клавиатуры |
| test_parse_record.py | Парсинг быстрого ввода |
| test_period_filters.py | Фильтры периодов и DB-запросы |
| test_queries.py | Сложные SQL-запросы |
| test_search_filter.py | Поиск записей и фильтры истории |
| test_budgets.py | Бюджеты: CRUD, прогресс, уведомления, сброс флагов |
| test_export_import.py | Экспорт/импорт: парсинг xlsx, валидация строк, bulk insert, дубли |
| test_export_import_tz.py | Экспорт/импорт: корректность временной зоны |
| test_goals.py | Цели: CRUD, deposit/withdraw, edit, archive, smart sort, overdue, ETA, форматтеры, длительность накопления |
| test_goals_errors.py | Цели: обработка ошибок и граничные случаи |
| test_debts.py | Долги: CRUD, частичные погашения, выборка для напоминаний, каскадное удаление |
| test_payments.py | Платежи: CRUD, mark_paid (перенос цикла), категория, запись расхода при оплате, выборка для напоминаний, форматтеры |
| test_family_goals.py | Семейные цели: общая цель, взносы участников, атрибуция, права владельца |
| test_notifications.py | Уведомления: форматтеры сводок (weekly/monthly/daily), DB-выборки |
| test_family.py | Семья: membership, инвайт-коды, лимит участников, общие сводки и разбивки, права владельца |
| test_admin.py | Админ-функции: выборки, бан, каскадное удаление, CSV-выгрузка, DAU/retention, динамика по дням |
| test_error_tracker.py | Счётчик ошибок 24ч: учёт ERROR/CRITICAL, окно, игнор INFO/WARNING |
| test_charts.py | Генерация графиков (matplotlib) |
| test_middleware.py | RateLimitMiddleware и UserMiddleware |
| test_set_user.py | Регистрация пользователя, дефолтный счёт |
