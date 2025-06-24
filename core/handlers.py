from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from datetime import datetime, timedelta
from collections import defaultdict
import re

from core.keyboards import (
    delete_period_keyboard,
    get_years_keyboard,
    get_months_keyboard,
    main_menu_keyboard,
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
)
from core.database.models import async_session


router = Router()


class AddRecord(StatesGroup):
    waiting_for_amount = State()


class MenuStates(StatesGroup):
    waiting_for_history_period = State()
    waiting_for_report_year = State()
    waiting_for_report_month = State()
    waiting_for_delete_period = State()
    waiting_for_delete_record = State()


# ====== Главное меню и старт ======
@router.message(Command("start"))
@log_exceptions("Ошибка при инициализации пользователя")
async def handle_start(message: Message, **kwargs):
    async with async_session() as session:
        await set_user(message.from_user.id, name=message.from_user.full_name)

    await message.answer(
        "Добро пожаловать!\nВыберите действие:",
        reply_markup=main_menu_keyboard(),
    )


# ====== Добавление записи ======
@router.message(F.text.in_(["➕ Доход", "➖ Расход"]))
@log_exceptions("Ошибка при обработке операции")
async def handle_income_expense(message: Message, state: FSMContext, **kwargs):
    operation = "+" if message.text == "➕ Доход" else "-"
    await state.update_data(operation=operation)
    await message.answer("Введите сумму и категорию, например: 1000 еда")
    await state.set_state(AddRecord.waiting_for_amount)


@router.message(AddRecord.waiting_for_amount)
@log_exceptions("Ошибка при добавлении записи")
async def handle_amount_and_category(message: Message, state: FSMContext, **kwargs):
    # Используем регулярку для поиска суммы
    match = re.search(r"([-+]?\d+(?:[.,]\d+)?)", message.text)
    if not match:
        await message.answer("Введите сумму и категорию, например: 1000 еда")
        return
    try:
        amount = float(match.group(1).replace(",", "."))
    except ValueError:
        await message.answer("Введите корректную сумму.")
        return

    # Категория — всё, что после суммы
    category = message.text.replace(match.group(0), "").strip()
    if not category:
        category = "не указано"

    data = await state.get_data()
    operation = data.get("operation")

    # async with async_session() as session:
    #     await set_user(message.from_user.id, name=message.from_user.full_name)
    #     await add_record(session, message.from_user.id, operation, amount, category)
    async with async_session() as session:
        await set_user(message.from_user.id, name=message.from_user.full_name)
        ok = await add_record(
            session, message.from_user.id, operation, amount, category
        )
        if not ok:
            await message.answer("Ошибка при добавлении записи.")
            return

    await message.answer("✅ Запись добавлена!", reply_markup=main_menu_keyboard())
    await state.clear()


# ====== История ======
@router.message(F.text == "🕘 История")
@log_exceptions("Ошибка при показе истории")
async def menu_history(message: Message, state: FSMContext, **kwargs):
    await message.answer(
        "За какой период показать историю?",
        reply_markup=delete_period_keyboard(),
    )
    await state.set_state(MenuStates.waiting_for_history_period)


@router.callback_query(MenuStates.waiting_for_history_period)
@log_exceptions("Ошибка при получении истории")
async def menu_history_period(callback: CallbackQuery, state: FSMContext, **kwargs):
    period = callback.data.split(":")[1]
    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await state.clear()
            return
        records = await get_records(session, user.id, period)
    if records:
        answer = make_history_text(records)
        await callback.message.edit_text(answer)
    else:
        await callback.message.edit_text("Записей не найдено за указанный период.")
    await state.clear()
    await callback.answer()


# ====== Отчёт ======
@router.message(F.text == "📊 Отчёт")
@log_exceptions("Произошла ошибка при формировании отчёта")
async def menu_report(message: Message, state: FSMContext, **kwargs):
    async with async_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Пользователь не найден.")
            return
        years_months = await get_available_years_and_months(session, user.id)
    if not years_months:
        await message.answer("Нет записей для отображения отчёта.")
        return
    keyboard = get_years_keyboard(list(years_months.keys()))
    await message.answer("Выберите год для отчёта:", reply_markup=keyboard)
    await state.set_state(MenuStates.waiting_for_report_year)


