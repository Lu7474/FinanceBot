from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
import config
import re
from core.database.requests import set_user, add_record, get_records
from core.database.models import async_session
from datetime import timedelta

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
    # operation = "-" if message.text.startswith(cmd_variants[0]) else "+"

    if any(message.text.startswith(prefix) for prefix in cmd_variants[0]):
        operation = "-"
    else:
        operation = "+"

    value = message.text
    for i in cmd_variants:
        for j in i:
            value = value.replace(j, "").strip()

    if len(value):
        x = re.findall(r"\d+(?:[.,]\d+)?", value)
        if len(x):
            value = float(x[0].replace(",", "."))

            async with async_session() as session:
                await set_user(message.from_user.id, name=message.from_user.full_name)
                await add_record(session, message.from_user.id, operation, value)

            if operation == "-":
                await message.answer("✅ Запись о расходе успешно внесена!")
            else:
                await message.answer("✅ Запись о доходе успешно внесена!")
        else:
            await message.answer("Не удалось определить сумму!")
    else:
        await message.answer("Не введена сумма!")


@router.message(Command("history", "h"))
async def handle_history(message: Message):
    cmd_variants = ("/history", "/h", "!history", "!h")
    within_als = {
        "day": ("today", "day", "сегодня", "день"),
        "month": ("month", "месяц", "за месяц"),
        "year": ("year", "год", "за год"),
    }

    cmd = message.text
    for r in cmd_variants:
        cmd = cmd.replace(r, "").strip().lower()

    within = "day"
    if cmd:
        for k, aliases in within_als.items():
            if any(alias in cmd for alias in aliases):
                within = k
                break

    async with async_session() as session:
        records = await get_records(session, message.from_user.id, within)

    if records:
        answer = f"🕘 История операций за {within_als[within][-1]}\n\n"
        sumadd = 0
        sumspent = 0
        for r in records:
            local_time = r.created_at + timedelta(hours=3)
            answer += "➖" if r.operation == "-" else "➕"
            answer += f" - {r.amount:,.0f}₽".replace(",", ".")
            answer += f" ({local_time.strftime('%d.%m.%Y')})\n"
            # со временем убрал
            # answer += f" ({local_time.strftime('%d.%m.%Y %H:%M')})\n"
            if r.operation == "-":
                sumspent += float(r.amount)
            else:
                sumadd += float(r.amount)
        answer += f"\nСумма доходов = {sumadd:,.0f}₽".replace(",", ".")
        answer += f"\nСумма расходов = {sumspent:,.0f}₽".replace(",", ".")
        answer += f"\nОсталось = {sumadd-sumspent:,.0f}₽".replace(",", ".")

        await message.answer(answer)
    else:
        await message.answer("Записей не обнаружено!")
