"""Handlers for reports (charts) and period comparison."""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InputMediaPhoto,
    Message,
)
from dateutil.relativedelta import relativedelta

from config import MAX_CAPTION_LENGTH, TIMEZONE
from core.charts import (
    build_balance_line_chart,
    build_category_chart,
    build_report_pie,
    build_stacked_bar_chart,
    build_trend_chart,
    build_yearly_chart,
)
from core.database.models import async_session
from core.database.requests import (
    get_categories_for_year,
    get_categories_summary,
    get_daily_balance_for_month,
    get_monthly_totals,
    get_records,
    get_stacked_data,
    get_yearly_report,
)
from core.keyboards import (
    chart_period_keyboard,
    get_months_keyboard,
    get_years_keyboard,
    report_section_keyboard,
    report_type_keyboard,
    stacked_period_keyboard,
    stacked_type_keyboard,
    yearly_report_cats_keyboard,
    yearly_report_type_keyboard,
    yearly_report_year_keyboard,
)
from core.reports import (
    format_balance_caption,
    format_period_caption,
    format_stacked_caption,
    format_yearly_report,
    get_available_years_and_months,
    make_comparison_text,
)
from core.utils import RU_MONTHS, log_exceptions

from .common import MenuStates, get_message, is_report

router = Router()


