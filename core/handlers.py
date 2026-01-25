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
    delete_record,
    get_user_by_tg_id,
    get_income_report,
    get_expense_report,
)
from core.database.models import async_session


router = Router()

# Количество записей на одной странице (для пагинации)
RECORDS_PER_PAGE = 10


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
        success = await set_user(session, message.from_user.id, name=message.from_user.full_name)
        if not success:
            await message.answer("Ошибка при регистрации. Попробуйте позже.")
            return

    await message.answer(
        "Добро пожаловать!\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
    )


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

    # Получаем тип операции из state и сохраняем в БД
    data = await state.get_data()
    operation = data.get("operation")

    async with async_session() as session:
        if not await set_user(session, message.from_user.id, name=message.from_user.full_name):
            await message.answer("Ошибка при сохранении пользователя.")
            return
        ok = await add_record(
            session, message.from_user.id, operation, amount, category
        )
        if not ok:
            await message.answer("Ошибка при добавлении записи.")
            return

    await message.answer("✅ Запись добавлена!", reply_markup=main_menu_keyboard())
    await state.clear()


# ==================== История операций ====================

def build_history_page(
    records: list[dict], page: int = 0
) -> tuple[str, InlineKeyboardBuilder, int]:
    """Формирует текст истории и кнопки навигации для указанной страницы.

    Args:
        records: Список записей (dict с ключами operation, amount, category, date)
        page: Номер страницы (с 0)

    Returns:
        (текст, клавиатура, всего_страниц)
    """
    total = len(records)
    total_pages = (total + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    # Защита от выхода за границы
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    # Записи для текущей страницы
    start = page * RECORDS_PER_PAGE
    end = start + RECORDS_PER_PAGE
    page_records = records[start:end]

    # Итоговые суммы (по всем записям, не только на странице)
    sumadd = sum(float(r["amount"]) for r in records if r["operation"] == "+")
    sumspent = sum(float(r["amount"]) for r in records if r["operation"] == "-")
    remaining = sumadd - sumspent

    # Формируем текст
    text = f"🕘 История операций (стр. {page + 1}/{total_pages}):\n\n"
    for r in page_records:
        symbol = "➖" if r["operation"] == "-" else "➕"
        category = f" - {r['category']}" if r.get("category") else ""
        text += f"{symbol} {r['amount']:,.0f}₽{category} ({r['date']})\n"

    text += f"\nСумма доходов: {sumadd:,.0f}₽".replace(",", ".")
    text += f"\nСумма расходов: {sumspent:,.0f}₽".replace(",", ".")
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

    return text, kb, total_pages


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
    """Выбран период — загружаем записи и показываем первую страницу."""
    # Парсим период из callback_data (формат: "del_period:day")
    try:
        period = callback.data.split(":")[1]
    except (IndexError, AttributeError):
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    # Загружаем записи из БД
    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            return
        records = await get_records(session, user.id, period)

    if not records:
        await callback.message.edit_text("Записей не найдено за указанный период.")
        await state.clear()
        await callback.answer()
        return

    # Конвертируем ORM-объекты в dict (чтобы хранить в state без сессии)
    records_data = [
        {
            "operation": r.operation,
            "amount": float(r.amount),
            "category": r.category,
            "date": r.created_at.strftime("%d.%m.%Y"),
        }
        for r in records
    ]
    await state.update_data(history_records=records_data, history_page=0)

    # Показываем первую страницу
    text, kb, total_pages = build_history_page(records_data, page=0)

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

    # Получаем записи из state
    data = await state.get_data()
    records_data = data.get("history_records", [])

    if not records_data:
        await callback.message.edit_text("Данные истории устарели. Попробуйте снова.")
        await state.clear()
        await callback.answer()
        return

    # Обновляем страницу
    await state.update_data(history_page=new_page)
    text, kb, _ = build_history_page(records_data, page=new_page)
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
    records: list[dict], page: int = 0
) -> tuple[InlineKeyboardBuilder, int, int]:
    """Формирует клавиатуру со списком записей для удаления.

    Args:
        records: Список записей (dict с id, operation, amount, category, date)
        page: Номер страницы (с 0)

    Returns:
        (клавиатура, всего_страниц, текущая_страница)
    """
    total = len(records)
    total_pages = (total + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    # Защита от выхода за границы
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    # Записи для текущей страницы
    start = page * RECORDS_PER_PAGE
    end = start + RECORDS_PER_PAGE
    page_records = records[start:end]

    # Кнопки с записями
    kb = InlineKeyboardBuilder()
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

    return kb, total_pages, page


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
    """Выбран период — загружаем записи и показываем список."""
    # Парсим период
    try:
        period = callback.data.split(":")[1]
    except (IndexError, AttributeError):
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    # Загружаем записи
    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            return
        records = await get_records(session, user.id, period)

    if not records:
        await callback.message.edit_text("Записей за выбранный период нет.")
        await state.clear()
        return

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
    await state.update_data(delete_records=records_data, delete_page=0)

    # Показываем первую страницу
    kb, total_pages, _ = build_delete_keyboard(records_data, page=0)
    total = len(records_data)
    await callback.message.edit_text(
        f"Выберите запись для удаления (всего: {total}):",
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
    records_data = data.get("delete_records", [])
    current_page = data.get("delete_page", 0)

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

        # Обновляем страницу
        await state.update_data(delete_page=new_page)
        kb, _, _ = build_delete_keyboard(records_data, page=new_page)
        total = len(records_data)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total}):",
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

            # Убираем из списка в state
            records_data = [r for r in records_data if r["id"] != record_id]
            await state.update_data(delete_records=records_data)

            # Если всё удалено — выходим
            if not records_data:
                await callback.message.edit_text("Все записи удалены.")
                await state.clear()
                return

            # Корректируем страницу (если последняя запись на странице удалена)
            total_pages = (len(records_data) + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
            if current_page >= total_pages:
                current_page = total_pages - 1
            await state.update_data(delete_page=current_page)

            # Обновляем клавиатуру
            kb, _, _ = build_delete_keyboard(records_data, page=current_page)
            total = len(records_data)
            await callback.message.edit_text(
                f"Выберите запись для удаления (всего: {total}):",
                reply_markup=kb.as_markup(),
            )
        else:
            await callback.answer("⚠️ Запись не найдена или уже удалена.")
        return

    # --- Неизвестный callback ---
    await callback.answer("Некорректные данные.")
    await state.clear()
