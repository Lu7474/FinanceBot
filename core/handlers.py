"""
Обработчики команд и callback-запросов.

Структура файла:
- Строки 60-100:   Фильтры команд (is_income, is_expense, ...)
- Строки 100-125:  FSM States
- Строки 125-220:  Главное меню (/start, /help, /cancel)
- Строки 220-490:  Добавление записей
- Строки 490-900:  История операций
- Строки 900-1100: Отчёты (графики)
- Строки 1100-1530: Удаление записей
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo
import re

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.keyboards import (
    delete_period_keyboard,
    history_period_keyboard,
    get_years_keyboard,
    get_months_keyboard,
    get_delete_years_keyboard,
    get_delete_months_keyboard,
    main_menu_keyboard,
    report_type_keyboard,
    confirm_delete_keyboard,
    accounts_menu_keyboard,
    account_select_keyboard,
    account_manage_keyboard,
    confirm_account_delete_keyboard,
    account_delete_move_keyboard,
)
from core.utils import (
    get_available_years_and_months,
    build_report_pie,
    build_trend_chart,
    make_comparison_text,
    format_money,
    RU_MONTHS,
    RU_WEEKDAYS,
    log_exceptions,
)

from core.database.requests import (
    set_user,
    add_record,
    get_records,
    count_records,
    delete_record,
    get_user_by_tg_id,
    get_categories_summary,
    get_history_data,
    get_monthly_totals,
    create_account,
    get_accounts,
    rename_account,
    delete_account,
    get_account_balances,
    get_account_balance,
    set_account_balance,
    get_account_record_count,
    create_transfer,
    move_and_delete_account,
    MAX_ACCOUNTS_PER_USER,
)
from core.database.models import async_session, Record
from config import (
    RECORDS_PER_PAGE,
    MAX_SHOW_ALL_RECORDS,
    MAX_CATEGORY_LENGTH,
    MAX_AMOUNT,
    MAX_MESSAGE_LENGTH,
    TIMEZONE,
)


# ==================== Хелперы ====================

async def get_user_id_from_event(
    event: Message | CallbackQuery,
    kwargs: dict,
    create_if_missing: bool = False,
) -> int | None:
    """Получает user_id из middleware или БД.

    Args:
        event: Message или CallbackQuery
        kwargs: Аргументы хендлера (содержат user_id от middleware)
        create_if_missing: Создать пользователя если не найден

    Returns:
        Внутренний user_id или None
    """
    # Сначала пробуем из middleware (уже закэшировано)
    user_id = kwargs.get("user_id")
    if user_id:
        return user_id

    # Fallback: получаем из БД (для /start и новых пользователей)
    tg_id = event.from_user.id if event.from_user else None
    if not tg_id:
        return None

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if user:
            return user.id

        if create_if_missing:
            name = event.from_user.full_name if event.from_user else "Unknown"
            user = await set_user(session, tg_id, name=name)
            if user:
                return user.id

    return None


async def save_parsed_records(
    user_id: int,
    records_to_add: list[tuple],
    account_id: int | None = None,
) -> list[tuple]:
    """Сохраняет распарсенные записи в БД.

    Args:
        user_id: Внутренний ID пользователя
        records_to_add: Список кортежей (operation, amount, category, date)
        account_id: ID счёта (опционально)

    Returns:
        Список успешно добавленных записей
    """
    added_records = []
    async with async_session() as session:
        for operation, amount, category, record_date in records_to_add:
            ok = await add_record(
                session, user_id, operation, amount, category, record_date, account_id
            )
            if ok:
                added_records.append((operation, amount, category, record_date))
    return added_records


router = Router()


# ==================== Фильтры команд ====================
# Гибкие фильтры для распознавания разных вариаций написания команд

def is_income(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Доход'."""
    if not message.text:
        return False
    text = message.text.strip().lower()
    return text in ("доход", "➕ доход", "+доход", "+ доход")


