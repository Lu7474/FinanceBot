"""
Обработчики команд и callback-запросов. Основная логика бота.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.keyboards import (
    delete_period_keyboard,
    get_years_keyboard,
    get_months_keyboard,
    main_menu_keyboard,
    report_type_keyboard,
)
from core.utils import (
    get_available_years_and_months,
    build_report_pie,
    make_history_text,
    RU_MONTHS,
    log_exceptions,
)

from core.database.requests import (
    set_user,
    add_record,
    get_records,
    count_records,
    get_totals,
    delete_record,
    get_user_by_tg_id,
    get_income_report,
    get_expense_report,
)
from core.database.models import async_session


router = Router()

# Количество записей на одной странице (для пагинации)
RECORDS_PER_PAGE = 10
# Максимальная длина категории (соответствует String(50) в модели)
MAX_CATEGORY_LENGTH = 50


# ==================== FSM States ====================
# Состояния для многошаговых операций

class AddRecord(StatesGroup):
    """Состояния для добавления записи дохода/расхода."""
    waiting_for_amount = State()  # Ожидание ввода суммы и категории


class MenuStates(StatesGroup):
    """Состояния для навигации по меню."""
    waiting_for_history_period = State()   # Выбор периода истории
    waiting_for_history_page = State()     # Навигация по страницам истории
    waiting_for_report_type = State()      # Выбор типа отчёта (доход/расход)
    waiting_for_report_year = State()      # Выбор года для отчёта
    waiting_for_report_month = State()     # Выбор месяца для отчёта
    waiting_for_delete_period = State()    # Выбор периода для удаления
    waiting_for_delete_record = State()    # Выбор записи для удаления


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

    await message.answer(
        "Добро пожаловать!\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
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


# ==================== Добавление записи ====================

@router.message(F.text.in_(["➕ Доход", "➖ Расход"]))
@log_exceptions("Ошибка при обработке операции")
async def handle_income_expense(message: Message, state: FSMContext, **kwargs) -> None:
    """Начало добавления записи: сохраняем тип операции, просим ввести сумму."""
    operation = "+" if message.text == "➕ Доход" else "-"
    await state.update_data(operation=operation)
    await message.answer("Введите сумму и категорию, например: 1000 еда")
    await state.set_state(AddRecord.waiting_for_amount)


@router.message(AddRecord.waiting_for_amount)
@log_exceptions("Ошибка при добавлении записи")
async def handle_amount_and_category(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Парсинг суммы и категории, сохранение в БД."""
    # Ищем число в тексте (поддержка запятой как разделителя)
    match = re.search(r"([-+]?\d+(?:[.,]\d+)?)", message.text)
    if not match:
        await message.answer("Введите сумму и категорию, например: 1000 еда")
        return

    # Валидация суммы
    try:
        amount = Decimal(match.group(1).replace(",", "."))
        if amount <= 0:
            await message.answer("Сумма должна быть положительной.")
            return
        if amount > Decimal("1000000"):
            await message.answer("Слишком большая сумма. Максимум — 1 000 000.")
            return
    except (InvalidOperation, ValueError):
        await message.answer("Введите корректную сумму.")
        return

    # Категория — всё, что осталось после удаления суммы
    category = message.text.replace(match.group(0), "").strip()
    if not category:
        category = "не указано"

    # Валидация длины категории
    if len(category) > MAX_CATEGORY_LENGTH:
        await message.answer(f"Категория слишком длинная. Максимум {MAX_CATEGORY_LENGTH} символов.")
        return

    # Получаем тип операции из state и сохраняем в БД
    data = await state.get_data()
    operation = data.get("operation")

    async with async_session() as session:
        # set_user возвращает User с id — используем его напрямую
        user = await set_user(session, message.from_user.id, name=message.from_user.full_name)
        if not user:
            await message.answer("Ошибка при сохранении пользователя.")
            return

        ok = await add_record(session, user.id, operation, amount, category)
        if not ok:
            await message.answer("Ошибка при добавлении записи.")
            return

    await message.answer("✅ Запись добавлена!", reply_markup=main_menu_keyboard())
    await state.clear()


# ==================== История операций ====================

def build_history_page(
    page_records: list[dict],
    page: int,
    total_pages: int,
    income_sum: Decimal,
    expense_sum: Decimal,
) -> tuple[str, InlineKeyboardBuilder]:
    """Формирует текст истории и кнопки навигации для указанной страницы.

    Args:
        page_records: Записи текущей страницы (уже загружены с LIMIT/OFFSET)
        page: Номер страницы (с 0)
        total_pages: Общее количество страниц
        income_sum: Сумма доходов (посчитана в БД)
        expense_sum: Сумма расходов (посчитана в БД)

    Returns:
        (текст, клавиатура)
    """
    remaining = income_sum - expense_sum

    # Формируем текст
    text = f"🕘 История операций (стр. {page + 1}/{total_pages}):\n\n"
    for r in page_records:
        symbol = "➖" if r["operation"] == "-" else "➕"
        category = f" - {r['category']}" if r.get("category") else ""
        text += f"{symbol} {r['amount']:,.0f}₽{category} ({r['date']})\n"

    text += f"\nСумма доходов: {income_sum:,.0f}₽".replace(",", ".")
    text += f"\nСумма расходов: {expense_sum:,.0f}₽".replace(",", ".")
    text += f"\nОстаток: {remaining:,.0f}₽".replace(",", ".")

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

    return text, kb


