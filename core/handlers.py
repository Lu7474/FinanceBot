from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from datetime import datetime, timedelta
import re

from core.keyboards import delete_period_keyboard
from core.database.requests import set_user, add_record, get_records, delete_record
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
            await add_record(session, message.from_user.id, operation, amount, category)

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
        records = await get_records(session, callback.from_user.id, period)

    if not records:
        await callback.message.edit_text("Записей за выбранный период нет.")
        return

    kb = InlineKeyboardBuilder()

    for r in records:
        local_time = r.created_at + timedelta(hours=3)
        text = f"{'➖' if r.operation == '-' else '➕'} {r.amount:.0f}₽ - {r.category} ({local_time.strftime('%d.%m.%Y')})"
        kb.button(text=text, callback_data=f"del_record:{r.id}")

    kb.adjust(1)
    await callback.message.edit_text(
        "Выберите запись для удаления:", reply_markup=kb.as_markup()
    )


# позже вынести её в utils.py
def parse_date(text: str) -> datetime | None:
    text = text.lower().strip()
    text = text.replace("г.", "").replace("г", "")

    # Форматы: день.месяц.год
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            return dt
        except ValueError:
            continue

    # Форматы: день месяц год (на русском)
    for fmt in ("%d %B %Y", "%d %B %y"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            return dt
        except ValueError:
            continue

    return None


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
        records = await get_records(
            session, message.from_user.id, within, date_from, date_to
        )

    if records:
        sumadd = sum(float(r.amount) for r in records if r.operation == "+")
        sumspent = sum(float(r.amount) for r in records if r.operation == "-")
        remaining = sumadd - sumspent

        answer = "🕘 История операций:\n\n"
        for r in records:
            local_time = r.created_at + timedelta(hours=3)
            category = f" - {r.category}" if getattr(r, "category", None) else ""
            symbol = "➖" if r.operation == "-" else "➕"
            answer += f"{symbol} {r.amount:,.0f}₽{category} ({local_time.strftime('%d.%m.%Y')})\n"

        answer += f"\nСумма доходов: {sumadd:,.0f}₽".replace(",", ".")
        answer += f"\nСумма расходов: {sumspent:,.0f}₽".replace(",", ".")
        answer += f"\nОстаток: {remaining:,.0f}₽".replace(",", ".")
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
