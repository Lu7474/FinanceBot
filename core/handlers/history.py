"""Handlers for operation history."""

import html
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.database.models import async_session, Record
from core.database.requests import get_history_data, get_records, get_user_by_tg_id
from core.keyboards import history_period_keyboard, main_menu_keyboard
from core.utils import RU_WEEKDAYS, format_money, log_exceptions
from config import MAX_MESSAGE_LENGTH, MAX_SHOW_ALL_RECORDS, RECORDS_PER_PAGE, TIMEZONE

from .common import MenuStates, get_user_id_from_event, is_history, is_main_menu_button

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
) -> tuple[str, InlineKeyboardBuilder]:
    """Формирует текст истории и кнопки навигации для указанной страницы."""
    remaining = income_sum - expense_sum

    grouped: dict[str, list] = {}
    for r in page_records:
        date_key = r.created_at.strftime("%d.%m.%y")
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append(r)

    if period_label:
        period_name = period_label
    else:
        period_name = PERIOD_NAMES.get(period, "")

    if header:
        text = f"{header} • {period_name}\n\n" if period_name else f"{header}\n\n"
    else:
        text = f"📊 <b>История</b> • {period_name}\n\n" if period_name else "📊 <b>История</b>\n\n"

    for date_str, day_records in grouped.items():
        day_income = sum(float(r.amount) for r in day_records if r.operation == "+")
        day_expense = sum(float(r.amount) for r in day_records if r.operation == "-")
        day_total = day_income - day_expense

        weekday = RU_WEEKDAYS[day_records[0].created_at.weekday()]
        short_date = ".".join(date_str.split(".")[:2])

        total_sign = "+" if day_total >= 0 else ""
        text += f"▸ <b>{weekday}, {short_date}</b> │ {total_sign}{day_total:,.0f}₽\n".replace(",", " ")

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
    try:
        period = callback.data.split(":")[1]
    except (IndexError, AttributeError):
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

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

        total_count, income_sum, expense_sum, records = await get_history_data(
            session, user.id, period, limit=RECORDS_PER_PAGE, offset=0
        )

        if total_count == 0:
            await callback.message.edit_text("Записей не найдено за указанный период.")
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
    )

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
    try:
        page_str = callback.data.split(":")[1]
        if page_str == "noop":
            await callback.answer()
            return
        new_page = int(page_str)
    except (IndexError, ValueError, AttributeError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    period = data.get("history_period")
    total_pages = data.get("history_total_pages", 1)
    income_sum = Decimal(data.get("history_income", "0"))
    expense_sum = Decimal(data.get("history_expense", "0"))

    if new_page < 0 or new_page >= total_pages:
        await callback.answer("Страница не существует.")
        return

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

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            await callback.answer()
            return

        offset = new_page * RECORDS_PER_PAGE
        records = await get_records(session, user.id, period, date_from, date_to, limit=RECORDS_PER_PAGE, offset=offset)

    period_label = data.get("history_period_label", "")
    total_count = data.get("history_total_count", 0)

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

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            await callback.answer()
            return

        records = await get_records(session, user.id, period, date_from, date_to, limit=MAX_SHOW_ALL_RECORDS, offset=0)

    text, _ = build_history_page(
        records, 0, 1, income_sum, expense_sum,
        period=period, period_label=period_label, total_count=total_count
    )

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

    match = re.match(r"(\d{1,2}\.\d{1,2}\.\d{2,4})\s*[-–—]\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", text)
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
        date_from = datetime.strptime(date_from_str, "%d.%m.%y" if len(date_from_str.split(".")[-1]) == 2 else "%d.%m.%Y")
        date_to = datetime.strptime(date_to_str, "%d.%m.%y" if len(date_to_str.split(".")[-1]) == 2 else "%d.%m.%Y")
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

    now = datetime.now(ZoneInfo(TIMEZONE))
    if date_from.replace(tzinfo=ZoneInfo(TIMEZONE)) > now:
        await message.answer("Начальная дата не может быть в будущем.")
        return

    date_from = date_from.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo(TIMEZONE))
    date_to = date_to.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=ZoneInfo(TIMEZONE))

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
    )

    text, kb = build_history_page(records, 0, total_pages, income_sum, expense_sum, period="range", period_label=period_label, total_count=total_count)

    if total_pages > 1:
        await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await state.set_state(MenuStates.waiting_for_history_page)
    else:
        await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        await state.clear()
