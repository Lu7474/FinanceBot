"""Handlers for operation history (with filter and search support)."""

import html
import re
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import MAX_MESSAGE_LENGTH, MAX_SHOW_ALL_RECORDS, RECORDS_PER_PAGE
from core.database.models import Record, async_session, moscow_now
from core.database.requests import (
    get_history_data,
    get_records,
    get_top_categories_for_period,
    search_records,
)
from core.keyboards import (
    history_category_filter_keyboard,
    history_period_keyboard,
    main_menu_keyboard,
    search_result_keyboard,
)
from core.utils import RU_WEEKDAYS, format_money, log_exceptions

from .common import MenuStates, get_message, is_history, is_main_menu_button

router = Router()

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
    operation_filter: str | None = None,
    category_filter: str | None = None,
) -> tuple[str, InlineKeyboardBuilder]:
    """Формирует текст истории и кнопки навигации + фильтров для указанной страницы."""
    remaining = income_sum - expense_sum

    grouped: dict[str, list] = {}
    for r in page_records:
        date_key = r.created_at.strftime("%d.%m.%y")
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(r)

    period_name = period_label if period_label else PERIOD_NAMES.get(period, "")

    filter_parts = []
    if operation_filter == "+":
        filter_parts.append("Доходы")
    elif operation_filter == "-":
        filter_parts.append("Расходы")
    if category_filter:
        filter_parts.append(html.escape(category_filter))
    active_filter = ("  │  " + "  │  ".join(filter_parts)) if filter_parts else ""

    base_header = header if header else "📊 <b>История</b>"
    if period_name:
        text = f"{base_header} • {period_name}{active_filter}\n\n"
    else:
        text = f"{base_header}{active_filter}\n\n"

    for date_str, day_records in grouped.items():
        day_income = sum(float(r.amount) for r in day_records if r.operation == "+")
        day_expense = sum(float(r.amount) for r in day_records if r.operation == "-")
        day_total = day_income - day_expense

        weekday = RU_WEEKDAYS[day_records[0].created_at.weekday()]
        short_date = ".".join(date_str.split(".")[:2])

        total_sign = "+" if day_total >= 0 else ""
        text += f"▸ <b>{weekday}, {short_date}</b> │ {total_sign}{day_total:,.0f}₽\n".replace(
            ",", " "
        )

        for r in day_records:
            sign = "+" if r.operation == "+" else "-"
            category = html.escape(r.category or "")
            text += f"   {sign}{float(r.amount):,.0f}₽ {category}\n".replace(",", " ")

        text += "\n"

    text += "─────────────────\n"
    text += f"📈 Доход: {format_money(income_sum)}\n"
    text += f"📉 Расход: {format_money(expense_sum)}\n"
    balance_sign = "+" if remaining >= 0 else ""
    text += f"💰 Баланс: {balance_sign}{format_money(remaining)}"

    kb = InlineKeyboardBuilder()
    row_sizes: list[int] = []

    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(("◀ Назад", f"hist_page:{page - 1}"))
        nav_buttons.append((f"{page + 1}/{total_pages}", "hist_page:noop"))
        if page < total_pages - 1:
            nav_buttons.append(("Вперёд ▶", f"hist_page:{page + 1}"))
        for btn_text, data in nav_buttons:
            kb.button(text=btn_text, callback_data=data)
        row_sizes.append(len(nav_buttons))

        if total_count > 0 and total_count <= MAX_SHOW_ALL_RECORDS:
            kb.button(
                text=f"Показать все ({total_count})", callback_data="hist_show_all"
            )
            row_sizes.append(1)

    kb.button(text="📋 Открыть запись", callback_data="hist_open_record")
    row_sizes.append(1)

    # Filter rows
    all_text = "✓ Все" if operation_filter is None else "Все"
    expense_text = "✓ Расходы" if operation_filter == "-" else "Только расходы"
    income_text = "✓ Доходы" if operation_filter == "+" else "Только доходы"
    kb.button(text=all_text, callback_data="hist_filter:all")
    kb.button(text=expense_text, callback_data="hist_filter:expense")
    kb.button(text=income_text, callback_data="hist_filter:income")
    row_sizes.append(3)

    cat_text = f"● {category_filter} ▾" if category_filter else "По категории ▾"
    kb.button(text=cat_text, callback_data="hist_filter:category")
    kb.button(text="Сбросить", callback_data="hist_filter:reset")
    kb.button(text="🔍 Поиск", callback_data="hist_search:start")
    row_sizes.append(3)

    kb.adjust(*row_sizes)
    return text, kb


