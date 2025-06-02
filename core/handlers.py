from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from datetime import datetime, timedelta
from collections import defaultdict
import re

from core.keyboards import (
    delete_period_keyboard,
    get_years_keyboard,
    get_months_keyboard,
)
from core.utils import (
    parse_date,
    get_available_years_and_months,
    build_report_pie,
    make_history_text,
    RU_MONTHS,
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


@router.message(Command("start"))
async def handle_start(message: Message):
    async with async_session() as session:
        await set_user(message.from_user.id, name=message.from_user.full_name)

    await message.answer(
        "Добро пожаловать!\n/e - Запись дохода\n(/e 100)\n/s - Запись расхода\n(/s 100)\n/h - История операций\n(/h (день/месяц/год)",
    )


@router.message(Command("spent", "earned", "s", "e"))
async def handle_add_record(message: Message):
    cmd_variants = (
        ("/spent", "/s", "!spent", "!s"),
        ("/earned", "/e", "!earned", "!e"),
    )

    text = message.text

    if any(text.startswith(prefix) for prefix in cmd_variants[0]):
        operation = "-"
    else:
        operation = "+"

    # Убираем команды
    for group in cmd_variants:
        for cmd in group:
            text = text.replace(cmd, "")
    text = text.strip()

    # Ищем сумму
    match = re.search(r"([-+]?\d+(?:[.,]\d+)?)", text)
    if match:
        amount = float(match.group(1).replace(",", "."))
        category = text.replace(match.group(0), "").strip()
        if not category:
            category = "не указано"

        async with async_session() as session:
            await set_user(message.from_user.id, name=message.from_user.full_name)
            user = await get_user_by_tg_id(session, message.from_user.id)
            if not user:
                await message.answer("Ошибка: пользователь не найден.")
                return
                
            success = await add_record(session, message.from_user.id, operation, amount, category)
            if not success:
                await message.answer("Ошибка при добавлении записи.")
                return

        if operation == "-":
            await message.answer("✅ Запись о расходе успешно внесена!")
        else:
            await message.answer("✅ Запись о доходе успешно внесена!")
    else:
        await message.answer("Не удалось определить сумму!")


@router.message(Command("delete"))
async def delete_menu(message: Message):
    await message.answer(
        "За какой период показать записи для удаления?",
        reply_markup=delete_period_keyboard(),
    )


@router.callback_query(lambda c: c.data.startswith("del_period:"))
async def handle_period_selection(callback: CallbackQuery):
    period = callback.data.split(":")[1]
    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            return
        records = await get_records(session, user.id, period)

    if not records:
        await callback.message.edit_text("Записей за выбранный период нет.")
        return

    kb = InlineKeyboardBuilder()

    for r in records:
        text = f"{'➖' if r.operation == '-' else '➕'} {r.amount:.0f}₽ - {r.category} ({r.created_at.strftime('%d.%m.%Y')})"
        kb.button(text=text, callback_data=f"del_record:{r.id}")

    kb.adjust(1)
    await callback.message.edit_text(
        "Выберите запись для удаления:", reply_markup=kb.as_markup()
    )


@router.message(Command("history", "h"))
async def handle_history(message: Message):
    text = message.text.lower().strip()
    text = re.sub(r"^(/h|/history|!h|!history)\s*", "", text)

    within = "day"
    date_from: datetime | None = None
    date_to: datetime | None = None

    if not text:
        pass  # по умолчанию: за день
    elif text in ["день", "сегодня", "day", "today"]:
        within = "day"
    elif text in ["месяц", "month"]:
        within = "month"
    elif text in ["год", "year"]:
        within = "year"
    elif "с" in text and "по" in text:
        # диапазон: с ... по ...
        parts = re.findall(r"с (.*?) по (.+)", text)
        if parts:
            start_str, end_str = parts[0]
            start_date = parse_date(start_str.strip())
            end_date = parse_date(end_str.strip())
            if start_date and end_date:
                date_from = start_date.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                date_to = end_date.replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
                within = "range"
            else:
                await message.answer(
                    "Не удалось распознать диапазон. Пример:\n/h с 01.01.2024 по 10.01.2024"
                )
                return
        else:
            await message.answer(
                "Неверный формат команды. Пример:\n/h с 01.01.2024 по 10.01.2024"
            )
            return
    else:
        parsed = parse_date(text)
        if parsed:
            date_from = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
            within = "date"
        else:
            await message.answer(
                "Не удалось распознать дату. Примеры:\n/h 10.10.2024\n/h 10 октября 2024\n/h с 01.01.24 по 10.01.24"
            )
            return

    async with async_session() as session:
        user = await get_user_by_tg_id(session, message.from_user.id)
        if not user:
            await message.answer("Пользователь не найден.")
            return
        records = await get_records(
            session, user.id, within, date_from, date_to
        )

    if records:
        answer = make_history_text(records)
        await message.answer(answer)
    else:
        await message.answer("Записей не найдено за указанный период.")


@router.callback_query(lambda c: c.data.startswith("del_record:"))
async def handle_record_delete(callback: CallbackQuery):
    record_id = int(callback.data.split(":")[1])

    async with async_session() as session:
        deleted = await delete_record(session, callback.from_user.id, record_id)

    if deleted:
        await callback.message.edit_text("✅ Запись удалена!")
    else:
        await callback.message.edit_text("⚠️ Запись не найдена или уже удалена.")


@router.message(Command("report"))
async def report_start(message: Message):
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


@router.callback_query(F.data.startswith("report_year:"))
async def report_year(callback: CallbackQuery):
    year = int(callback.data.split(":")[1])
    now = datetime.utcnow()
    current_year = now.year
    current_month = now.month

    async with async_session() as session:
        user = await get_user_by_tg_id(session, callback.from_user.id)
        if not user:
            await callback.message.edit_text("Пользователь не найден.")
            await callback.answer()
            return
        years_months = await get_available_years_and_months(session, user.id)

    if year not in years_months:
        await callback.answer("Нет записей за этот год.")
        return

    # Фильтруем будущие месяцы
    available_months = [
        month for month in years_months[year]
        if year < current_year or (year == current_year and month <= current_month)
    ]

    if not available_months:
        await callback.answer("Нет доступных месяцев для отчета.")
        return

    keyboard = get_months_keyboard(year, available_months)
    await callback.message.edit_text(
        f"Выберите месяц {year} года:", reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("report_month:"))
async def show_monthly_report(callback: CallbackQuery):
    parts = callback.data.split(":")
    year = int(parts[1])
    month = int(parts[2])

    # Проверяем, не является ли запрашиваемый месяц будущим
    now = datetime.utcnow()
    if year > now.year or (year == now.year and month > now.month):
        await callback.message.edit_text("Нельзя получить отчет за будущий месяц.")
        await callback.answer()
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
            return
        records = await get_records(session, user.id, "range", date_from, date_to)

    if not records:
        month_name = RU_MONTHS[date_from.month]
        await callback.message.edit_text(
            f"За {month_name} {date_from.year} записей не найдено."
        )
        await callback.answer()
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
        return

    buf, caption = build_report_pie(categories, total, date_from)
    if buf is None:
        await callback.message.edit_text(caption)
        await callback.answer()
        return

    await callback.message.answer_photo(
        photo=BufferedInputFile(buf.read(), filename="report.png"),
        caption=caption,
    )
    await callback.answer()
