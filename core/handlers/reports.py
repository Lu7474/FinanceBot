"""Handlers for reports (charts) and period comparison."""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import TIMEZONE
from core.charts import build_report_pie, build_trend_chart
from core.database.models import async_session
from core.database.requests import (
    get_categories_summary,
    get_monthly_totals,
    get_records,
)
from core.keyboards import (
    get_months_keyboard,
    get_years_keyboard,
    main_menu_keyboard,
    report_type_keyboard,
)
from core.reports import get_available_years_and_months, make_comparison_text
from core.utils import RU_MONTHS, log_exceptions

from .common import MenuStates, is_expense, is_income, is_report

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

    user_id = kwargs.get("user_id")
    if not user_id:
        await message.answer("Пользователь не найден.")
        await state.clear()
        return
    async with async_session() as session:
        years_months = await get_available_years_and_months(session, user_id, operation)

    if not years_months:
        await message.answer(
            f"Нет записей по категории «{report_type}» для отображения отчёта.",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    await state.update_data(report_years_months=years_months)

    await message.answer(
        "Тип отчёта: " + report_type, reply_markup=main_menu_keyboard()
    )
    keyboard = get_years_keyboard(list(years_months.keys()))
    await message.answer("Выберите год:", reply_markup=keyboard)
    await state.set_state(MenuStates.waiting_for_report_year)


@router.callback_query(MenuStates.waiting_for_report_year)
@log_exceptions("Ошибка при получении месяцев для отчёта")
async def menu_report_year(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран год — показываем доступные месяцы."""
    try:
        year = int(callback.data.split(":")[1])
    except (IndexError, ValueError, AttributeError):
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
    await callback.message.edit_text(
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
    await callback.message.edit_text("Выберите год:", reply_markup=keyboard)
    await state.set_state(MenuStates.waiting_for_report_year)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_report_month)
@log_exceptions("Ошибка при формировании отчёта")
async def menu_report_month(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран месяц — генерируем график и текстовый отчёт."""
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

    now = datetime.now(ZoneInfo(TIMEZONE))
    if year > now.year or (year == now.year and month > now.month):
        await callback.message.edit_text("Нельзя получить отчет за будущий месяц.")
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

    await callback.message.edit_text("⏳ Генерация отчёта...")
    await callback.answer()

    user_id = kwargs.get("user_id")
    if not user_id:
        await callback.message.edit_text("Пользователь не найден.")
        await state.clear()
        return

    async with async_session() as session:
        categories = await get_categories_summary(
            session, user_id, operation_sign, date_from, date_to
        )
        total = sum(categories.values()) if categories else Decimal("0.0")

        records = await get_records(session, user_id, "range", date_from, date_to, limit=30)

        if categories:
            buf, caption = await build_report_pie(
                categories, total, date_from, report_type, records
            )

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
                await callback.message.answer(
                    caption,
                    parse_mode="HTML",
                    reply_markup=compare_kb.as_markup(),
                )
        else:
            await callback.message.answer("Нет данных за выбранный период.")

    try:
        await callback.message.delete()
    except Exception:
        pass

    await state.clear()


@router.callback_query(F.data.startswith("compare:"))
@log_exceptions("Ошибка при сравнении периодов")
async def handle_compare_periods(callback: CallbackQuery, **kwargs) -> None:
    """Сравнение текущего месяца с предыдущим."""
    try:
        parts = callback.data.split(":")
        report_type = parts[1]
        year = int(parts[2])
        month = int(parts[3])
    except (IndexError, ValueError, AttributeError):
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
        await callback.message.answer("Пользователь не найден.")
        return

    async with async_session() as session:
        cur_categories = await get_categories_summary(
            session, user_id, operation_sign, cur_date_from, cur_date_to
        )
        prev_categories = await get_categories_summary(
            session, user_id, operation_sign, prev_date_from, prev_date_to
        )

        cur_total = sum(cur_categories.values()) if cur_categories else Decimal("0")
        prev_total = sum(prev_categories.values()) if prev_categories else Decimal("0")

        monthly_data = await get_monthly_totals(session, user_id, operation_sign)

    if not prev_categories:
        await callback.message.answer(
            f"Нет данных за {RU_MONTHS[prev_month]} {prev_year} для сравнения."
        )
        return

    avg_monthly = None
    if monthly_data:
        avg_monthly = sum(v for _, _, v in monthly_data) / len(monthly_data)

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
            await callback.message.answer_photo(
                photo=BufferedInputFile(chart_buf.read(), filename="trend.png"),
                caption=comparison_text,
                parse_mode="HTML",
            )
        else:
            await callback.message.answer(comparison_text, parse_mode="HTML")
    else:
        await callback.message.answer(comparison_text, parse_mode="HTML")
