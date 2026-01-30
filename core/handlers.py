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
)
from core.database.models import async_session, Record
from config import (
    RECORDS_PER_PAGE,
    MAX_SHOW_ALL_RECORDS,
    MAX_CATEGORY_LENGTH,
    MAX_AMOUNT,
    MAX_MESSAGE_LENGTH,
)


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


# ==================== FSM States ====================
# Состояния для многошаговых операций

class AddRecord(StatesGroup):
    """Состояния для добавления записи дохода/расхода."""
    waiting_for_amount = State()  # Ожидание ввода суммы и категории


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

@router.message(StateFilter(None), F.func(lambda m: is_income(m) or is_expense(m)))
@log_exceptions("Ошибка при обработке операции")
async def handle_income_expense(message: Message, state: FSMContext, **kwargs) -> None:
    """Начало добавления записи: сохраняем тип операции, просим ввести сумму."""
    is_income_op = is_income(message)
    operation = "+" if is_income_op else "-"
    await state.update_data(operation=operation)

    if is_income_op:
        prompt_text = "💵 Введите сумму и категорию:\n<code>5000 зарплата</code>"
    else:
        prompt_text = "🛒 Введите сумму и категорию:\n<code>500 продукты</code>"

    await message.answer(prompt_text, parse_mode="HTML")
    await state.set_state(AddRecord.waiting_for_amount)


def format_added_records_response(
    added_records: list[tuple[str, Decimal, str, datetime | None]],
    errors: list[str] | None = None,
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

    today = datetime.now(ZoneInfo("Europe/Moscow")).date()

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
            year = datetime.now(ZoneInfo("Europe/Moscow")).year

        try:
            record_date = datetime(year, month, day, 12, 0, 0, tzinfo=ZoneInfo("Europe/Moscow"))
            line = line[date_match.end():].strip()
        except ValueError:
            # Невалидная дата (например 31.02) — пропускаем её часть и продолжаем парсинг
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
        category = "не указано"

    if len(category) > MAX_CATEGORY_LENGTH:
        category = category[:MAX_CATEGORY_LENGTH]

    return operation, amount, category, record_date


@router.message(AddRecord.waiting_for_amount)
@log_exceptions("Ошибка при добавлении записи")
async def handle_amount_and_category(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Парсинг суммы и категории, сохранение в БД. Поддерживает несколько записей."""
    # Получаем тип операции из state (по умолчанию)
    data = await state.get_data()
    default_operation = data.get("operation")

    # Разбиваем на строки
    lines = message.text.strip().split("\n")

    # Парсим все записи
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

    # Сохраняем в БД
    async with async_session() as session:
        # Используем get вместо set — пользователь уже создан при /start
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            # Редкий случай: пользователь не зарегистрирован
            user = await set_user(session, message.from_user.id, name=message.from_user.full_name)
            if not user:
                await message.answer("Ошибка. Отправьте /start для регистрации.")
                return

        user_id = user.id

        added_records = []
        for operation, amount, category, record_date in records_to_add:
            ok = await add_record(session, user_id, operation, amount, category, record_date)
            if ok:
                added_records.append((operation, amount, category, record_date))

    # Формируем красивый ответ
    if not added_records:
        await message.answer("Не удалось сохранить записи.", reply_markup=main_menu_keyboard())
        await state.clear()
        return

    response = format_added_records_response(added_records, errors)
    await message.answer(response, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    await state.clear()


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

    # Сохраняем в БД
    async with async_session() as session:
        # Используем get вместо set — пользователь уже создан при /start
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            # Редкий случай: пользователь не зарегистрирован
            user = await set_user(session, message.from_user.id, name=message.from_user.full_name)
            if not user:
                await message.answer("Ошибка. Отправьте /start для регистрации.")
                return

        user_id = user.id

        added_records = []
        for operation, amount, category, record_date in records_to_add:
            ok = await add_record(session, user_id, operation, amount, category, record_date)
            if ok:
                added_records.append((operation, amount, category, record_date))

    # Формируем красивый ответ
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


@router.message(MenuStates.waiting_for_custom_period)
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
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    if date_from.replace(tzinfo=ZoneInfo("Europe/Moscow")) > now:
        await message.answer("Начальная дата не может быть в будущем.")
        return

    # Устанавливаем время для конечной даты (конец дня)
    date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo("Europe/Moscow"))
    date_to = date_to.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=ZoneInfo("Europe/Moscow"))

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

@router.message(F.func(is_report))
@log_exceptions("Произошла ошибка при формировании отчёта")
async def menu_report(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка Отчёт — проверяем наличие записей, просим выбрать тип."""
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

    now = datetime.now(ZoneInfo("Europe/Moscow"))
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
    now = datetime.now(ZoneInfo("Europe/Moscow"))
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
    cur_date_from = datetime(year, month, 1, tzinfo=ZoneInfo("Europe/Moscow"))
    if month == 12:
        cur_date_to = datetime(year + 1, 1, 1, tzinfo=ZoneInfo("Europe/Moscow")) - timedelta(seconds=1)
    else:
        cur_date_to = datetime(year, month + 1, 1, tzinfo=ZoneInfo("Europe/Moscow")) - timedelta(seconds=1)

    prev_date_from = datetime(prev_year, prev_month, 1, tzinfo=ZoneInfo("Europe/Moscow"))
    if prev_month == 12:
        prev_date_to = datetime(prev_year + 1, 1, 1, tzinfo=ZoneInfo("Europe/Moscow")) - timedelta(seconds=1)
    else:
        prev_date_to = datetime(prev_year, prev_month + 1, 1, tzinfo=ZoneInfo("Europe/Moscow")) - timedelta(seconds=1)

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


@router.message(F.func(is_delete))
@log_exceptions("Ошибка при показе меню удаления")
async def menu_delete(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка Удалить — показываем выбор периода."""
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
        start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        last_day = monthrange(year, month)[1]
        end_date = datetime(year, month, last_day, 23, 59, 59, tzinfo=ZoneInfo("Europe/Moscow"))

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