def _build_search_page_text(
    records: list[Record],
    page: int,
    total_pages: int,
    total: int,
    query_str: str,
    income_sum: Decimal = Decimal("0"),
    expense_sum: Decimal = Decimal("0"),
) -> str:
    """Builds search results text."""
    text = f"🔍 <b>Поиск:</b> <i>{html.escape(query_str)}</i>\n\n"

    grouped: dict[str, list] = {}
    for r in records:
        date_key = r.created_at.strftime("%d.%m.%y")
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(r)

    for date_str, day_records in grouped.items():
        weekday = RU_WEEKDAYS[day_records[0].created_at.weekday()]
        short_date = ".".join(date_str.split(".")[:2])
        text += f"▸ <b>{weekday}, {short_date}</b>\n"
        for r in day_records:
            sign = "+" if r.operation == "+" else "-"
            category = html.escape(r.category or "")
            text += f"   {sign}{float(r.amount):,.0f}₽ {category}\n".replace(",", " ")
        text += "\n"

    text = text.rstrip()
    totals = []
    if income_sum > 0:
        totals.append(f"+{format_money(income_sum)}")
    if expense_sum > 0:
        totals.append(f"−{format_money(expense_sum)}")
    total_display = " ".join(totals) if totals else format_money(0)
    text += f"\n\nНайдено: {total} зап.  │  {total_display}"
    return text


def _extract_date_range(data: dict) -> tuple[datetime | None, datetime | None]:
    """Extracts date_from/date_to from state data if period == 'range'."""
    if data.get("history_period") != "range":
        return None, None
    date_from_str = data.get("history_date_from")
    date_to_str = data.get("history_date_to")
    if date_from_str and date_to_str:
        return datetime.fromisoformat(date_from_str), datetime.fromisoformat(
            date_to_str
        )
    return None, None