def is_expense(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Расход'."""
    if not message.text:
        return False
    text = message.text.strip().lower()
    return text in ("расход", "➖ расход", "-расход", "- расход")


def is_history(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'История'."""
    if not message.text:
        return False
    text = message.text.strip().lower()
    return text in ("история", "🕘 история")


def is_report(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Отчёт'."""
    if not message.text:
        return False
    text = message.text.strip().lower()
    return text in ("отчёт", "отчет", "📊 отчёт", "📊 отчет")


def is_delete(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Удалить запись'."""
    if not message.text:
        return False
    text = message.text.strip().lower()
    return text in ("удалить запись", "удалить", "🗑️ удалить запись", "🗑️ удалить")


def is_accounts(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Счета'."""
    if not message.text:
        return False
    return message.text.strip().lower() in ("счета", "💳 счета")


def is_main_menu_button(message: Message) -> bool:
    """True если сообщение — кнопка главного меню."""
    return any([
        is_income(message), is_expense(message), is_history(message),
        is_report(message), is_delete(message), is_accounts(message),
    ])


# ==================== FSM States ====================
# Состояния для многошаговых операций

class AddRecord(StatesGroup):
    """Состояния для добавления записи дохода/расхода."""
    waiting_for_amount = State()   # Ввод суммы и категории
    waiting_for_account = State()  # Выбор счёта


class MenuStates(StatesGroup):
    """Состояния для навигации по меню."""
    waiting_for_history_period = State()   # Выбор периода истории
    waiting_for_history_page = State()     # Навигация по страницам истории
    waiting_for_custom_period = State()    # Ввод своего периода текстом
    waiting_for_report_type = State()      # Выбор типа отчёта (доход/расход)
    waiting_for_report_year = State()      # Выбор года для отчёта
    waiting_for_report_month = State()     # Выбор месяца для отчёта
    waiting_for_delete_period = State()    # Выбор периода для удаления
    waiting_for_delete_record = State()    # Выбор записи для удаления
    waiting_for_delete_confirm = State()   # Подтверждение удаления


class AccountStates(StatesGroup):
    """Состояния для управления счетами."""
    waiting_for_account_name = State()   # Ввод названия нового счёта
    waiting_for_rename_name = State()    # Ввод нового названия при переименовании
    waiting_for_transfer_amount = State()  # Ввод суммы перевода
    waiting_for_set_balance = State()    # Ввод желаемого баланса счёта
    waiting_for_acc_hist_period = State()  # Выбор периода истории счёта
    waiting_for_acc_hist_page = State()    # Навигация по истории счёта


# ==================== Главное меню ====================

@router.message(Command("start"))
@log_exceptions("Ошибка при инициализации пользователя")
async def handle_start(message: Message, **kwargs) -> None:
    """Команда /start — регистрация пользователя и показ главного меню."""
    async with async_session() as session:
        user = await set_user(session, message.from_user.id, name=message.from_user.full_name)
        if not user:
            await message.answer("Ошибка при регистрации. Попробуйте позже.")
            return
        # Создаём счёт по умолчанию для новых пользователей
        accounts = await get_accounts(session, user.id)
        if not accounts:
            await create_account(session, user.id, "Наличные")

    # Получаем имя пользователя для приветствия
    first_name = message.from_user.first_name or "друг"

    welcome_text = f"""
💰 <b>Привет, {first_name}!</b>

Я твой персональный финансовый помощник.
Помогу вести учёт доходов и расходов.

<b>Что я умею:</b>
➕ Записывать доходы
➖ Записывать расходы
📊 Строить отчёты по категориям
🕘 Показывать историю операций
🗑️ Удалять ненужные записи

<b>Быстрый ввод:</b>
<code>+5000 зарплата</code>
<code>-200 кофе</code>

Выбери действие в меню ниже 👇
"""

    await message.answer(
        welcome_text.strip(),
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
@log_exceptions("Ошибка при отмене")
async def handle_cancel(message: Message, state: FSMContext, **kwargs) -> None:
    """Команда /cancel — отмена текущей операции и возврат в главное меню."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активной операции для отмены.")
        return

    await state.clear()
    await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "cancel")
async def handle_cancel_callback(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Обработка нажатия кнопки Отмена в inline-клавиатурах."""
    await state.clear()
    await callback.message.edit_text("Операция отменена.")
    await callback.answer()


@router.message(Command("help"))
async def handle_help(message: Message, **kwargs) -> None:
    """Команда /help — справка по боту."""
    help_text = """<b>Справка по боту</b>

<b>Команды:</b>
/start — начать работу с ботом
/help — показать эту справку
/cancel — отменить текущую операцию

<b>Основные функции:</b>
<b>Доход</b> — добавить доход
<b>Расход</b> — добавить расход
<b>История</b> — просмотр операций за период
<b>Отчёт</b> — график доходов/расходов по месяцам
<b>Удалить</b> — удалить запись

<b>Формат ввода:</b>
Одна запись: <code>1000 еда</code>
Несколько записей (каждая с новой строки):
<code>1000 зарплата
500 еда
200 транспорт</code>

<b>Старые записи (с датой):</b>
<code>27.01 500 продукты</code> — 27 января
<code>15.12.25 1000 подарок</code> — 15.12.2025

<b>Быстрый ввод (без кнопки):</b>
<code>+1000 зарплата</code> — доход
<code>-500 еда</code> — расход
<code>27.01 -350 магазин</code> — расход 27.01"""

    await message.answer(help_text, parse_mode="HTML")


# ==================== Добавление записи ====================

@router.message(~StateFilter(MenuStates.waiting_for_report_type), F.func(lambda m: is_income(m) or is_expense(m)))
@log_exceptions("Ошибка при обработке операции")
async def handle_income_expense(message: Message, state: FSMContext, **kwargs) -> None:
    """Начало добавления записи: сохраняем тип операции, просим ввести сумму."""
    await state.clear()
    is_income_op = is_income(message)
    operation = "+" if is_income_op else "-"
    await state.update_data(operation=operation)

    if is_income_op:
        prompt_text = "💵 Введите сумму и категорию:\n<code>5000 зарплата</code>"
    else:
        prompt_text = "🛒 Введите сумму и категорию:\n<code>500 продукты</code>"

    await message.answer(prompt_text, parse_mode="HTML")
    await state.set_state(AddRecord.waiting_for_amount)


def _deserialize_records(
    serialized: list[dict],
) -> list[tuple[str, Decimal, str, datetime | None]]:
    """Восстанавливает записи из FSM-state (str → Decimal/datetime)."""
    result = []
    for d in serialized:
        date = datetime.fromisoformat(d["date"]) if d.get("date") else None
        result.append((d["op"], Decimal(d["amount"]), d["cat"], date))
    return result


def format_added_records_response(
    added_records: list[tuple[str, Decimal, str, datetime | None]],
    errors: list[str] | None = None,
    account_name: str | None = None,
) -> str:
    """Формирует красивый ответ после добавления записей.

    Args:
        added_records: Список кортежей (operation, amount, category, date)
        errors: Список ошибок парсинга (опционально)

    Returns:
        Отформатированный текст ответа
    """
    if not added_records:
        return "Не удалось сохранить записи."

    today = datetime.now(ZoneInfo(TIMEZONE)).date()

    if len(added_records) == 1:
        op, amt, cat, record_date = added_records[0]
        icon = "💵" if op == "+" else "🛒"
        op_type = "Доход" if op == "+" else "Расход"

        date_str = ""
        if record_date and record_date.date() != today:
            date_str = f"\n📅 Дата: {record_date.strftime('%d.%m.%Y')}"

        response = f"""
✅ <b>Запись добавлена!</b>

{icon} {op_type}: <b>{amt:,.0f}₽</b>
📁 Категория: {cat}{date_str}
""".replace(",", " ")
    else:
        total_income = sum(amt for op, amt, _, _ in added_records if op == "+")
        total_expense = sum(amt for op, amt, _, _ in added_records if op == "-")

        response = f"✅ <b>Добавлено записей: {len(added_records)}</b>\n\n"
        for op, amt, cat, record_date in added_records:
            icon = "💵" if op == "+" else "🛒"
            sign = "+" if op == "+" else "-"
            date_suffix = ""
            if record_date and record_date.date() != today:
                date_suffix = f" ({record_date.strftime('%d.%m')})"
            response += f"{icon} {sign}{amt:,.0f}₽ — {cat}{date_suffix}\n".replace(",", " ")

        response += "\n"
        if total_income > 0:
            response += f"📈 Доходы: +{total_income:,.0f}₽\n".replace(",", " ")
        if total_expense > 0:
            response += f"📉 Расходы: -{total_expense:,.0f}₽".replace(",", " ")

    if account_name:
        response += f"\n💳 Счёт: {account_name}"

    if errors:
        response += "\n\n⚠️ <b>Ошибки:</b>\n" + "\n".join(errors)

    return response.strip()


def parse_record_line(
    line: str, default_operation: str | None = None
) -> tuple[str, Decimal, str, datetime | None] | None:
    """Парсит строку записи и возвращает (операция, сумма, категория, дата) или None при ошибке.

    Форматы:
    - "1000 еда" — использует default_operation, сегодняшняя дата
    - "+1000 зарплата" — доход, сегодняшняя дата
    - "-500 еда" — расход, сегодняшняя дата
    - "27.01 500 еда" — указанная дата (ДД.ММ текущего года)
    - "27.01.25 500 еда" — указанная дата (ДД.ММ.ГГ)
    """
    line = line.strip()
    if not line:
        return None

    record_date = None

    # Проверяем дату в начале строки: ДД.ММ или ДД.ММ.ГГ
    # Ограничиваем день (1-31) и месяц (1-12) для избежания путаницы с суммами
    date_match = re.match(r"^(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])(?:\.(\d{2}))?\s+", line)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year_short = date_match.group(3)

        if year_short:
            year = 2000 + int(year_short)
        else:
            year = datetime.now(ZoneInfo(TIMEZONE)).year

        try:
            record_date = datetime(year, month, day, 12, 0, 0, tzinfo=ZoneInfo(TIMEZONE))
        except ValueError:
            # Невалидная дата (например 31.02) — отклоняем запись
            return None

        # Проверка на даты в далёком будущем (более 1 дня)
        now = datetime.now(ZoneInfo(TIMEZONE))
        if record_date.date() > (now + timedelta(days=1)).date():
            return None

        line = line[date_match.end():].strip()

    # Определяем операцию из знака в начале
    operation = default_operation
    if line.startswith("+"):
        operation = "+"
        line = line[1:].strip()
    elif line.startswith("-"):
        operation = "-"
        line = line[1:].strip()

    if not operation:
        return None

    # Ищем число
    match = re.search(r"(\d+(?:[.,]\d+)?)", line)
    if not match:
        return None

    try:
        amount = Decimal(match.group(1).replace(",", "."))
        if amount <= 0 or amount > Decimal(str(MAX_AMOUNT)):
            return None
    except (InvalidOperation, ValueError):
        return None

    # Категория — всё, что осталось после удаления суммы
    category = line.replace(match.group(0), "").strip()
    if not category:
        category = "Не указано"
    else:
        # Нормализация: первая буква заглавная, остальные строчные
        category = category.capitalize()

    if len(category) > MAX_CATEGORY_LENGTH:
        category = category[:MAX_CATEGORY_LENGTH]

    return operation, amount, category, record_date


@router.message(AddRecord.waiting_for_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при добавлении записи")
async def handle_amount_and_category(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Парсинг суммы и категории, затем запрос выбора счёта."""
    data = await state.get_data()
    default_operation = data.get("operation")

    lines = message.text.strip().split("\n")
    records_to_add = []
    errors = []

    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        result = parse_record_line(line, default_operation)
        if result:
            records_to_add.append(result)
        else:
            errors.append(f"Строка {i}: не удалось распознать")

    if not records_to_add:
        await message.answer(
            "Не удалось распознать записи.\n"
            "Формат: <code>1000 еда</code> или <code>+1000 зарплата</code>\n"
            "Можно несколько строк.",
            parse_mode="HTML",
        )
        return

    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        await state.clear()
        return

    # Сериализуем записи в state (Decimal → str, datetime → isoformat)
    serialized = [
        {
            "op": op,
            "amount": str(amt),
            "cat": cat,
            "date": dt.isoformat() if dt else None,
        }
        for op, amt, cat, dt in records_to_add
    ]
    await state.update_data(pending_records=serialized, parse_errors=errors, user_id=user_id)

    # Запрашиваем счёт
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        # Нет счетов — сохраняем без счёта
        added = await save_parsed_records(user_id, records_to_add)
        response = format_added_records_response(added, errors)
        await message.answer(response, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        await state.clear()
        return

    await message.answer(
        "💳 <b>Выберите счёт:</b>",
        reply_markup=account_select_keyboard(accounts),
        parse_mode="HTML",
    )
    await state.set_state(AddRecord.waiting_for_account)


@router.callback_query(F.data.startswith("acc_select:"), AddRecord.waiting_for_account)
@log_exceptions("Ошибка при выборе счёта")
async def handle_record_account_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет записи с выбранным счётом."""
    try:
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("user_id")
    serialized = data.get("pending_records", [])
    errors = data.get("parse_errors", [])

    records_to_add = _deserialize_records(serialized)
    added = await save_parsed_records(user_id, records_to_add, account_id)

    # Узнаём имя счёта для отображения в ответе
    account_name: str | None = None
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        for acc in accounts:
            if acc.id == account_id:
                account_name = acc.name
                break

    response = format_added_records_response(added, errors, account_name=account_name)
    await callback.message.edit_text(response, parse_mode="HTML")
    await callback.message.answer("Выберите действие:", reply_markup=main_menu_keyboard())
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "acc_skip", AddRecord.waiting_for_account)
@log_exceptions("Ошибка при пропуске выбора счёта")
async def handle_record_account_skip(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет записи с первым счётом (по умолчанию)."""
    data = await state.get_data()
    user_id = data.get("user_id")
    serialized = data.get("pending_records", [])
    errors = data.get("parse_errors", [])

    records_to_add = _deserialize_records(serialized)

    account_id: int | None = None
    account_name: str | None = None
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        if accounts:
            account_id = accounts[0].id
            account_name = accounts[0].name

    added = await save_parsed_records(user_id, records_to_add, account_id)
    response = format_added_records_response(added, errors, account_name=account_name)
    await callback.message.edit_text(response, parse_mode="HTML")
    await callback.message.answer("Выберите действие:", reply_markup=main_menu_keyboard())
    await state.clear()
    await callback.answer()


@router.message(StateFilter(None), F.text.regexp(r"^([+-]\d|\d{1,2}\.\d{1,2}\.?\d{0,2}\s+[+-]?\d)"))
@log_exceptions("Ошибка при добавлении записи")
async def handle_direct_record(message: Message, **kwargs) -> None:
    """Прямой ввод записей без нажатия кнопки (если начинается с +/- или с даты)."""
    lines = message.text.strip().split("\n")

    # Парсим все записи
    records_to_add = []
    errors = []

    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue

        result = parse_record_line(line, default_operation=None)
        if result:
            records_to_add.append(result)
        else:
            errors.append(f"Строка {i}: не удалось распознать")

    if not records_to_add:
        await message.answer(
            "Не удалось распознать записи.\n"
            "Формат: <code>+1000 зарплата</code> или <code>-500 еда</code>",
            parse_mode="HTML",
        )
        return

    # Получаем user_id (из middleware или БД)
    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return

    # Для быстрого ввода берём первый счёт молча (без диалога)
    account_id: int | None = None
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        if accounts:
            account_id = accounts[0].id

    added_records = await save_parsed_records(user_id, records_to_add, account_id)

    if not added_records:
        await message.answer("Не удалось сохранить записи.", reply_markup=main_menu_keyboard())
        return

    response = format_added_records_response(added_records, errors)
    await message.answer(response, reply_markup=main_menu_keyboard(), parse_mode="HTML")


# ==================== История операций ====================

# Названия периодов для отображения в заголовке истории
PERIOD_NAMES = {
    "day": "сегодня",
    "yesterday": "вчера",
    "week": "7 дней",
    "month30": "30 дней",
    "month": "этот месяц",
    "prev_month": "прошлый месяц",
    "year": "этот год",
    "range": "выбранный период",
}


def build_history_page(
    page_records: list[Record],
    page: int,
    total_pages: int,
    income_sum: Decimal,
    expense_sum: Decimal,
    period: str = "",
    period_label: str = "",
    total_count: int = 0,
    header: str = "",
) -> tuple[str, InlineKeyboardBuilder]:
    """Формирует текст истории и кнопки навигации для указанной страницы.

    Args:
        page_records: Записи текущей страницы (ORM-объекты Record)
        page: Номер страницы (с 0)
        total_pages: Общее количество страниц
        income_sum: Сумма доходов (посчитана в БД)
        expense_sum: Сумма расходов (посчитана в БД)
        period: Код периода (day, week, month и т.д.)
        period_label: Пользовательское название периода (для range)
        total_count: Общее количество записей (для кнопки "Показать все")

    Returns:
        (текст, клавиатура)
    """
    remaining = income_sum - expense_sum

    # Группировка по датам (работаем напрямую с ORM-объектами)
    grouped: dict[str, list] = {}
    for r in page_records:
        date_key = r.created_at.strftime("%d.%m.%y")
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(r)

    # Определяем название периода
    if period_label:
        period_name = period_label
    else:
        period_name = PERIOD_NAMES.get(period, "")

    # Формируем заголовок
    if header:
        text = f"{header} • {period_name}\n\n" if period_name else f"{header}\n\n"
    else:
        text = f"📊 <b>История</b> • {period_name}\n\n" if period_name else "📊 <b>История</b>\n\n"

    for date_str, day_records in grouped.items():
        # Итог дня
        day_income = sum(float(r.amount) for r in day_records if r.operation == "+")
        day_expense = sum(float(r.amount) for r in day_records if r.operation == "-")
        day_total = day_income - day_expense

        # День недели (короткий формат даты без года)
        weekday = RU_WEEKDAYS[day_records[0].created_at.weekday()]
        short_date = ".".join(date_str.split(".")[:2])  # "27.01.26" -> "27.01"

        # Заголовок дня
        total_sign = "+" if day_total >= 0 else ""
        text += f"▸ <b>{weekday}, {short_date}</b> │ {total_sign}{day_total:,.0f}₽\n".replace(",", " ")

        # Операции дня
        for r in day_records:
            sign = "+" if r.operation == "+" else "-"
            category = r.category or ""
            text += f"   {sign}{float(r.amount):,.0f}₽ {category}\n".replace(",", " ")

        text += "\n"

    # Разделитель и итоги
    text += "─────────────────\n"
    text += f"📈 Доход: {format_money(income_sum)}\n"
    text += f"📉 Расход: {format_money(expense_sum)}\n"
    balance_sign = "+" if remaining >= 0 else ""
    text += f"💰 Баланс: {balance_sign}{format_money(remaining)}"

    # Кнопки навигации (только если страниц > 1)
    kb = InlineKeyboardBuilder()
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(("◀ Назад", f"hist_page:{page - 1}"))
        nav_buttons.append((f"{page + 1}/{total_pages}", "hist_page:noop"))
        if page < total_pages - 1:
            nav_buttons.append(("Вперёд ▶", f"hist_page:{page + 1}"))

        for btn_text, data in nav_buttons:
            kb.button(text=btn_text, callback_data=data)
        kb.adjust(len(nav_buttons))

        # Кнопка "Показать все" (если записей не слишком много)
        if total_count > 0 and total_count <= MAX_SHOW_ALL_RECORDS:
            kb.button(text=f"Показать все ({total_count})", callback_data="hist_show_all")
            kb.adjust(len(nav_buttons), 1)

    return text, kb


@router.message(F.func(is_history))
@log_exceptions("Ошибка при показе истории")
async def menu_history(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка История — показываем выбор периода."""
    await state.clear()
    await message.answer(
        "За какой период показать историю?",
        reply_markup=history_period_keyboard(),
    )
    await state.set_state(MenuStates.waiting_for_history_period)


@router.callback_query(MenuStates.waiting_for_history_period)
@log_exceptions("Ошибка при получении истории")
async def menu_history_period(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран период — загружаем первую страницу записей."""
    # Парсим период из callback_data (формат: "hist_period:day")
    try:
        period = callback.data.split(":")[1]
    except (IndexError, AttributeError):
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    # Обработка "Свой период" — переход к текстовому вводу дат
    if period == "custom":
        await callback.message.edit_text(
            "Введите период в формате:\n"
            "<code>01.01.25 - 31.01.25</code>\n\n"
            "Или отправьте /cancel для отмены.",
            parse_mode="HTML",
        )
        await state.set_state(MenuStates.waiting_for_custom_period)
        await callback.answer()
        return

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            return

        # Один вызов вместо трёх (count + totals + records)
        total_count, income_sum, expense_sum, records = await get_history_data(
            session, user.id, period, limit=RECORDS_PER_PAGE, offset=0
        )

        if total_count == 0:
            await callback.message.edit_text("Записей не найдено за указанный период.")
            await state.clear()
            await callback.answer()
            return

        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    # Сохраняем в state только параметры, не все записи
    await state.update_data(
        history_period=period,
        history_page=0,
        history_total_pages=total_pages,
        history_total_count=total_count,
        history_income=str(income_sum),
        history_expense=str(expense_sum),
    )

    # Показываем первую страницу (передаём ORM-объекты напрямую)
    text, kb = build_history_page(records, 0, total_pages, income_sum, expense_sum, period=period, total_count=total_count)

    if total_pages > 1:
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await state.set_state(MenuStates.waiting_for_history_page)
    else:
        await callback.message.edit_text(text, parse_mode="HTML")
        await state.clear()
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_history_page, F.data.startswith("hist_page:"))
@log_exceptions("Ошибка при навигации по истории")
async def menu_history_page(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Навигация по страницам истории (кнопки Назад/Вперёд)."""
    # Парсим номер страницы
    try:
        page_str = callback.data.split(":")[1]
        if page_str == "noop":  # Клик по номеру страницы — игнорируем
            await callback.answer()
            return
        new_page = int(page_str)
    except (IndexError, ValueError, AttributeError):
        await callback.answer("Некорректные данные.")
        return

    # Получаем параметры из state
    data = await state.get_data()
    period = data.get("history_period")
    total_pages = data.get("history_total_pages", 1)
    income_sum = Decimal(data.get("history_income", "0"))
    expense_sum = Decimal(data.get("history_expense", "0"))

    # Проверка границ пагинации
    if new_page < 0 or new_page >= total_pages:
        await callback.answer("Страница не существует.")
        return

    # Для "range" периода получаем сохранённые даты
    date_from = None
    date_to = None
    if period == "range":
        date_from_str = data.get("history_date_from")
        date_to_str = data.get("history_date_to")
        if date_from_str and date_to_str:
            date_from = datetime.fromisoformat(date_from_str)
            date_to = datetime.fromisoformat(date_to_str)

    if not period:
        await callback.message.edit_text("Данные истории устарели. Попробуйте снова.")
        await state.clear()
        await callback.answer()
        return

    # Загружаем записи нужной страницы из БД
    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            await callback.answer()
            return

        offset = new_page * RECORDS_PER_PAGE
        records = await get_records(session, user.id, period, date_from, date_to, limit=RECORDS_PER_PAGE, offset=offset)

    # Получаем label и total_count для периода (если есть)
    period_label = data.get("history_period_label", "")
    total_count = data.get("history_total_count", 0)

    # Обновляем страницу в state
    await state.update_data(history_page=new_page)
    text, kb = build_history_page(records, new_page, total_pages, income_sum, expense_sum, period=period, period_label=period_label, total_count=total_count)
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_history_page, F.data == "hist_show_all")
@log_exceptions("Ошибка при показе всех записей")
async def menu_history_show_all(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показать все записи без пагинации."""
    data = await state.get_data()
    period = data.get("history_period")
    period_label = data.get("history_period_label", "")
    total_count = data.get("history_total_count", 0)
    income_sum = Decimal(data.get("history_income", "0"))
    expense_sum = Decimal(data.get("history_expense", "0"))

    # Для "range" периода получаем сохранённые даты
    date_from = None
    date_to = None
    if period == "range":
        date_from_str = data.get("history_date_from")
        date_to_str = data.get("history_date_to")
        if date_from_str and date_to_str:
            date_from = datetime.fromisoformat(date_from_str)
            date_to = datetime.fromisoformat(date_to_str)

    if not period:
        await callback.message.edit_text("Данные истории устарели. Попробуйте снова.")
        await state.clear()
        await callback.answer()
        return

    # Загружаем все записи
    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            await callback.answer()
            return

        records = await get_records(session, user.id, period, date_from, date_to, limit=MAX_SHOW_ALL_RECORDS, offset=0)

    # Переиспользуем build_history_page (без дублирования кода)
    text, _ = build_history_page(
        records, 0, 1, income_sum, expense_sum,
        period=period, period_label=period_label, total_count=total_count
    )

    # Проверяем длину сообщения (лимит Telegram — 4096 символов)
    if len(text) > MAX_MESSAGE_LENGTH - 100:
        text = text[:MAX_MESSAGE_LENGTH - 150] + "\n\n... (сообщение обрезано)"

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.clear()
    await callback.answer()


@router.message(MenuStates.waiting_for_custom_period, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при обработке своего периода")
async def menu_history_custom_period(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Обработка текстового ввода дат для своего периода."""
    text = message.text.strip()

    # Парсим даты из текста (формат: "01.01.25 - 31.01.25")
    match = re.match(r"(\d{1,2}\.\d{1,2}\.\d{2,4})\s*[-–—]\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", text)
    if not match:
        await message.answer(
            "Неверный формат. Введите период в формате:\n"
            "<code>01.01.25 - 31.01.25</code>\n\n"
            "Или отправьте /cancel для отмены.",
            parse_mode="HTML",
        )
        return

    # Парсим начальную и конечную даты
    date_from_str, date_to_str = match.groups()

    try:
        date_from = datetime.strptime(date_from_str, "%d.%m.%y" if len(date_from_str.split(".")[-1]) == 2 else "%d.%m.%Y")
        date_to = datetime.strptime(date_to_str, "%d.%m.%y" if len(date_to_str.split(".")[-1]) == 2 else "%d.%m.%Y")
    except ValueError:
        await message.answer(
            "Неверный формат даты. Используйте формат ДД.ММ.ГГ или ДД.ММ.ГГГГ\n"
            "Например: <code>01.01.25 - 31.01.25</code>",
            parse_mode="HTML",
        )
        return

    # Проверяем, что начальная дата не позже конечной
    if date_from > date_to:
        await message.answer("Начальная дата не может быть позже конечной.")
        return

    # Проверяем, что даты не в будущем
    now = datetime.now(ZoneInfo(TIMEZONE))
    if date_from.replace(tzinfo=ZoneInfo(TIMEZONE)) > now:
        await message.answer("Начальная дата не может быть в будущем.")
        return

    # Устанавливаем время для конечной даты (конец дня)
    date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo(TIMEZONE))
    date_to = date_to.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=ZoneInfo(TIMEZONE))

    # Загружаем записи за указанный период (один запрос вместо трёх)
    async with async_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Пользователь не найден.")
            await state.clear()
            return

        total_count, income_sum, expense_sum, records = await get_history_data(
            session, user.id, "range", date_from, date_to, limit=RECORDS_PER_PAGE, offset=0
        )

        if total_count == 0:
            await message.answer("Записей не найдено за указанный период.", reply_markup=main_menu_keyboard())
            await state.clear()
            return

        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    # Формируем label для периода
    period_label = f"{date_from.strftime('%d.%m.%y')} - {date_to.strftime('%d.%m.%y')}"

    # Сохраняем параметры в state
    await state.update_data(
        history_period="range",
        history_period_label=period_label,
        history_date_from=date_from.isoformat(),
        history_date_to=date_to.isoformat(),
        history_page=0,
        history_total_pages=total_pages,
        history_total_count=total_count,
        history_income=str(income_sum),
        history_expense=str(expense_sum),
    )

    text, kb = build_history_page(records, 0, total_pages, income_sum, expense_sum, period="range", period_label=period_label, total_count=total_count)

    if total_pages > 1:
        await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await state.set_state(MenuStates.waiting_for_history_page)
    else:
        await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        await state.clear()


# ==================== Отчёт (график) ====================

@router.message(StateFilter("*"), F.func(is_report))
@log_exceptions("Произошла ошибка при формировании отчёта")
async def menu_report(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка Отчёт — проверяем наличие записей, просим выбрать тип."""
    await state.clear()
    async with async_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Пользователь не найден.")
            return
        years_months = await get_available_years_and_months(session, user.id)

    if not years_months:
        await message.answer("Нет записей для отображения отчёта.")
        return

    # Кэшируем years_months в state (не запрашивать повторно)
    await state.update_data(report_years_months=years_months)
    await message.answer("Выберите тип отчёта:", reply_markup=report_type_keyboard())
    await state.set_state(MenuStates.waiting_for_report_type)


@router.message(MenuStates.waiting_for_report_type)
@log_exceptions("Ошибка при выборе типа отчёта")
async def report_type_handler(message: Message, state: FSMContext, **kwargs) -> None:
    """Выбор типа отчёта (Доход/Расход) — показываем выбор года."""
    if is_income(message):
        report_type = "Доход"
        operation = "+"
    elif is_expense(message):
        report_type = "Расход"
        operation = "-"
    else:
        await message.answer(
            "Пожалуйста, выберите тип отчёта:",
            reply_markup=report_type_keyboard(),
        )
        return

    await state.update_data(report_type=report_type)

    # Запрашиваем годы/месяцы с учётом типа операции
    async with async_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Пользователь не найден.")
            await state.clear()
            return
        years_months = await get_available_years_and_months(session, user.id, operation)

    if not years_months:
        await message.answer(
            f"Нет записей по категории «{report_type}» для отображения отчёта.",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    # Кэшируем отфильтрованные данные
    await state.update_data(report_years_months=years_months)

    # Возвращаем основную клавиатуру
    await message.answer("Тип отчёта: " + report_type, reply_markup=main_menu_keyboard())
    # Показываем inline-клавиатуру с годами
    keyboard = get_years_keyboard(list(years_months.keys()))
    await message.answer("Выберите год:", reply_markup=keyboard)
    await state.set_state(MenuStates.waiting_for_report_year)


@router.callback_query(MenuStates.waiting_for_report_year)
@log_exceptions("Ошибка при получении месяцев для отчёта")
async def menu_report_year(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран год — показываем доступные месяцы."""
    # Парсим год из callback_data
    try:
        year = int(callback.data.split(":")[1])
    except (IndexError, ValueError, AttributeError):
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    now = datetime.now(ZoneInfo(TIMEZONE))
    current_year = now.year
    current_month = now.month

    # Используем закэшированные данные из state
    data = await state.get_data()
    years_months = data.get("report_years_months", {})

    if year not in years_months:
        await callback.answer("Нет записей за этот год.")
        await state.clear()
        return

    # Фильтруем будущие месяцы
    available_months = [
        month
        for month in years_months[year]
        if year < current_year or (year == current_year and month <= current_month)
    ]

    if not available_months:
        await callback.answer("Нет доступных месяцев для отчета.")
        await state.clear()
        return

    keyboard = get_months_keyboard(year, available_months)
    await callback.message.edit_text(
        f"Выберите месяц {year} года:", reply_markup=keyboard
    )
    await state.update_data(report_year=year)
    await state.set_state(MenuStates.waiting_for_report_month)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_report_month)
@log_exceptions("Ошибка при формировании отчёта")
async def menu_report_month(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Выбран месяц — генерируем график и текстовый отчёт."""
    # Парсим год и месяц из callback_data (формат: "report_month:2024:6")
    try:
        parts = callback.data.split(":")
        year = int(parts[1])
        month = int(parts[2])
    except (IndexError, ValueError, AttributeError):
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    await state.update_data(report_year=year, report_month=month)
    data = await state.get_data()
    raw_type = data.get("report_type")

    # Определяем тип отчёта и знак операции
    if raw_type == "Доход":
        report_type = "income"
        operation_sign = "+"
    elif raw_type == "Расход":
        report_type = "expense"
        operation_sign = "-"
    else:
        await callback.message.edit_text("Ошибка: не выбран тип отчёта.")
        await state.clear()
        await callback.answer()
        return

    # Проверка на будущий месяц
    now = datetime.now(ZoneInfo(TIMEZONE))
    if year > now.year or (year == now.year and month > now.month):
        await callback.message.edit_text("Нельзя получить отчет за будущий месяц.")
        await state.clear()
        await callback.answer()
        return

    # Диапазон дат для запроса
    date_from = datetime(year, month, 1)
    if month == 12:
        date_to = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        date_to = datetime(year, month + 1, 1) - timedelta(seconds=1)

    # Показываем сообщение о генерации (улучшает UX при медленном соединении)
    await callback.message.edit_text("⏳ Генерация отчёта...")
    await callback.answer()

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            return

        # Получаем суммы по категориям через SQL GROUP BY (оптимизировано)
        categories = await get_categories_summary(
            session, user.id, operation_sign, date_from, date_to
        )
        total = sum(categories.values()) if categories else Decimal("0.0")

        # Загружаем записи для детализации
        records = await get_records(session, user.id, "range", date_from, date_to)

        # Генерируем график с полным отчётом (если есть данные)
        if categories:
            buf, caption = await build_report_pie(categories, total, date_from, report_type, records)

            # Кнопка "Сравнить с прошлым месяцем"
            compare_kb = InlineKeyboardBuilder()
            compare_kb.button(
                text="📊 Сравнить с прошлым месяцем",
                callback_data=f"compare:{report_type}:{year}:{month}",
            )

            if buf:
                await callback.message.answer_photo(
                    photo=BufferedInputFile(buf.read(), filename="report.png"),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=compare_kb.as_markup(),
                )
            else:
                # Если график не сгенерировался — отправляем только текст
                await callback.message.answer(
                    caption,
                    parse_mode="HTML",
                    reply_markup=compare_kb.as_markup(),
                )
        else:
            await callback.message.answer("Нет данных за выбранный период.")

    # Удаляем сообщение "Генерация..."
    try:
        await callback.message.delete()
    except Exception:
        pass

    await state.clear()


# ==================== Сравнение периодов ====================

@router.callback_query(F.data.startswith("compare:"))
@log_exceptions("Ошибка при сравнении периодов")
async def handle_compare_periods(callback: CallbackQuery, **kwargs) -> None:
    """Сравнение текущего месяца с предыдущим."""
    # Парсим данные из callback_data (формат: "compare:expense:2025:1")
    try:
        parts = callback.data.split(":")
        report_type = parts[1]  # "income" или "expense"
        year = int(parts[2])
        month = int(parts[3])
    except (IndexError, ValueError, AttributeError):
        await callback.answer("Некорректные данные.")
        return

    operation_sign = "+" if report_type == "income" else "-"

    # Вычисляем предыдущий месяц
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    # Диапазоны дат
    cur_date_from = datetime(year, month, 1, tzinfo=ZoneInfo(TIMEZONE))
    if month == 12:
        cur_date_to = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(TIMEZONE)) - timedelta(seconds=1)
    else:
        cur_date_to = datetime(year, month + 1, 1, tzinfo=ZoneInfo(TIMEZONE)) - timedelta(seconds=1)

    prev_date_from = datetime(prev_year, prev_month, 1, tzinfo=ZoneInfo(TIMEZONE))
    if prev_month == 12:
        prev_date_to = datetime(prev_year + 1, 1, 1, tzinfo=ZoneInfo(TIMEZONE)) - timedelta(seconds=1)
    else:
        prev_date_to = datetime(prev_year, prev_month + 1, 1, tzinfo=ZoneInfo(TIMEZONE)) - timedelta(seconds=1)

    await callback.answer("⏳ Формирую сравнение...")

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.answer("Пользователь не найден.")
            return

        # Получаем данные за текущий и предыдущий месяцы
        cur_categories = await get_categories_summary(
            session, user.id, operation_sign, cur_date_from, cur_date_to
        )
        prev_categories = await get_categories_summary(
            session, user.id, operation_sign, prev_date_from, prev_date_to
        )

        cur_total = sum(cur_categories.values()) if cur_categories else Decimal("0")
        prev_total = sum(prev_categories.values()) if prev_categories else Decimal("0")

        # Получаем данные для тренда (за год)
        monthly_data = await get_monthly_totals(session, user.id, operation_sign)

    # Проверяем наличие данных за предыдущий месяц
    if not prev_categories:
        await callback.message.answer(
            f"Нет данных за {RU_MONTHS[prev_month]} {prev_year} для сравнения."
        )
        return

    # Вычисляем средний расход/доход
    avg_monthly = None
    if monthly_data:
        avg_monthly = sum(v for _, _, v in monthly_data) / len(monthly_data)

    # Формируем текст сравнения
    comparison_text = make_comparison_text(
        current_categories=cur_categories,
        prev_categories=prev_categories,
        current_total=cur_total,
        prev_total=prev_total,
        current_month=(year, month),
        prev_month=(prev_year, prev_month),
        report_type=report_type,
        avg_monthly=avg_monthly,
    )

    # Строим график тренда
    if monthly_data and len(monthly_data) >= 2:
        chart_buf = await build_trend_chart(
            monthly_data=monthly_data,
            report_type=report_type,
            current_month=(year, month),
            prev_month=(prev_year, prev_month),
        )

        if chart_buf:
            await callback.message.answer_photo(
                photo=BufferedInputFile(chart_buf.read(), filename="trend.png"),
                caption=comparison_text,
                parse_mode="HTML",
            )
        else:
            await callback.message.answer(comparison_text, parse_mode="HTML")
    else:
        await callback.message.answer(comparison_text, parse_mode="HTML")


# ==================== Удаление записи ====================

def build_delete_keyboard(
    page_records: list[dict[str, Any]],
    page: int,
    total_pages: int,
) -> InlineKeyboardBuilder:
    """Формирует клавиатуру со списком записей для удаления.

    Args:
        page_records: Записи текущей страницы (уже загружены с LIMIT/OFFSET)
        page: Номер страницы (с 0)
        total_pages: Общее количество страниц

    Returns:
        Клавиатура с кнопками записей и навигацией
    """
    kb = InlineKeyboardBuilder()

    # Кнопки с записями (компактный формат) — каждая в отдельном ряду
    for r in page_records:
        icon = "🛒" if r["operation"] == "-" else "💵"
        # Дата в формате ДД.ММ.ГГ
        short_date = r["created_at"].strftime("%d.%m.%y")
        # Сокращаем категорию если слишком длинная (макс ~12 символов)
        cat = r["category"][:12] + "…" if len(r["category"]) > 12 else r["category"]
        text = f"{icon} {r['amount']:.0f}₽ {cat} {short_date}"
        kb.button(text=text, callback_data=f"del_record:{r['id']}")

    # Размещаем все записи по 1 в ряд
    num_records = len(page_records)
    if num_records > 0:
        kb.adjust(*([1] * num_records))

    # Кнопки навигации (только если страниц > 1)
    if total_pages > 1:
        nav_kb = InlineKeyboardBuilder()
        if page > 0:
            nav_kb.button(text="◀ Назад", callback_data=f"del_page:{page - 1}")
        nav_kb.button(text=f"{page + 1}/{total_pages}", callback_data="del_page:noop")
        if page < total_pages - 1:
            nav_kb.button(text="Вперёд ▶", callback_data=f"del_page:{page + 1}")
        nav_kb.adjust(3)  # Навигация в одном ряду
        kb.attach(nav_kb)

    # Кнопка отмены
    cancel_kb = InlineKeyboardBuilder()
    cancel_kb.button(text="Отмена", callback_data="cancel")
    kb.attach(cancel_kb)

    return kb


@router.message(StateFilter("*"), F.func(is_delete))
@log_exceptions("Ошибка при показе меню удаления")
async def menu_delete(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка Удалить — показываем выбор периода."""
    await state.clear()
    await message.answer(
        "За какой период показать записи для удаления?",
        reply_markup=delete_period_keyboard(),
    )
    await state.set_state(MenuStates.waiting_for_delete_period)


@router.callback_query(MenuStates.waiting_for_delete_period)
@log_exceptions("Ошибка при получении записей для удаления")
async def menu_delete_period(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка выбора периода или навигации по годам/месяцам."""

    # --- Кнопка "Выбрать месяц" — показываем годы ---
    if callback.data == "del_select_month":
        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                return

            years_months = await get_available_years_and_months(session, user.id)

        if not years_months:
            await callback.message.edit_text("У вас пока нет записей.")
            await state.clear()
            await callback.answer()
            return

        years = list(years_months.keys())
        await state.update_data(delete_years_months=years_months)
        await callback.message.edit_text(
            "Выберите год:",
            reply_markup=get_delete_years_keyboard(years),
        )
        await callback.answer()
        return

    # --- Выбран год — показываем месяцы ---
    if callback.data.startswith("del_year:"):
        try:
            year = int(callback.data.split(":")[1])
        except (IndexError, ValueError):
            await callback.answer("Некорректные данные.")
            return

        data = await state.get_data()
        years_months = data.get("delete_years_months", {})
        months = years_months.get(year, [])

        if not months:
            await callback.answer("Нет записей за этот год.")
            return

        await state.update_data(delete_selected_year=year)
        await callback.message.edit_text(
            f"<b>{year}</b> — выберите месяц:",
            reply_markup=get_delete_months_keyboard(year, months),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    # --- Выбран месяц — показываем записи ---
    if callback.data.startswith("del_month:"):
        try:
            parts = callback.data.split(":")
            year = int(parts[1])
            month = int(parts[2])
        except (IndexError, ValueError):
            await callback.answer("Некорректные данные.")
            return

        # Вычисляем диапазон дат для месяца
        from calendar import monthrange
        start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=ZoneInfo(TIMEZONE))
        last_day = monthrange(year, month)[1]
        end_date = datetime(year, month, last_day, 23, 59, 59, tzinfo=ZoneInfo(TIMEZONE))

        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                return

            total_count = await count_records(session, user.id, "range", start_date, end_date)
            if total_count == 0:
                await callback.answer("Записей за этот месяц нет.")
                return

            total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
            records = await get_records(
                session, user.id, "range", start_date, end_date,
                limit=RECORDS_PER_PAGE, offset=0
            )

        records_data = [r.to_dict(include_id=True) for r in records]

        await state.update_data(
            delete_period="range",
            delete_date_from=start_date,
            delete_date_to=end_date,
            delete_page=0,
            delete_total_count=total_count,
            delete_total_pages=total_pages,
            delete_selected_year=year,
            delete_selected_month=month,
        )

        kb = build_delete_keyboard(records_data, 0, total_pages)
        await callback.message.edit_text(
            f"Записи за {RU_MONTHS[month]} {year} (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await state.set_state(MenuStates.waiting_for_delete_record)
        await callback.answer()
        return

    # --- Назад к выбору периода ---
    if callback.data == "del_back_to_period":
        await callback.message.edit_text(
            "За какой период показать записи для удаления?",
            reply_markup=delete_period_keyboard(),
        )
        await callback.answer()
        return

    # --- Назад к выбору года ---
    if callback.data == "del_back_to_years":
        data = await state.get_data()
        years_months = data.get("delete_years_months", {})
        years = list(years_months.keys())

        await callback.message.edit_text(
            "Выберите год:",
            reply_markup=get_delete_years_keyboard(years),
        )
        await callback.answer()
        return

    # --- Стандартный выбор периода (day/month/year/yesterday) ---
    if callback.data.startswith("del_period:"):
        try:
            period = callback.data.split(":")[1]
        except (IndexError, AttributeError):
            await callback.answer("Некорректные данные.")
            await state.clear()
            return

        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                return

            total_count = await count_records(session, user.id, period)
            if total_count == 0:
                await callback.message.edit_text("Записей за выбранный период нет.")
                await state.clear()
                await callback.answer()
                return

            total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
            records = await get_records(session, user.id, period, limit=RECORDS_PER_PAGE, offset=0)

        records_data = [r.to_dict(include_id=True) for r in records]

        await state.update_data(
            delete_period=period,
            delete_page=0,
            delete_total_count=total_count,
            delete_total_pages=total_pages,
        )

        kb = build_delete_keyboard(records_data, 0, total_pages)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await state.set_state(MenuStates.waiting_for_delete_record)
        await callback.answer()
        return

    # --- Неизвестный callback ---
    await callback.answer("Некорректные данные.")


@router.callback_query(MenuStates.waiting_for_delete_record)
@log_exceptions("Ошибка при удалении записи")
async def menu_delete_record(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка: навигация по страницам или удаление выбранной записи."""
    data = await state.get_data()
    period = data.get("delete_period")
    total_count = data.get("delete_total_count", 0)
    date_from = data.get("delete_date_from")
    date_to = data.get("delete_date_to")

    if not period:
        await callback.answer("Данные устарели. Попробуйте снова.")
        await state.clear()
        return

    # --- Навигация по страницам ---
    if callback.data.startswith("del_page:"):
        try:
            page_str = callback.data.split(":")[1]
            if page_str == "noop":  # Клик по номеру страницы
                await callback.answer()
                return
            new_page = int(page_str)
        except (IndexError, ValueError, AttributeError):
            await callback.answer("Некорректные данные.")
            return

        # Проверка границ пагинации
        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
        if new_page < 0 or new_page >= total_pages:
            await callback.answer("Страница не существует.")
            return

        # Загружаем записи нужной страницы из БД
        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                await callback.answer()
                return

            offset = new_page * RECORDS_PER_PAGE
            records = await get_records(
                session, user.id, period, date_from, date_to,
                limit=RECORDS_PER_PAGE, offset=offset
            )

        records_data = [r.to_dict(include_id=True) for r in records]

        await state.update_data(delete_page=new_page)
        kb = build_delete_keyboard(records_data, new_page, total_pages)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await callback.answer()
        return

    # --- Выбор записи для удаления (показываем подтверждение) ---
    if callback.data.startswith("del_record:"):
        try:
            record_id = int(callback.data.split(":")[1])
        except (IndexError, ValueError, AttributeError):
            await callback.answer("Некорректные данные.")
            await state.clear()
            return

        # Сохраняем ID записи для подтверждения
        await state.update_data(delete_record_id=record_id)
        await callback.message.edit_text(
            "⚠️ Вы уверены, что хотите удалить эту запись?",
            reply_markup=confirm_delete_keyboard(record_id),
        )
        await state.set_state(MenuStates.waiting_for_delete_confirm)
        await callback.answer()
        return

    # --- Неизвестный callback ---
    await callback.answer("Некорректные данные.")
    await state.clear()


@router.callback_query(MenuStates.waiting_for_delete_confirm)
@log_exceptions("Ошибка при подтверждении удаления")
async def menu_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка подтверждения или отмены удаления."""
    data = await state.get_data()
    period = data.get("delete_period")
    current_page = data.get("delete_page", 0)
    date_from = data.get("delete_date_from")
    date_to = data.get("delete_date_to")

    # --- Отмена удаления ---
    if callback.data == "cancel_del":
        # Возвращаемся к списку записей
        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                await callback.answer()
                return

            total_count = await count_records(session, user.id, period, date_from, date_to)
            if total_count == 0:
                await callback.message.edit_text("Записей нет.")
                await state.clear()
                await callback.answer()
                return

            total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
            if current_page >= total_pages:
                current_page = total_pages - 1

            offset = current_page * RECORDS_PER_PAGE
            records = await get_records(
                session, user.id, period, date_from, date_to,
                limit=RECORDS_PER_PAGE, offset=offset
            )

        records_data = [r.to_dict(include_id=True) for r in records]
        kb = build_delete_keyboard(records_data, current_page, total_pages)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await state.set_state(MenuStates.waiting_for_delete_record)
        await callback.answer("Удаление отменено")
        return

    # --- Подтверждение удаления ---
    if callback.data.startswith("confirm_del:"):
        try:
            record_id = int(callback.data.split(":")[1])
        except (IndexError, ValueError, AttributeError):
            await callback.answer("Некорректные данные.")
            await state.clear()
            return

        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.answer("Пользователь не найден.")
                await state.clear()
                return

            # Сохраняем user_id до commit (после commit объект user станет expired)
            user_id = user.id
            deleted = await delete_record(session, user_id, record_id)

            if deleted:
                await callback.answer("✅ Запись удалена!")

                # Пересчитываем общее количество
                new_total = await count_records(session, user_id, period, date_from, date_to)

                # Если всё удалено — выходим
                if new_total == 0:
                    await callback.message.edit_text("Все записи удалены.")
                    await state.clear()
                    return

                # Корректируем страницу
                total_pages = (new_total + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
                if current_page >= total_pages:
                    current_page = total_pages - 1

                # Загружаем текущую страницу заново
                offset = current_page * RECORDS_PER_PAGE
                records = await get_records(
                    session, user_id, period, date_from, date_to,
                    limit=RECORDS_PER_PAGE, offset=offset
                )

                records_data = [r.to_dict(include_id=True) for r in records]

                await state.update_data(
                    delete_page=current_page,
                    delete_total_count=new_total,
                    delete_total_pages=total_pages,
                )

                kb = build_delete_keyboard(records_data, current_page, total_pages)
                await callback.message.edit_text(
                    f"Выберите запись для удаления (всего: {new_total}):",
                    reply_markup=kb.as_markup(),
                )
                await state.set_state(MenuStates.waiting_for_delete_record)
            else:
                await callback.answer("⚠️ Запись не найдена или уже удалена.")
                await state.set_state(MenuStates.waiting_for_delete_record)
        return

    # --- Неизвестный callback ---
    await callback.answer("Некорректные данные.")
    await state.clear()


# ==================== Счета ====================

def _build_accounts_text(balances: list[tuple]) -> str:
    """Формирует текст с балансами по счетам."""
    if not balances:
        return "💳 <b>Мои счета</b>\n\nСчетов нет. Нажмите ➕ Создать."

    lines = ["💳 <b>Мои счета</b>\n"]
    total = Decimal("0")
    for acc, balance in balances:
        sign = "-" if balance < 0 else ""
        formatted = f"{sign}{abs(balance):,.0f}₽".replace(",", " ")
        lines.append(f"<b>{acc.name}</b>  —  {formatted}")
        total += balance

    lines.append("\n" + "─" * 22)
    sign = "-" if total < 0 else ""
    total_str = f"{sign}{abs(total):,.0f}₽".replace(",", " ")
    lines.append(f"Всего:  <b>{total_str}</b>")
    return "\n".join(lines)


@router.message(StateFilter("*"), F.func(is_accounts))
@log_exceptions("Ошибка при отображении счетов")
async def handle_accounts(message: Message, state: FSMContext, **kwargs) -> None:
    """Показывает балансы по счетам и меню управления."""
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return

    async with async_session() as session:
        balances = await get_account_balances(session, user_id)

    await message.answer(
        _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )


# --- Создать счёт ---

@router.callback_query(F.data == "acc_create")
@log_exceptions("Ошибка при создании счёта")
async def handle_acc_create(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Запрашивает название нового счёта."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if len(accounts) >= MAX_ACCOUNTS_PER_USER:
        await callback.answer(f"Нельзя создать более {MAX_ACCOUNTS_PER_USER} счетов.", show_alert=True)
        return

    await state.update_data(acc_user_id=user_id)
    await callback.message.edit_text(
        f"Введите название нового счёта (до 30 символов):"
    )
    await state.set_state(AccountStates.waiting_for_account_name)
    await callback.answer()


@router.message(AccountStates.waiting_for_account_name, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении названия счёта")
async def handle_new_account_name(message: Message, state: FSMContext, **kwargs) -> None:
    """Создаёт новый счёт с введённым названием."""
    name = message.text.strip()[:30]
    if not name:
        await message.answer("Название не может быть пустым. Введите снова:")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)

    async with async_session() as session:
        acc = await create_account(session, user_id, name)
        if acc is None:
            accounts = await get_accounts(session, user_id)
            if len(accounts) >= MAX_ACCOUNTS_PER_USER:
                await message.answer(
                    f"Нельзя создать более {MAX_ACCOUNTS_PER_USER} счетов.",
                    reply_markup=main_menu_keyboard(),
                )
            else:
                await message.answer(
                    f"Счёт с названием «{name}» уже существует. Введите другое название:"
                )
                return
        else:
            balances = await get_account_balances(session, user_id)
            await message.answer(
                f"✅ Счёт «{acc.name}» создан!\n\n" + _build_accounts_text(balances),
                reply_markup=accounts_menu_keyboard(),
                parse_mode="HTML",
            )

    await state.clear()


# --- Переименовать счёт ---

@router.callback_query(F.data == "acc_rename")
@log_exceptions("Ошибка при переименовании счёта")
async def handle_acc_rename(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список счетов для выбора переименования."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        await callback.answer("Нет счетов для переименования.", show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите счёт для переименования:",
        reply_markup=account_manage_keyboard(accounts, "rename_select"),
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_rename_select:"))
@log_exceptions("Ошибка при выборе счёта для переименования")
async def handle_acc_rename_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет выбранный счёт и запрашивает новое название."""
    try:
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    await state.update_data(rename_account_id=account_id, acc_user_id=user_id)
    await callback.message.edit_text("Введите новое название счёта (до 30 символов):")
    await state.set_state(AccountStates.waiting_for_rename_name)
    await callback.answer()


@router.message(AccountStates.waiting_for_rename_name, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при применении нового названия")
async def handle_rename_name(message: Message, state: FSMContext, **kwargs) -> None:
    """Переименовывает счёт."""
    new_name = message.text.strip()[:30]
    if not new_name:
        await message.answer("Название не может быть пустым. Введите снова:")
        return

    data = await state.get_data()
    account_id = data.get("rename_account_id")
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)

    async with async_session() as session:
        ok = await rename_account(session, account_id, user_id, new_name)
        if not ok:
            await message.answer(
                f"Счёт с названием «{new_name}» уже существует. Введите другое название:"
            )
            return

        balances = await get_account_balances(session, user_id)

    await message.answer(
        f"✅ Счёт переименован в «{new_name}»!\n\n" + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()


# --- Удалить счёт ---

@router.callback_query(F.data == "acc_delete")
@log_exceptions("Ошибка при удалении счёта")
async def handle_acc_delete(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список счетов для удаления."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        await callback.answer("Нет счетов для удаления.", show_alert=True)
        return

    if len(accounts) == 1:
        await callback.answer("Нельзя удалить последний счёт.", show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите счёт для удаления:",
        reply_markup=account_manage_keyboard(accounts, "delete_select"),
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete_select:"))
@log_exceptions("Ошибка при выборе счёта для удаления")
async def handle_acc_delete_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Если есть записи — предлагает выбрать счёт для переноса. Иначе — простое подтверждение."""
    try:
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        account = next((a for a in accounts if a.id == account_id), None)
        if not account:
            await callback.answer("Счёт не найден.")
            return
        record_count = await get_account_record_count(session, account_id)

    targets = [a for a in accounts if a.id != account_id]

    if record_count > 0:
        await callback.message.edit_text(
            f"⚠️ Счёт <b>«{account.name}»</b> содержит {record_count} записей.\n"
            f"Куда перенести записи?",
            reply_markup=account_delete_move_keyboard(account_id, targets),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            f"Удалить счёт <b>«{account.name}»</b>?",
            reply_markup=confirm_account_delete_keyboard(account_id),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete_move:"))
@log_exceptions("Ошибка при переносе записей и удалении счёта")
async def handle_acc_delete_move(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Переносит записи на выбранный счёт и удаляет исходный."""
    try:
        parts = callback.data.split(":")
        from_id = int(parts[1])
        to_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        ok = await move_and_delete_account(session, from_id, user_id, to_id)
        if not ok:
            await callback.answer("Не удалось удалить счёт.", show_alert=True)
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)

    await callback.message.edit_text(
        "✅ Записи перенесены, счёт удалён.\n\n" + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete_confirm:"))
@log_exceptions("Ошибка при подтверждении удаления счёта")
async def handle_acc_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет счёт и обновляет список."""
    try:
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        ok = await delete_account(session, account_id, user_id)
        if not ok:
            await callback.answer("Счёт не найден или уже удалён.", show_alert=True)
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)

    await callback.message.edit_text(
        "✅ Счёт удалён.\n\n" + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "acc_delete_cancel")
async def handle_acc_delete_cancel(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Отмена удаления счёта — возврат к списку балансов."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if user_id:
        async with async_session() as session:
            balances = await get_account_balances(session, user_id)
        await callback.message.edit_text(
            _build_accounts_text(balances),
            reply_markup=accounts_menu_keyboard(),
            parse_mode="HTML",
        )
    await state.clear()
    await callback.answer()


# --- Перевод между счетами ---

@router.callback_query(F.data == "acc_transfer")
@log_exceptions("Ошибка при переводе")
async def handle_acc_transfer(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список счетов для выбора источника перевода."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if len(accounts) < 2:
        await callback.answer("Нужно минимум 2 счёта для перевода.", show_alert=True)
        return

    await callback.message.edit_text(
        "↔️ <b>Перевод</b>\n\nВыберите счёт-источник:",
        reply_markup=account_manage_keyboard(accounts, "transfer_from"),
        parse_mode="HTML",
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_transfer_from:"))
@log_exceptions("Ошибка при выборе счёта-источника")
async def handle_acc_transfer_from(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список счетов-назначения (исключая источник)."""
    try:
        from_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    from_acc = next((a for a in accounts if a.id == from_id), None)
    if not from_acc:
        await callback.answer("Счёт не найден.")
        return

    destinations = [a for a in accounts if a.id != from_id]
    await state.update_data(transfer_from_id=from_id, acc_user_id=user_id)
    await callback.message.edit_text(
        f"↔️ <b>Перевод с «{from_acc.name}»</b>\n\nВыберите счёт-назначение:",
        reply_markup=account_manage_keyboard(destinations, f"transfer_to:{from_id}"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acc_transfer_to:"))
@log_exceptions("Ошибка при выборе счёта-назначения")
async def handle_acc_transfer_to(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет счёта перевода и запрашивает сумму."""
    try:
        parts = callback.data.split(":")
        from_id = int(parts[1])
        to_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    from_acc = next((a for a in accounts if a.id == from_id), None)
    to_acc = next((a for a in accounts if a.id == to_id), None)
    if not from_acc or not to_acc:
        await callback.answer("Счёт не найден.")
        return

    await state.update_data(transfer_from_id=from_id, transfer_to_id=to_id, acc_user_id=user_id)
    await callback.message.edit_text(
        f"↔️ <b>{from_acc.name} → {to_acc.name}</b>\n\nВведите сумму перевода:",
        parse_mode="HTML",
    )
    await state.set_state(AccountStates.waiting_for_transfer_amount)
    await callback.answer()


@router.message(AccountStates.waiting_for_transfer_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при выполнении перевода")
async def handle_transfer_amount(message: Message, state: FSMContext, **kwargs) -> None:
    """Выполняет перевод между счетами."""
    try:
        amount = Decimal(message.text.strip().replace(",", "."))
        if amount <= 0 or amount > Decimal(str(MAX_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введите число от 0.01 до {MAX_AMOUNT:,}:".replace(",", " ")
        )
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)
    from_id = data.get("transfer_from_id")
    to_id = data.get("transfer_to_id")

    async with async_session() as session:
        balance = await get_account_balance(session, from_id)
        if amount > balance:
            await message.answer(
                f"Недостаточно средств. Баланс счёта: {balance:,.0f} ₽".replace(",", " ")
            )
            return

        ok = await create_transfer(session, user_id, from_id, to_id, amount)
        if not ok:
            await message.answer("Не удалось выполнить перевод.", reply_markup=main_menu_keyboard())
            await state.clear()
            return

        accounts = await get_accounts(session, user_id)
        from_name = next((a.name for a in accounts if a.id == from_id), "—")
        to_name = next((a.name for a in accounts if a.id == to_id), "—")
        balances = await get_account_balances(session, user_id)

    amount_str = f"{amount:,.0f}₽".replace(",", " ")
    await message.answer(
        f"✅ Перевод выполнен!\n{from_name} → {to_name}: <b>{amount_str}</b>\n\n"
        + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()


# ==================== История счёта ====================

@router.callback_query(F.data == "acc_history")
@log_exceptions("Ошибка при открытии истории счёта")
async def handle_acc_history(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список счетов для выбора истории."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        return
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
    await callback.message.edit_text(
        "📋 <b>История по счёту</b>\n\nВыберите счёт:",
        reply_markup=account_manage_keyboard(accounts, "history_select"),
        parse_mode="HTML",
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_history_select:"))
@log_exceptions("Ошибка при выборе счёта для истории")
async def handle_acc_history_select(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Сохраняет выбранный счёт и показывает выбор периода."""
    account_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        acc = next((a for a in accounts if a.id == account_id), None)
        if not acc:
            await callback.answer("Счёт не найден.")
            return
        balance = await get_account_balance(session, account_id)

    balance_str = f"{balance:,.0f} ₽".replace(",", " ")
    await state.update_data(
        acc_hist_account_id=account_id,
        acc_hist_account_name=acc.name,
        acc_hist_balance=balance_str,
        acc_user_id=user_id,
    )
    await callback.message.edit_text(
        f"📋 <b>{acc.name}</b> — {balance_str}\n\nЗа какой период показать историю?",
        reply_markup=history_period_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(AccountStates.waiting_for_acc_hist_period)
    await callback.answer()


@router.callback_query(AccountStates.waiting_for_acc_hist_period)
@log_exceptions("Ошибка при получении истории счёта")
async def handle_acc_hist_period(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Загружает первую страницу истории выбранного счёта."""
    try:
        period = callback.data.split(":")[1]
    except (IndexError, AttributeError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    account_id = data.get("acc_hist_account_id")
    acc_name = data.get("acc_hist_account_name", "Счёт")
    acc_balance = data.get("acc_hist_balance", "")
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            return
        total_count, income_sum, expense_sum, records = await get_history_data(
            session, user.id, period,
            limit=RECORDS_PER_PAGE, offset=0,
            account_id=account_id, include_transfers=True,
        )

    if total_count == 0:
        await callback.message.edit_text(
            f"📋 <b>{acc_name}</b>\nЗаписей за указанный период нет.",
            parse_mode="HTML",
        )
        await state.clear()
        await callback.answer()
        return

    total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
    await state.update_data(
        acc_hist_period=period,
        acc_hist_page=0,
        acc_hist_total_pages=total_pages,
        acc_hist_total_count=total_count,
        acc_hist_income=str(income_sum),
        acc_hist_expense=str(expense_sum),
    )

    header = f"📋 <b>{acc_name}</b> — {acc_balance}"
    text, kb = build_history_page(
        records, 0, total_pages, income_sum, expense_sum,
        period=period, total_count=total_count, header=header,
    )
    if total_pages > 1:
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await state.set_state(AccountStates.waiting_for_acc_hist_page)
    else:
        await callback.message.edit_text(text, parse_mode="HTML")
        await state.clear()
    await callback.answer()


@router.callback_query(AccountStates.waiting_for_acc_hist_page, F.data.startswith("hist_page:"))
@log_exceptions("Ошибка при навигации по истории счёта")
async def handle_acc_hist_page(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Навигация по страницам истории счёта."""
    try:
        page_str = callback.data.split(":")[1]
        if page_str == "noop":
            await callback.answer()
            return
        new_page = int(page_str)
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    account_id = data.get("acc_hist_account_id")
    acc_name = data.get("acc_hist_account_name", "Счёт")
    acc_balance = data.get("acc_hist_balance", "")
    period = data.get("acc_hist_period")
    total_pages = data.get("acc_hist_total_pages", 1)
    total_count = data.get("acc_hist_total_count", 0)
    income_sum = Decimal(data.get("acc_hist_income", "0"))
    expense_sum = Decimal(data.get("acc_hist_expense", "0"))
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    if new_page < 0 or new_page >= total_pages:
        await callback.answer("Страница не существует.")
        return

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            return
        records = await get_records(
            session, user.id, period,
            limit=RECORDS_PER_PAGE, offset=new_page * RECORDS_PER_PAGE,
            account_id=account_id, include_transfers=True,
        )

    await state.update_data(acc_hist_page=new_page)
    header = f"📋 <b>{acc_name}</b> — {acc_balance}"
    text, kb = build_history_page(
        records, new_page, total_pages, income_sum, expense_sum,
        period=period, total_count=total_count, header=header,
    )
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()


# ==================== Установка баланса счёта ====================

@router.callback_query(F.data == "acc_set_balance")
@log_exceptions("Ошибка при установке баланса")
async def handle_acc_set_balance(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список счетов для выбора."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        return
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
    await callback.message.edit_text(
        "💰 <b>Установить баланс</b>\n\nВыберите счёт:",
        reply_markup=account_manage_keyboard(accounts, "set_balance_select"),
        parse_mode="HTML",
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_set_balance_select:"))
@log_exceptions("Ошибка при выборе счёта для установки баланса")
async def handle_acc_set_balance_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Запрашивает желаемый баланс для выбранного счёта."""
    account_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        acc = next((a for a in accounts if a.id == account_id), None)
        if not acc:
            await callback.answer("Счёт не найден.")
            return
        current = await get_account_balance(session, account_id)

    await state.update_data(set_balance_account_id=account_id, acc_user_id=user_id)
    await callback.message.edit_text(
        f"💰 <b>{acc.name}</b>\n"
        f"Текущий баланс: <b>{current:,.0f} ₽</b>\n\n"
        "Введите новый баланс:".replace(",", " "),
        parse_mode="HTML",
    )
    await state.set_state(AccountStates.waiting_for_set_balance)
    await callback.answer()


@router.message(AccountStates.waiting_for_set_balance, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении баланса")
async def handle_set_balance_amount(message: Message, state: FSMContext, **kwargs) -> None:
    """Сохраняет желаемый баланс через balance_offset."""
    try:
        desired = Decimal(message.text.strip().replace(",", "."))
        if desired < 0 or desired > Decimal(str(MAX_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введите число от 0 до {MAX_AMOUNT:,}:".replace(",", " ")
        )
        return

    data = await state.get_data()
    account_id = data.get("set_balance_account_id")
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)

    async with async_session() as session:
        ok = await set_account_balance(session, account_id, desired, user_id)
        if not ok:
            await message.answer("Не удалось установить баланс.", reply_markup=main_menu_keyboard())
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)
        accounts = await get_accounts(session, user_id)
        acc_name = next((a.name for a in accounts if a.id == account_id), "—")

    await state.clear()
    desired_str = f"{desired:,.0f} ₽".replace(",", " ")
    await message.answer(
        f"✅ Баланс <b>{acc_name}</b> установлен: <b>{desired_str}</b>\n\n"
        + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )


# ==================== Fallback для неизвестных сообщений ====================

@router.message(StateFilter(None), F.text)
async def handle_unknown_message(message: Message, **kwargs) -> None:
    """Обработка текстовых сообщений, не подходящих под другие хендлеры."""
    await message.answer(
        "🤔 Не понял команду.\n\n"
        "<b>Используйте кнопки меню</b> или быстрый ввод:\n"
        "<code>+1000 зарплата</code> — доход\n"
        "<code>-500 еда</code> — расход",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