@router.message(F.text == "🕘 История")
@log_exceptions("Ошибка при показе истории")
async def menu_history(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка История — показываем выбор периода."""
    await message.answer(
        "За какой период показать историю?",
        reply_markup=delete_period_keyboard(),
    )
    await state.set_state(MenuStates.waiting_for_history_period)


@router.callback_query(MenuStates.waiting_for_history_period)
@log_exceptions("Ошибка при получении истории")
async def menu_history_period(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран период — загружаем первую страницу записей."""
    # Парсим период из callback_data (формат: "del_period:day")
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

        # Подсчёт общего количества и сумм (один раз)
        total_count = await count_records(session, user.id, period)
        if total_count == 0:
            await callback.message.edit_text("Записей не найдено за указанный период.")
            await state.clear()
            await callback.answer()
            return

        income_sum, expense_sum = await get_totals(session, user.id, period)
        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

        # Загружаем только первую страницу
        records = await get_records(session, user.id, period, limit=RECORDS_PER_PAGE, offset=0)

    # Конвертируем ORM-объекты в dict
    records_data = [
        {
            "operation": r.operation,
            "amount": float(r.amount),
            "category": r.category,
            "date": r.created_at.strftime("%d.%m.%Y"),
        }
        for r in records
    ]

    # Сохраняем в state только параметры, не все записи
    await state.update_data(
        history_period=period,
        history_page=0,
        history_total_pages=total_pages,
        history_income=float(income_sum),
        history_expense=float(expense_sum),
    )

    # Показываем первую страницу
    text, kb = build_history_page(records_data, 0, total_pages, income_sum, expense_sum)

    if total_pages > 1:
        await callback.message.edit_text(text, reply_markup=kb.as_markup())
        await state.set_state(MenuStates.waiting_for_history_page)
    else:
        await callback.message.edit_text(text)
        await state.clear()
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_history_page)
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
    income_sum = Decimal(str(data.get("history_income", 0)))
    expense_sum = Decimal(str(data.get("history_expense", 0)))

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
        records = await get_records(session, user.id, period, limit=RECORDS_PER_PAGE, offset=offset)

    # Конвертируем в dict
    records_data = [
        {
            "operation": r.operation,
            "amount": float(r.amount),
            "category": r.category,
            "date": r.created_at.strftime("%d.%m.%Y"),
        }
        for r in records
    ]

    # Обновляем страницу в state
    await state.update_data(history_page=new_page)
    text, kb = build_history_page(records_data, new_page, total_pages, income_sum, expense_sum)
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()


# ==================== Отчёт (график) ====================

@router.message(F.text == "📊 Отчёт")
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

    await message.answer("Выберите тип отчёта:", reply_markup=report_type_keyboard())
    await state.set_state(MenuStates.waiting_for_report_type)


@router.message(MenuStates.waiting_for_report_type)
@log_exceptions("Ошибка при выборе типа отчёта")
async def report_type_handler(message: Message, state: FSMContext, **kwargs) -> None:
    """Выбор типа отчёта (Доход/Расход) — показываем выбор года."""
    if message.text not in ("Доход", "Расход"):
        await message.answer(
            "Пожалуйста, выберите тип отчёта:",
            reply_markup=report_type_keyboard(),
        )
        return

    await state.update_data(report_type=message.text)

    # Получаем доступные годы
    async with async_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Пользователь не найден.")
            await state.clear()
            return
        years_months = await get_available_years_and_months(session, user.id)

    if not years_months:
        await message.answer("Нет записей для отображения отчёта.")
        await state.clear()
        return

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

    # Получаем месяцы с записями
    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await callback.answer()
            await state.clear()
            return
        years_months = await get_available_years_and_months(session, user.id)

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

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            await callback.answer()
            return

        # Загружаем записи за месяц
        records = await get_records(session, user.id, "range", date_from, date_to)

        # Группируем по категориям
        categories = defaultdict(Decimal)
        total = Decimal("0.0")
        for r in records:
            if r.operation == operation_sign:
                cat = r.category or "Без категории"
                categories[cat] += r.amount
                total += r.amount

        # Генерируем график (если есть данные)
        if categories:
            buf, caption = await build_report_pie(categories, total, date_from, report_type)
            if buf:
                await callback.message.answer_photo(
                    photo=BufferedInputFile(buf.read(), filename="report.png"),
                    caption=caption,
                )

        # Текстовый отчёт
        if report_type == "income":
            text = await get_income_report(session, user.id, date_from, date_to)
        else:
            text = await get_expense_report(session, user.id, date_from, date_to)

        await callback.message.answer(text)

    await state.clear()
    await callback.answer()