async def _apply_filter_and_reload(
    callback: CallbackQuery,
    state: FSMContext,
    new_filter: dict,
    user_id: int,
) -> None:
    """Apply new history_filter, refetch data, update state and message."""
    data = await state.get_data()
    if data.get("history_filter", {}) == new_filter:
        await callback.answer()
        return
    period = data.get("history_period") or "all"
    period_label = data.get("history_period_label", "")
    date_from, date_to = _extract_date_range(data)

    operation_filter = new_filter.get("operation")
    category_filter = new_filter.get("category")

    async with async_session() as session:
        total_count, income_sum, expense_sum, records = await get_history_data(
            session,
            user_id,
            period,
            date_from,
            date_to,
            limit=RECORDS_PER_PAGE,
            offset=0,
            operation_filter=operation_filter,
            category_filter=category_filter,
        )

    if total_count == 0:
        await callback.answer("Нет записей по данному фильтру.", show_alert=True)
        return

    total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    await state.update_data(
        history_filter=new_filter,
        history_page=0,
        history_total_pages=total_pages,
        history_total_count=total_count,
        history_income=str(income_sum),
        history_expense=str(expense_sum),
    )
    await state.set_state(MenuStates.waiting_for_history_page)

    text, kb = build_history_page(
        records,
        0,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        period_label=period_label,
        total_count=total_count,
        operation_filter=operation_filter,
        category_filter=category_filter,
    )
    await get_message(callback).edit_text(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


# ==================== Основные хендлеры ====================


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
    try:
        period = (callback.data or "").split(":")[1]
    except IndexError, AttributeError:
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    if period == "custom":
        await get_message(callback).edit_text(
            "Введите период в формате:\n"
            "<code>01.01.25 - 31.01.25</code>\n\n"
            "Или отправьте /cancel для отмены.",
            parse_mode="HTML",
        )
        await state.set_state(MenuStates.waiting_for_custom_period)
        await callback.answer()
        return

    user_id = kwargs["user_id"]
    async with async_session() as session:
        total_count, income_sum, expense_sum, records = await get_history_data(
            session, user_id, period, limit=RECORDS_PER_PAGE, offset=0
        )

        if total_count == 0:
            await get_message(callback).edit_text(
                "Записей не найдено за указанный период."
            )
            await state.clear()
            await callback.answer()
            return

        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    await state.update_data(
        history_period=period,
        history_page=0,
        history_total_pages=total_pages,
        history_total_count=total_count,
        history_income=str(income_sum),
        history_expense=str(expense_sum),
        history_filter={},
    )

    text, kb = build_history_page(
        records,
        0,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        total_count=total_count,
    )

    await get_message(callback).edit_text(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
    )
    await state.set_state(MenuStates.waiting_for_history_page)
    await callback.answer()


@router.callback_query(
    MenuStates.waiting_for_history_page, F.data.startswith("hist_page:")
)
@log_exceptions("Ошибка при навигации по истории")
async def menu_history_page(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Навигация по страницам истории (кнопки Назад/Вперёд)."""
    try:
        page_str = (callback.data or "").split(":")[1]
        if page_str == "noop":
            await callback.answer()
            return
        new_page = int(page_str)
    except IndexError, ValueError, AttributeError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    period = data.get("history_period")
    total_pages = data.get("history_total_pages", 1)
    income_sum = Decimal(data.get("history_income", "0"))
    expense_sum = Decimal(data.get("history_expense", "0"))
    history_filter = data.get("history_filter", {})
    operation_filter = history_filter.get("operation")
    category_filter = history_filter.get("category")

    if new_page < 0 or new_page >= total_pages:
        await callback.answer("Страница не существует.")
        return

    date_from, date_to = _extract_date_range(data)

    if not period:
        await get_message(callback).edit_text(
            "Данные истории устарели. Попробуйте снова."
        )
        await state.clear()
        await callback.answer()
        return

    user_id = kwargs["user_id"]
    async with async_session() as session:
        offset = new_page * RECORDS_PER_PAGE
        records = await get_records(
            session,
            user_id,
            period,
            date_from,
            date_to,
            limit=RECORDS_PER_PAGE,
            offset=offset,
            operation_filter=operation_filter,
            category_filter=category_filter,
        )

    period_label = data.get("history_period_label", "")
    total_count = data.get("history_total_count", 0)

    await state.update_data(history_page=new_page)
    text, kb = build_history_page(
        records,
        new_page,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        period_label=period_label,
        total_count=total_count,
        operation_filter=operation_filter,
        category_filter=category_filter,
    )
    if len(text) > MAX_MESSAGE_LENGTH - 100:
        text = text[: MAX_MESSAGE_LENGTH - 150] + "\n\n... (сообщение обрезано)"

    await get_message(callback).edit_text(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
    )
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
    history_filter = data.get("history_filter", {})
    operation_filter = history_filter.get("operation")
    category_filter = history_filter.get("category")

    date_from, date_to = _extract_date_range(data)

    if not period:
        await get_message(callback).edit_text(
            "Данные истории устарели. Попробуйте снова."
        )
        await state.clear()
        await callback.answer()
        return

    user_id = kwargs["user_id"]
    async with async_session() as session:
        records = await get_records(
            session,
            user_id,
            period,
            date_from,
            date_to,
            limit=MAX_SHOW_ALL_RECORDS,
            offset=0,
            operation_filter=operation_filter,
            category_filter=category_filter,
        )

    text, _ = build_history_page(
        records,
        0,
        1,
        income_sum,
        expense_sum,
        period=period,
        period_label=period_label,
        total_count=total_count,
        operation_filter=operation_filter,
        category_filter=category_filter,
    )

    if len(text) > MAX_MESSAGE_LENGTH - 100:
        text = text[: MAX_MESSAGE_LENGTH - 150] + "\n\n... (сообщение обрезано)"

    await get_message(callback).edit_text(text, parse_mode="HTML")
    await state.clear()
    await callback.answer()


@router.message(MenuStates.waiting_for_custom_period, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при обработке своего периода")
async def menu_history_custom_period(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Обработка текстового ввода дат для своего периода."""
    text = (message.text or "").strip()

    match = re.match(
        r"(\d{1,2}\.\d{1,2}\.\d{2,4})\s*[-–—]\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", text
    )
    if not match:
        await message.answer(
            "Неверный формат. Введите период в формате:\n"
            "<code>01.01.25 - 31.01.25</code>\n\n"
            "Или отправьте /cancel для отмены.",
            parse_mode="HTML",
        )
        return

    date_from_str, date_to_str = match.groups()

    try:
        date_from = datetime.strptime(
            date_from_str,
            "%d.%m.%y" if len(date_from_str.split(".")[-1]) == 2 else "%d.%m.%Y",
        )
        date_to = datetime.strptime(
            date_to_str,
            "%d.%m.%y" if len(date_to_str.split(".")[-1]) == 2 else "%d.%m.%Y",
        )
    except ValueError:
        await message.answer(
            "Неверный формат даты. Используйте формат ДД.ММ.ГГ или ДД.ММ.ГГГГ\n"
            "Например: <code>01.01.25 - 31.01.25</code>",
            parse_mode="HTML",
        )
        return

    if date_from > date_to:
        await message.answer("Начальная дата не может быть позже конечной.")
        return

    now = moscow_now()
    if date_from > now:
        await message.answer("Начальная дата не может быть в будущем.")
        return

    date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = date_to.replace(hour=23, minute=59, second=59, microsecond=999999)

    user_id = kwargs["user_id"]
    async with async_session() as session:
        total_count, income_sum, expense_sum, records = await get_history_data(
            session,
            user_id,
            "range",
            date_from,
            date_to,
            limit=RECORDS_PER_PAGE,
            offset=0,
        )

        if total_count == 0:
            await message.answer(
                "Записей не найдено за указанный период.",
                reply_markup=main_menu_keyboard(),
            )
            await state.clear()
            return

        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    period_label = f"{date_from.strftime('%d.%m.%y')} - {date_to.strftime('%d.%m.%y')}"

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
        history_filter={},
    )

    text, kb = build_history_page(
        records,
        0,
        total_pages,
        income_sum,
        expense_sum,
        period="range",
        period_label=period_label,
        total_count=total_count,
    )

    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await state.set_state(MenuStates.waiting_for_history_page)


# ==================== Фильтры ====================


@router.callback_query(
    MenuStates.waiting_for_history_page, F.data == "hist_filter:category"
)
@log_exceptions("Ошибка при показе фильтра по категории")
async def show_category_filter(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Открывает выбор категории для фильтра."""
    data = await state.get_data()
    period = data.get("history_period") or "all"
    date_from, date_to = _extract_date_range(data)
    history_filter = data.get("history_filter", {})
    operation_filter = history_filter.get("operation")

    user_id = kwargs["user_id"]
    async with async_session() as session:
        categories = await get_top_categories_for_period(
            session,
            user_id,
            period,
            date_from,
            date_to,
            operation_filter=operation_filter,
        )

    if not categories:
        await callback.answer("Нет категорий для выбора.", show_alert=True)
        return

    await get_message(callback).edit_reply_markup(
        reply_markup=history_category_filter_keyboard(categories)
    )
    await state.set_state(MenuStates.waiting_for_history_category_filter)
    await callback.answer()


async def _restore_history_from_category_picker(
    callback: CallbackQuery, state: FSMContext, data: dict, user_id: int
) -> None:
    """Restore history page from category picker (shared by back and no-op)."""
    period = data.get("history_period")
    period_label = data.get("history_period_label", "")
    page = data.get("history_page", 0)
    total_pages = data.get("history_total_pages", 1)
    total_count = data.get("history_total_count", 0)
    income_sum = Decimal(data.get("history_income", "0"))
    expense_sum = Decimal(data.get("history_expense", "0"))
    history_filter = data.get("history_filter", {})
    operation_filter = history_filter.get("operation")
    category_filter = history_filter.get("category")
    date_from, date_to = _extract_date_range(data)

    if not period:
        await get_message(callback).edit_text("Данные истории устарели.")
        await state.clear()
        await callback.answer()
        return

    async with async_session() as session:
        records = await get_records(
            session,
            user_id,
            period,
            date_from,
            date_to,
            limit=RECORDS_PER_PAGE,
            offset=page * RECORDS_PER_PAGE,
            operation_filter=operation_filter,
            category_filter=category_filter,
        )

    await state.set_state(MenuStates.waiting_for_history_page)
    text, kb = build_history_page(
        records,
        page,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        period_label=period_label,
        total_count=total_count,
        operation_filter=operation_filter,
        category_filter=category_filter,
    )
    await get_message(callback).edit_text(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(
    MenuStates.waiting_for_history_category_filter,
    F.data == "hist_cat_filter_back",
)
@log_exceptions("Ошибка при возврате из выбора категории")
async def category_filter_back(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Возврат из выбора категории обратно в историю."""
    data = await state.get_data()
    await _restore_history_from_category_picker(
        callback, state, data, kwargs["user_id"]
    )


@router.callback_query(
    MenuStates.waiting_for_history_category_filter,
    F.data.startswith("hist_cat_filter:"),
)
@log_exceptions("Ошибка при применении фильтра по категории")
async def apply_category_filter(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Применяет выбранную категорию как фильтр."""
    category = (callback.data or "").split(":", 1)[1]
    if not category:
        await callback.answer()
        return
    data = await state.get_data()
    current_filter = data.get("history_filter", {})
    new_filter = {**current_filter, "category": category}
    if new_filter == current_filter:
        await _restore_history_from_category_picker(
            callback, state, data, kwargs["user_id"]
        )
        return
    await _apply_filter_and_reload(callback, state, new_filter, kwargs["user_id"])


@router.callback_query(
    MenuStates.waiting_for_history_page, F.data == "hist_filter:reset"
)
@log_exceptions("Ошибка при сбросе фильтров")
async def reset_filter(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Сбрасывает все фильтры."""
    await _apply_filter_and_reload(callback, state, {}, kwargs["user_id"])


@router.callback_query(
    MenuStates.waiting_for_history_page, F.data.startswith("hist_filter:")
)
@log_exceptions("Ошибка при применении фильтра")
async def apply_operation_filter(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Применяет фильтр по типу операции (all/income/expense)."""
    filter_type = (callback.data or "").split(":")[1]
    if filter_type in ("category", "reset"):
        await callback.answer()
        return
    data = await state.get_data()
    current_filter = data.get("history_filter", {})

    if filter_type == "all":
        new_filter = {k: v for k, v in current_filter.items() if k != "operation"}
    elif filter_type == "expense":
        new_filter = {**current_filter, "operation": "-"}
    elif filter_type == "income":
        new_filter = {**current_filter, "operation": "+"}
    else:
        await callback.answer()
        return

    await _apply_filter_and_reload(callback, state, new_filter, kwargs["user_id"])


# ==================== Поиск ====================


@router.callback_query(
    MenuStates.waiting_for_history_page, F.data == "hist_search:start"
)
@log_exceptions("Ошибка при запуске поиска")
async def start_search(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Начало поиска — просим ввести запрос."""
    await get_message(callback).edit_text(
        "Введите запрос.\nПримеры: <code>Такси</code> | <code>Еда</code> | <code>&gt;1000</code> | <code>+&gt;1000</code> | <code>-&gt;1000</code>",
        parse_mode="HTML",
    )
    await state.set_state(MenuStates.waiting_for_search_query)
    await callback.answer()


@router.message(MenuStates.waiting_for_search_query, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при обработке поискового запроса")
async def handle_search_input(message: Message, state: FSMContext, **kwargs) -> None:
    """Обрабатывает введённый поисковый запрос."""
    query_str = (message.text or "").strip()
    if not query_str:
        await message.answer("Введите запрос для поиска.")
        return
    user_id = kwargs["user_id"]

    async with async_session() as session:
        total, income_sum, expense_sum, records = await search_records(
            session, user_id, query_str, limit=RECORDS_PER_PAGE, offset=0
        )

    if total == 0:
        await message.answer(
            "По вашему запросу ничего не найдено.\n\nПопробуйте другой запрос."
        )
        return

    total_pages = (total + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE

    await state.update_data(
        search_query=query_str,
        search_page=0,
        search_total=total,
        search_total_pages=total_pages,
        search_income_sum=str(income_sum),
        search_expense_sum=str(expense_sum),
    )
    await state.set_state(MenuStates.waiting_for_search_page)

    text = _build_search_page_text(
        records, 0, total_pages, total, query_str, income_sum, expense_sum
    )
    kb = search_result_keyboard(0, total_pages)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(
    MenuStates.waiting_for_search_page, F.data.startswith("search_page:")
)
@log_exceptions("Ошибка при навигации по результатам поиска")
async def search_page_nav(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Навигация по страницам результатов поиска."""
    try:
        page_str = (callback.data or "").split(":")[1]
        if page_str == "noop":
            await callback.answer()
            return
        new_page = int(page_str)
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    query_str = data.get("search_query", "")
    total_pages = data.get("search_total_pages", 1)
    total = data.get("search_total", 0)
    income_sum = Decimal(data.get("search_income_sum", "0"))
    expense_sum = Decimal(data.get("search_expense_sum", "0"))
    user_id = kwargs["user_id"]

    if new_page < 0 or new_page >= total_pages:
        await callback.answer("Страница не существует.")
        return

    async with async_session() as session:
        _, _, _, records = await search_records(
            session,
            user_id,
            query_str,
            limit=RECORDS_PER_PAGE,
            offset=new_page * RECORDS_PER_PAGE,
        )

    await state.update_data(search_page=new_page)
    text = _build_search_page_text(
        records, new_page, total_pages, total, query_str, income_sum, expense_sum
    )
    kb = search_result_keyboard(new_page, total_pages)
    await get_message(callback).edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_search_page, F.data == "search_new")
@log_exceptions("Ошибка при запуске нового поиска")
async def new_search(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Начать новый поиск."""
    await state.update_data(search_query=None, search_page=0)
    await get_message(callback).edit_text(
        "Введите запрос.\nПримеры: <code>Такси</code> | <code>Еда</code> | <code>&gt;1000</code> | <code>+&gt;1000</code> | <code>-&gt;1000</code>",
        parse_mode="HTML",
    )
    await state.set_state(MenuStates.waiting_for_search_query)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_search_page, F.data == "search_back")
@log_exceptions("Ошибка при возврате из поиска")
async def search_back_to_history(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Возврат из поиска к истории с фильтрами."""
    data = await state.get_data()
    period = data.get("history_period")
    period_label = data.get("history_period_label", "")
    page = data.get("history_page", 0)
    total_pages = data.get("history_total_pages", 1)
    total_count = data.get("history_total_count", 0)
    income_sum = Decimal(data.get("history_income", "0"))
    expense_sum = Decimal(data.get("history_expense", "0"))
    history_filter = data.get("history_filter", {})
    operation_filter = history_filter.get("operation")
    category_filter = history_filter.get("category")
    date_from, date_to = _extract_date_range(data)

    if not period:
        await get_message(callback).edit_text(
            "Данные истории устарели. Откройте историю заново."
        )
        await state.clear()
        await callback.answer()
        return

    user_id = kwargs["user_id"]
    async with async_session() as session:
        records = await get_records(
            session,
            user_id,
            period,
            date_from,
            date_to,
            limit=RECORDS_PER_PAGE,
            offset=page * RECORDS_PER_PAGE,
            operation_filter=operation_filter,
            category_filter=category_filter,
        )

    await state.set_state(MenuStates.waiting_for_history_page)
    text, kb = build_history_page(
        records,
        page,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        period_label=period_label,
        total_count=total_count,
        operation_filter=operation_filter,
        category_filter=category_filter,
    )
    await get_message(callback).edit_text(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
    )
    await callback.answer()