@router.message(StateFilter("*"), F.func(is_report))
@log_exceptions("Произошла ошибка при формировании отчёта")
async def menu_report(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка Отчёт — проверяем наличие записей, просим выбрать тип."""
    await state.clear()
    user_id = kwargs.get("user_id")
    if not user_id:
        await message.answer("Пользователь не найден.")
        return
    async with async_session() as session:
        years_months = await get_available_years_and_months(session, user_id)

    if not years_months:
        await message.answer("Нет записей для отображения отчёта.")
        return

    await state.update_data(report_years_months=years_months)
    await message.answer("Выберите тип отчёта:", reply_markup=report_section_keyboard())


@router.callback_query(F.data == "report_section:categories")
@log_exceptions("Ошибка при выборе раздела отчёта")
async def report_section_categories(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Раздел «По категориям» — вход в существующий pie-флоу."""
    # Clear state so report_type_handler (StateFilter None) fires next.
    await state.clear()
    await get_message(callback).edit_text(
        "Выберите тип отчёта:", reply_markup=report_type_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "report_section:structure")
@log_exceptions("Ошибка при выборе раздела отчёта")
async def report_section_structure(callback: CallbackQuery, **kwargs) -> None:
    """Раздел «Структура по месяцам» — выбор типа (Доход/Расход)."""
    await get_message(callback).edit_text(
        "Структура по месяцам — выберите тип:",
        reply_markup=stacked_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "report_section_back")
@log_exceptions("Ошибка при возврате в меню отчётов")
async def report_section_back(callback: CallbackQuery, **kwargs) -> None:
    """Назад к подменю типов отчёта."""
    await get_message(callback).edit_text(
        "Выберите тип отчёта:", reply_markup=report_section_keyboard()
    )
    await callback.answer()


# ==================== Динамика баланса по дням ====================


@router.callback_query(F.data == "report_section:balance")
@log_exceptions("Ошибка при выборе раздела отчёта")
async def report_section_balance(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Раздел «Динамика баланса» — выбор года (без выбора типа: баланс = net)."""
    data = await state.get_data()
    years_months = data.get("report_years_months", {})
    if not years_months:
        await callback.answer("Сессия истекла, откройте отчёт заново.", show_alert=True)
        return
    keyboard = get_years_keyboard(list(years_months.keys()), prefix="bal")
    await get_message(callback).edit_text(
        "Динамика баланса — выберите год:", reply_markup=keyboard
    )
    await state.set_state(MenuStates.waiting_for_balance_year)
    await callback.answer()


@router.callback_query(
    MenuStates.waiting_for_balance_year, F.data.startswith("bal_year:")
)
@log_exceptions("Ошибка при выборе года для баланса")
async def balance_year(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Выбран год — показываем доступные месяцы."""
    try:
        year = int((callback.data or "").split(":")[1])
    except IndexError, ValueError, AttributeError:
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    now = datetime.now(ZoneInfo(TIMEZONE))
    data = await state.get_data()
    years_months = data.get("report_years_months", {})

    if year not in years_months:
        await callback.answer("Нет записей за этот год.")
        await state.clear()
        return

    available_months = [
        month
        for month in years_months[year]
        if year < now.year or (year == now.year and month <= now.month)
    ]
    if not available_months:
        await callback.answer("Нет доступных месяцев.")
        await state.clear()
        return

    keyboard = get_months_keyboard(year, available_months, prefix="bal")
    await get_message(callback).edit_text(
        f"Баланс за {year} — выберите месяц:", reply_markup=keyboard
    )
    await state.update_data(report_year=year)
    await state.set_state(MenuStates.waiting_for_balance_month)
    await callback.answer()


@router.callback_query(F.data == "bal_back_years")
@log_exceptions("Ошибка при возврате к выбору года")
async def balance_back_to_years(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Шаг назад: с выбора месяца обратно к выбору года."""
    data = await state.get_data()
    years_months = data.get("report_years_months", {})
    if not years_months:
        await callback.answer("Сессия истекла.", show_alert=True)
        await state.clear()
        return
    keyboard = get_years_keyboard(list(years_months.keys()), prefix="bal")
    await get_message(callback).edit_text("Выберите год:", reply_markup=keyboard)
    await state.set_state(MenuStates.waiting_for_balance_year)
    await callback.answer()


@router.callback_query(
    MenuStates.waiting_for_balance_month, F.data.startswith("bal_month:")
)
@log_exceptions("Ошибка при построении графика баланса")
async def balance_month(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Выбран месяц — строим линейный график динамики баланса по дням."""
    try:
        parts = (callback.data or "").split(":")
        year = int(parts[1])
        month = int(parts[2])
    except IndexError, ValueError, AttributeError:
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    now = datetime.now(ZoneInfo(TIMEZONE))
    if year > now.year or (year == now.year and month > now.month):
        await get_message(callback).edit_text("Нельзя получить отчёт за будущий месяц.")
        await state.clear()
        await callback.answer()
        return

    await get_message(callback).edit_text("⏳ Генерация графика...")
    await callback.answer()

    user_id = kwargs.get("user_id")
    if not user_id:
        await get_message(callback).edit_text("Пользователь не найден.")
        await state.clear()
        return

    async with async_session() as session:
        daily_data = await get_daily_balance_for_month(session, user_id, year, month)

    if not daily_data:
        await get_message(callback).edit_text(
            f"Нет данных за {RU_MONTHS[month]} {year}."
        )
        await state.clear()
        return

    buf = await build_balance_line_chart(daily_data, year, month)
    caption = format_balance_caption(daily_data, year, month)

    if buf:
        await get_message(callback).answer_photo(
            photo=BufferedInputFile(buf.read(), filename="balance.png"),
            caption=caption,
            parse_mode="HTML",
        )
        try:
            await get_message(callback).delete()
        except Exception:
            pass
    else:
        await get_message(callback).edit_text(caption, parse_mode="HTML")

    await state.clear()


@router.callback_query(F.data.startswith("stacked_type:"))
@log_exceptions("Ошибка при выборе типа структуры")
async def stacked_type_handler(callback: CallbackQuery, **kwargs) -> None:
    """Выбран тип для структуры — показываем выбор периода."""
    raw = (callback.data or "").split(":", 1)[1]
    op = "inc" if raw == "income" else "exp"
    await get_message(callback).edit_text(
        "Выберите период:", reply_markup=stacked_period_keyboard(op)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("stacked_build:"))
@log_exceptions("Ошибка при построении структуры")
async def stacked_build_handler(callback: CallbackQuery, **kwargs) -> None:
    """Строит stacked bar chart за выбранный период."""
    try:
        parts = (callback.data or "").split(":")
        op = parts[1]
        months_count = int(parts[2])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    operation_sign = "+" if op == "inc" else "-"
    user_id = kwargs.get("user_id")
    if not user_id:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    await callback.answer("⏳ Генерация...")

    async with async_session() as session:
        data = await get_stacked_data(session, user_id, operation_sign, months_count)

    if not data:
        await get_message(callback).edit_text("Нет данных за выбранный период.")
        return

    buf = await build_stacked_bar_chart(data, operation_sign)
    caption = format_stacked_caption(data, operation_sign)

    if buf:
        await get_message(callback).answer_photo(
            photo=BufferedInputFile(buf.read(), filename="structure.png"),
            caption=caption,
            parse_mode="HTML",
        )
    else:
        await get_message(callback).answer(caption, parse_mode="HTML")

    try:
        await get_message(callback).delete()
    except Exception:
        pass


@router.callback_query(StateFilter(None), F.data.startswith("report_type:"))
@log_exceptions("Ошибка при выборе типа отчёта")
async def report_type_handler(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбор типа отчёта (Доход/Расход) — показываем выбор года."""
    try:
        raw = (callback.data or "").split(":", 1)[1]
    except IndexError:
        await callback.answer("Некорректные данные.")
        return
    if raw == "income":
        report_type = "Доход"
        operation = "+"
    elif raw == "expense":
        report_type = "Расход"
        operation = "-"
    else:
        await callback.answer("Некорректный тип отчёта.")
        return

    await state.update_data(report_type=report_type)

    user_id = kwargs.get("user_id")
    if not user_id:
        await get_message(callback).edit_text("Пользователь не найден.")
        await state.clear()
        await callback.answer()
        return

    async with async_session() as session:
        years_months = await get_available_years_and_months(session, user_id, operation)

    if not years_months:
        await get_message(callback).edit_text(
            f"Нет записей по категории «{report_type}» для отображения отчёта."
        )
        await state.clear()
        await callback.answer()
        return

    await state.update_data(report_years_months=years_months)
    keyboard = get_years_keyboard(list(years_months.keys()))
    await get_message(callback).edit_text(
        f"Тип отчёта: {report_type}\n\nВыберите год:", reply_markup=keyboard
    )
    await state.set_state(MenuStates.waiting_for_report_year)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_report_year)
@log_exceptions("Ошибка при получении месяцев для отчёта")
async def menu_report_year(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран год — показываем доступные месяцы."""
    try:
        year = int((callback.data or "").split(":")[1])
    except IndexError, ValueError, AttributeError:
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    now = datetime.now(ZoneInfo(TIMEZONE))
    current_year = now.year
    current_month = now.month

    data = await state.get_data()
    years_months = data.get("report_years_months", {})

    if year not in years_months:
        await callback.answer("Нет записей за этот год.")
        await state.clear()
        return

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
    await get_message(callback).edit_text(
        f"Выберите месяц {year} года:", reply_markup=keyboard
    )
    await state.update_data(report_year=year)
    await state.set_state(MenuStates.waiting_for_report_month)
    await callback.answer()


@router.callback_query(F.data == "report_back_years")
@log_exceptions("Ошибка при возврате к выбору года")
async def report_back_to_years(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Шаг назад: с выбора месяца обратно к выбору года."""
    data = await state.get_data()
    years_months = data.get("report_years_months", {})
    if not years_months:
        await callback.answer("Сессия истекла.", show_alert=True)
        await state.clear()
        return
    keyboard = get_years_keyboard(list(years_months.keys()))
    await get_message(callback).edit_text("Выберите год:", reply_markup=keyboard)
    await state.set_state(MenuStates.waiting_for_report_year)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_report_month)
@log_exceptions("Ошибка при формировании отчёта")
async def menu_report_month(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран месяц — генерируем график и текстовый отчёт."""
    try:
        parts = (callback.data or "").split(":")
        year = int(parts[1])
        month = int(parts[2])
    except IndexError, ValueError, AttributeError:
        await callback.answer("Некорректные данные.")
        await state.clear()
        return

    await state.update_data(report_year=year, report_month=month)
    data = await state.get_data()
    raw_type = data.get("report_type")

    if raw_type == "Доход":
        report_type = "income"
        operation_sign = "+"
    elif raw_type == "Расход":
        report_type = "expense"
        operation_sign = "-"
    else:
        await get_message(callback).edit_text("Ошибка: не выбран тип отчёта.")
        await state.clear()
        await callback.answer()
        return

    now = datetime.now(ZoneInfo(TIMEZONE))
    if year > now.year or (year == now.year and month > now.month):
        await get_message(callback).edit_text("Нельзя получить отчет за будущий месяц.")
        await state.clear()
        await callback.answer()
        return

    date_from = datetime(year, month, 1, tzinfo=ZoneInfo(TIMEZONE))
    if month == 12:
        date_to = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(TIMEZONE)) - timedelta(
            seconds=1
        )
    else:
        date_to = datetime(year, month + 1, 1, tzinfo=ZoneInfo(TIMEZONE)) - timedelta(
            seconds=1
        )

    await get_message(callback).edit_text("⏳ Генерация отчёта...")
    await callback.answer()

    user_id = kwargs.get("user_id")
    if not user_id:
        await get_message(callback).edit_text("Пользователь не найден.")
        await state.clear()
        return

    async with async_session() as session:
        categories = await get_categories_summary(
            session, user_id, operation_sign, date_from, date_to
        )
        total = sum(categories.values()) if categories else Decimal("0.0")

        records = await get_records(
            session, user_id, "range", date_from, date_to, limit=30
        )

        if categories:
            buf, caption = await build_report_pie(
                categories, total, date_from, report_type, records
            )

            period_kb = chart_period_keyboard("month", report_type, year, month)

            if buf:
                await get_message(callback).answer_photo(
                    photo=BufferedInputFile(buf.read(), filename="report.png"),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=period_kb,
                )
            else:
                await get_message(callback).answer(
                    caption,
                    parse_mode="HTML",
                    reply_markup=period_kb,
                )
        else:
            await get_message(callback).answer("Нет данных за выбранный период.")

    try:
        await get_message(callback).delete()
    except Exception:
        pass

    await state.clear()


@router.callback_query(F.data.startswith("compare:"))
@log_exceptions("Ошибка при сравнении периодов")
async def handle_compare_periods(callback: CallbackQuery, **kwargs) -> None:
    """Сравнение текущего месяца с предыдущим."""
    try:
        parts = (callback.data or "").split(":")
        report_type = parts[1]
        year = int(parts[2])
        month = int(parts[3])
    except IndexError, ValueError, AttributeError:
        await callback.answer("Некорректные данные.")
        return

    operation_sign = "+" if report_type == "income" else "-"

    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    cur_date_from = datetime(year, month, 1, tzinfo=ZoneInfo(TIMEZONE))
    if month == 12:
        cur_date_to = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(TIMEZONE)) - timedelta(
            seconds=1
        )
    else:
        cur_date_to = datetime(
            year, month + 1, 1, tzinfo=ZoneInfo(TIMEZONE)
        ) - timedelta(seconds=1)

    prev_date_from = datetime(prev_year, prev_month, 1, tzinfo=ZoneInfo(TIMEZONE))
    if prev_month == 12:
        prev_date_to = datetime(
            prev_year + 1, 1, 1, tzinfo=ZoneInfo(TIMEZONE)
        ) - timedelta(seconds=1)
    else:
        prev_date_to = datetime(
            prev_year, prev_month + 1, 1, tzinfo=ZoneInfo(TIMEZONE)
        ) - timedelta(seconds=1)

    await callback.answer("⏳ Формирую сравнение...")

    user_id = kwargs.get("user_id")
    if not user_id:
        await get_message(callback).answer("Пользователь не найден.")
        return

    async with async_session() as session:
        cur_categories = await get_categories_summary(
            session, user_id, operation_sign, cur_date_from, cur_date_to
        )
        prev_categories = await get_categories_summary(
            session, user_id, operation_sign, prev_date_from, prev_date_to
        )

        cur_total = sum(cur_categories.values(), Decimal(0))
        prev_total = sum(prev_categories.values(), Decimal(0))

        monthly_data = await get_monthly_totals(session, user_id, operation_sign)

    if not prev_categories:
        await get_message(callback).answer(
            f"Нет данных за {RU_MONTHS[prev_month]} {prev_year} для сравнения."
        )
        return

    avg_monthly: Decimal | None = None
    if monthly_data:
        avg_monthly = sum((v for _, _, v in monthly_data), Decimal(0)) / len(
            monthly_data
        )

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

    if monthly_data and len(monthly_data) >= 2:
        chart_buf = await build_trend_chart(
            monthly_data=monthly_data,
            report_type=report_type,
            current_month=(year, month),
            prev_month=(prev_year, prev_month),
        )

        if chart_buf:
            await get_message(callback).answer_photo(
                photo=BufferedInputFile(chart_buf.read(), filename="trend.png"),
                caption=comparison_text,
                parse_mode="HTML",
            )
        else:
            await get_message(callback).answer(comparison_text, parse_mode="HTML")
    else:
        await get_message(callback).answer(comparison_text, parse_mode="HTML")


# ==================== Feature 1: interactive period switch ====================


def _period_dates(period: str, year: int, month: int) -> tuple[datetime, datetime]:
    """Returns (date_from, date_to) for a period anchored at (year, month).

    month   → the given calendar month.
    quarter → last 3 months ending now.
    year    → last 12 months ending now.
    """
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    if period == "month":
        date_from = datetime(year, month, 1, tzinfo=tz)
        if month == 12:
            date_to = datetime(year + 1, 1, 1, tzinfo=tz) - timedelta(seconds=1)
        else:
            date_to = datetime(year, month + 1, 1, tzinfo=tz) - timedelta(seconds=1)
        return date_from, date_to

    if period == "quarter":
        start = (now - relativedelta(months=2)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return start, now

    # year
    start = (now - relativedelta(months=11)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return start, now


async def _render_chart_switch(
    callback: CallbackQuery, period: str, op: str, year: int, month: int, user_id: int
) -> None:
    """Rebuilds the category chart for a new period and edits the photo in place."""
    report_type = "income" if op == "inc" else "expense"
    operation_sign = "+" if op == "inc" else "-"
    title_type = "Доходы" if report_type == "income" else "Расходы"

    date_from, date_to = _period_dates(period, year, month)
    await callback.answer()

    async with async_session() as session:
        categories = await get_categories_summary(
            session, user_id, operation_sign, date_from, date_to
        )
        total = sum(categories.values()) if categories else Decimal("0.0")
        records = (
            await get_records(session, user_id, "range", date_from, date_to, limit=30)
            if period == "month"
            else None
        )

    if not categories:
        await get_message(callback).answer("Нет данных за выбранный период.")
        return

    if period == "month":
        buf, caption = await build_report_pie(
            categories, total, date_from, report_type, records
        )
    elif period == "quarter":
        buf = await build_category_chart(
            categories, total, f"{title_type} за последние 3 месяца", report_type
        )
        caption = format_period_caption(
            categories, total, "Последние 3 месяца", report_type
        )
    else:  # year
        buf = await build_category_chart(
            categories, total, f"{title_type} за последние 12 месяцев", report_type
        )
        caption = format_period_caption(
            categories, total, "Последние 12 месяцев", report_type
        )

    if not buf:
        await get_message(callback).answer("Не удалось построить график.")
        return

    kb = chart_period_keyboard(period, report_type, year, month)
    media = InputMediaPhoto(
        media=BufferedInputFile(buf.read(), filename="report.png"),
        caption=caption,
        parse_mode="HTML",
    )
    try:
        await get_message(callback).edit_media(media=media, reply_markup=kb)
    except Exception:
        logging.exception("Не удалось обновить график периода")


@router.callback_query(F.data == "chart_noop")
async def chart_noop(callback: CallbackQuery, **kwargs) -> None:
    """Tap on the central month label — no-op."""
    await callback.answer()


@router.callback_query(F.data.startswith("chart_period:"))
@log_exceptions("Ошибка при смене периода графика")
async def chart_period_handler(callback: CallbackQuery, **kwargs) -> None:
    """Switch chart period (month/quarter/year)."""
    try:
        parts = (callback.data or "").split(":")
        period, op = parts[1], parts[2]
        year, month = int(parts[3]), int(parts[4])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    user_id = kwargs.get("user_id")
    if not user_id:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    await _render_chart_switch(callback, period, op, year, month, user_id)


@router.callback_query(F.data.startswith("chart_nav:"))
@log_exceptions("Ошибка при навигации по месяцам графика")
async def chart_nav_handler(callback: CallbackQuery, **kwargs) -> None:
    """Shift month back/forward (month period only)."""
    try:
        parts = (callback.data or "").split(":")
        direction, op = parts[1], parts[2]
        year, month = int(parts[3]), int(parts[4])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    if direction == "prev":
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    else:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    now = datetime.now(ZoneInfo(TIMEZONE))
    if year > now.year or (year == now.year and month > now.month):
        await callback.answer("Нельзя смотреть будущий месяц.", show_alert=True)
        return

    user_id = kwargs.get("user_id")
    if not user_id:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    await _render_chart_switch(callback, "month", op, year, month, user_id)


# ==================== Feature: Годовой отчёт ====================


@router.callback_query(F.data == "report_section:yearly")
@log_exceptions("Ошибка при входе в годовой отчёт")
async def yearly_entry(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Раздел «Годовой отчёт» — выбор типа (Доход/Расход)."""
    await state.set_state(MenuStates.waiting_for_yearly_type)
    await get_message(callback).edit_text(
        "📅 <b>Годовой отчёт</b>\n\nВыберите тип:",
        reply_markup=yearly_report_type_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "yr_back_type")
@log_exceptions("Ошибка при возврате к выбору типа годового отчёта")
async def yearly_back_type(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Шаг назад: с выбора года обратно к выбору типа."""
    await state.set_state(MenuStates.waiting_for_yearly_type)
    await get_message(callback).edit_text(
        "📅 <b>Годовой отчёт</b>\n\nВыберите тип:",
        reply_markup=yearly_report_type_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(
    MenuStates.waiting_for_yearly_type, F.data.startswith("yr_type:")
)
@log_exceptions("Ошибка при выборе типа годового отчёта")
async def yearly_type(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Выбран тип — показываем доступные годы."""
    raw = (callback.data or "").split(":", 1)[1]
    operation = "+" if raw == "income" else "-"

    user_id = kwargs.get("user_id")
    if not user_id:
        await get_message(callback).edit_text("Пользователь не найден.")
        await state.clear()
        await callback.answer()
        return

    async with async_session() as session:
        years_months = await get_available_years_and_months(session, user_id, operation)
    years = sorted(years_months.keys())

    if not years:
        type_name = "доходам" if operation == "+" else "расходам"
        await get_message(callback).edit_text(
            f"Нет записей по {type_name} для годового отчёта."
        )
        await state.clear()
        await callback.answer()
        return

    await state.update_data(yearly_operation=operation, yearly_years=years)
    type_name = "Доходы" if operation == "+" else "Расходы"
    await get_message(callback).edit_text(
        f"Тип: <b>{type_name}</b>\n\nВыберите год:",
        reply_markup=yearly_report_year_keyboard(years),
        parse_mode="HTML",
    )
    await state.set_state(MenuStates.waiting_for_yearly_year)
    await callback.answer()


@router.callback_query(F.data == "yr_back_year")
@log_exceptions("Ошибка при возврате к выбору года годового отчёта")
async def yearly_back_year(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Шаг назад: с выбора категорий обратно к выбору года."""
    data = await state.get_data()
    years = data.get("yearly_years", [])
    operation = data.get("yearly_operation")
    if not years or operation is None:
        await callback.answer("Сессия истекла.", show_alert=True)
        await state.clear()
        return
    type_name = "Доходы" if operation == "+" else "Расходы"
    await get_message(callback).edit_text(
        f"Тип: <b>{type_name}</b>\n\nВыберите год:",
        reply_markup=yearly_report_year_keyboard(years),
        parse_mode="HTML",
    )
    await state.set_state(MenuStates.waiting_for_yearly_year)
    await callback.answer()


@router.callback_query(
    MenuStates.waiting_for_yearly_year, F.data.startswith("yr_year:")
)
@log_exceptions("Ошибка при выборе года годового отчёта")
async def yearly_year(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Выбран год (или «За всё время») — показываем категории."""
    raw = (callback.data or "").split(":", 1)[1]
    if raw == "all":
        year: int | None = None
    else:
        try:
            year = int(raw)
        except ValueError:
            await callback.answer("Некорректные данные.")
            return

    data = await state.get_data()
    operation = data.get("yearly_operation")
    user_id = kwargs.get("user_id")
    if not user_id or operation is None:
        await get_message(callback).edit_text("Сессия истекла. Откройте отчёт заново.")
        await state.clear()
        await callback.answer()
        return

    async with async_session() as session:
        cats = await get_categories_for_year(session, user_id, operation, year)

    await state.update_data(yearly_year=year, yearly_cats_list=cats, yearly_selected=[])
    subtitle = "за всё время" if year is None else str(year)
    await get_message(callback).edit_text(
        f"Год: <b>{subtitle}</b>\n\nВыберите категории (можно несколько).\n"
        "<i>Ничего не выбрано = все категории.</i>",
        reply_markup=yearly_report_cats_keyboard(cats, set()),
        parse_mode="HTML",
    )
    await state.set_state(MenuStates.waiting_for_yearly_cats)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_yearly_cats, F.data.startswith("yr_cat:"))
@log_exceptions("Ошибка при выборе категории годового отчёта")
async def yearly_cat_toggle(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Toggle одной категории по индексу — перерисовываем клавиатуру."""
    try:
        idx = int((callback.data or "").split(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    cats = data.get("yearly_cats_list", [])
    selected = set(data.get("yearly_selected", []))
    if idx < 0 or idx >= len(cats):
        await callback.answer()
        return

    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)
    await state.update_data(yearly_selected=list(selected))

    try:
        await get_message(callback).edit_reply_markup(
            reply_markup=yearly_report_cats_keyboard(cats, selected)
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_yearly_cats, F.data == "yr_done")
@log_exceptions("Ошибка при формировании годового отчёта")
async def yearly_done(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Готово — строим текстовый отчёт + график."""
    data = await state.get_data()
    operation = data.get("yearly_operation")
    year = data.get("yearly_year")
    cats = data.get("yearly_cats_list", [])
    selected = data.get("yearly_selected", [])

    user_id = kwargs.get("user_id")
    if not user_id or operation is None:
        await get_message(callback).edit_text("Сессия истекла. Откройте отчёт заново.")
        await state.clear()
        await callback.answer()
        return

    categories = [cats[i] for i in selected if 0 <= i < len(cats)] or None

    await get_message(callback).edit_text("⏳ Генерация годового отчёта...")
    await callback.answer()

    async with async_session() as session:
        report = await get_yearly_report(session, user_id, operation, year, categories)

    if not report:
        await get_message(callback).edit_text("Нет данных за выбранный период.")
        await state.clear()
        return

    buf = await build_yearly_chart(report, operation, year)
    text = format_yearly_report(report, year, operation)
    msg = get_message(callback)

    if buf:
        photo = BufferedInputFile(buf.read(), filename="yearly.png")
        if len(text) <= MAX_CAPTION_LENGTH:
            await msg.answer_photo(photo=photo, caption=text, parse_mode="HTML")
        else:
            await msg.answer_photo(photo=photo)
            await msg.answer(text, parse_mode="HTML")
    else:
        await msg.answer(text, parse_mode="HTML")

    try:
        await msg.delete()
    except Exception:
        pass

    await state.clear()