# ==================== Удаление записи ====================

def build_delete_keyboard(
    page_records: list[dict],
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

    # Кнопки с записями
    for r in page_records:
        symbol = "➖" if r["operation"] == "-" else "➕"
        text = f"{symbol} {r['amount']:.0f}₽ - {r['category']} ({r['date']})"
        kb.button(text=text, callback_data=f"del_record:{r['id']}")

    kb.adjust(1)  # По одной кнопке в ряд

    # Кнопки навигации (только если страниц > 1)
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(("◀ Назад", f"del_page:{page - 1}"))
        nav_buttons.append((f"{page + 1}/{total_pages}", "del_page:noop"))
        if page < total_pages - 1:
            nav_buttons.append(("Вперёд ▶", f"del_page:{page + 1}"))

        for text, data in nav_buttons:
            kb.button(text=text, callback_data=data)
        kb.adjust(1, len(nav_buttons))  # Записи по 1, навигация в одном ряду

    return kb


@router.message(F.text == "🗑️ Удалить запись")
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
    """Выбран период — загружаем первую страницу записей для удаления."""
    # Парсим период
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

        # Подсчёт общего количества
        total_count = await count_records(session, user.id, period)
        if total_count == 0:
            await callback.message.edit_text("Записей за выбранный период нет.")
            await state.clear()
            await callback.answer()
            return

        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

        # Загружаем только первую страницу
        records = await get_records(session, user.id, period, limit=RECORDS_PER_PAGE, offset=0)

    # Конвертируем в dict (включая id для удаления)
    records_data = [
        {
            "id": r.id,
            "operation": r.operation,
            "amount": float(r.amount),
            "category": r.category,
            "date": r.created_at.strftime("%d.%m.%Y"),
        }
        for r in records
    ]

    # Сохраняем в state только параметры
    await state.update_data(
        delete_period=period,
        delete_page=0,
        delete_total_count=total_count,
        delete_total_pages=total_pages,
    )

    # Показываем первую страницу
    kb = build_delete_keyboard(records_data, 0, total_pages)
    await callback.message.edit_text(
        f"Выберите запись для удаления (всего: {total_count}):",
        reply_markup=kb.as_markup(),
    )
    await state.set_state(MenuStates.waiting_for_delete_record)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_delete_record)
@log_exceptions("Ошибка при удалении записи")
async def menu_delete_record(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка: навигация по страницам или удаление выбранной записи."""
    data = await state.get_data()
    period = data.get("delete_period")
    current_page = data.get("delete_page", 0)
    total_count = data.get("delete_total_count", 0)

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

        # Загружаем записи нужной страницы из БД
        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                await callback.answer()
                return

            offset = new_page * RECORDS_PER_PAGE
            records = await get_records(session, user.id, period, limit=RECORDS_PER_PAGE, offset=offset)

        records_data = [
            {
                "id": r.id,
                "operation": r.operation,
                "amount": float(r.amount),
                "category": r.category,
                "date": r.created_at.strftime("%d.%m.%Y"),
            }
            for r in records
        ]

        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
        await state.update_data(delete_page=new_page)
        kb = build_delete_keyboard(records_data, new_page, total_pages)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await callback.answer()
        return

    # --- Удаление записи ---
    if callback.data.startswith("del_record:"):
        try:
            record_id = int(callback.data.split(":")[1])
        except (IndexError, ValueError, AttributeError):
            await callback.answer("Некорректные данные.")
            await state.clear()
            return

        # Удаляем из БД
        async with async_session() as session:
            deleted = await delete_record(session, callback.from_user.id, record_id)

            if deleted:
                await callback.answer("✅ Запись удалена!")

                # Пересчитываем общее количество
                new_total = await count_records(session, callback.from_user.id, period)
                user = await get_user_by_tg_id(session, callback.from_user.id)

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
                records = await get_records(session, user.id, period, limit=RECORDS_PER_PAGE, offset=offset)

                records_data = [
                    {
                        "id": r.id,
                        "operation": r.operation,
                        "amount": float(r.amount),
                        "category": r.category,
                        "date": r.created_at.strftime("%d.%m.%Y"),
                    }
                    for r in records
                ]

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
            else:
                await callback.answer("⚠️ Запись не найдена или уже удалена.")
        return

    # --- Неизвестный callback ---
    await callback.answer("Некорректные данные.")
    await state.clear()