@router.callback_query(MenuStates.waiting_for_report_year)
@log_exceptions("Ошибка при получении месяцев для отчёта")
async def menu_report_year(callback: CallbackQuery, state: FSMContext, **kwargs):
    year = int(callback.data.split(":")[1])
    now = datetime.utcnow()
    current_year = now.year
    current_month = now.month

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
async def menu_report_month(callback: CallbackQuery, state: FSMContext, **kwargs):
    parts = callback.data.split(":")
    year = int(parts[1])
    month = int(parts[2])

    now = datetime.utcnow()
    if year > now.year or (year == now.year and month > now.month):
        await callback.message.edit_text("Нельзя получить отчет за будущий месяц.")
        await callback.answer()
        await state.clear()
        return

    date_from = datetime(year, month, 1)
    if month == 12:
        date_to = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        date_to = datetime(year, month + 1, 1) - timedelta(seconds=1)

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await callback.answer()
            await state.clear()
            return
        records = await get_records(session, user.id, "range", date_from, date_to)

    if not records:
        month_name = RU_MONTHS[date_from.month]
        await callback.message.edit_text(
            f"За {month_name} {date_from.year} записей не найдено."
        )
        await callback.answer()
        await state.clear()
        return

    categories = defaultdict(float)
    total = 0.0
    for r in records:
        if r.operation == "-":
            cat = r.category or "Без категории"
            categories[cat] += float(r.amount)
            total += float(r.amount)

    if not categories:
        month_name = RU_MONTHS[date_from.month]
        await callback.message.edit_text(
            f"За {month_name} {date_from.year} нет расходов для отображения."
        )
        await callback.answer()
        await state.clear()
        return

    buf, caption = build_report_pie(categories, total, date_from)
    if buf is None:
        await callback.message.edit_text(caption)
        await callback.answer()
        await state.clear()
        return

    await callback.message.answer_photo(
        photo=BufferedInputFile(buf.read(), filename="report.png"),
        caption=caption,
    )
    await callback.answer()
    await state.clear()


# ====== Удаление записи ======
@router.message(F.text == "🗑️ Удалить запись")
@log_exceptions("Ошибка при показе меню удаления")
async def menu_delete(message: Message, state: FSMContext, **kwargs):
    await message.answer(
        "За какой период показать записи для удаления?",
        reply_markup=delete_period_keyboard(),
    )
    await state.set_state(MenuStates.waiting_for_delete_period)


@router.callback_query(MenuStates.waiting_for_delete_period)
@log_exceptions("Ошибка при получении записей для удаления")
async def menu_delete_period(callback: CallbackQuery, state: FSMContext, **kwargs):
    period = callback.data.split(":")[1]
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

    kb = InlineKeyboardBuilder()
    for r in records:
        text = f"{'➖' if r.operation == '-' else '➕'} {r.amount:.0f}₽ - {r.category} ({r.created_at.strftime('%d.%m.%Y')})"
        kb.button(text=text, callback_data=f"del_record:{r.id}")

    kb.adjust(1)
    await callback.message.edit_text(
        "Выберите запись для удаления:", reply_markup=kb.as_markup()
    )
    await state.set_state(MenuStates.waiting_for_delete_record)
    await callback.answer()


@router.callback_query(MenuStates.waiting_for_delete_record)
@log_exceptions("Ошибка при удалении записи")
async def menu_delete_record(callback: CallbackQuery, state: FSMContext, **kwargs):
    record_id = int(callback.data.split(":")[1])
    async with async_session() as session:
        deleted = await delete_record(session, callback.from_user.id, record_id)
    if deleted:
        await callback.message.edit_text("✅ Запись удалена!")
    else:
        await callback.message.edit_text("⚠️ Запись не найдена или уже удалена.")
    await state.clear()
    await callback.answer()
