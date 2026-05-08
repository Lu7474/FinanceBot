"""
Common utilities: money formatting, locale constants, exception decorator.
"""
import html
import logging
from datetime import date as date_type
from decimal import Decimal
from functools import wraps
from typing import Callable

from aiogram.exceptions import TelegramBadRequest


def format_money(amount: float | int) -> str:
    """Форматирует сумму с пробелами как разделителями тысяч (русская локаль)."""
    return f"{amount:,.0f}₽".replace(",", " ")


RU_MONTHS = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

RU_WEEKDAYS = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}


RU_MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def format_date_ru(d: date_type) -> str:
    """Formats date as '15 марта 2025'."""
    return f"{d.day} {RU_MONTHS_GEN[d.month]} {d.year}"


def format_snapshot(items: list, prev_items: list | None, snapshot_date: date_type) -> str:
    """Formats savings snapshot text with dynamic comparison to previous snapshot."""
    prev_map: dict[str, Decimal] = {}
    if prev_items:
        for item in prev_items:
            prev_map[item.name] = item.amount

    date_str = format_date_ru(snapshot_date)
    lines = [f"💰 <b>Накопления</b>\n\n📅 {date_str}\n"]

    total = Decimal("0")
    for item in items:
        amount_str = format_money(float(item.amount))
        if item.name in prev_map:
            diff = item.amount - prev_map[item.name]
            if diff > 0:
                diff_str = f"  <i>(+{format_money(float(diff))})</i>"
            elif diff < 0:
                diff_str = f"  <i>(−{format_money(float(abs(diff)))})</i>"
            else:
                diff_str = "  <i>(=)</i>"
        else:
            diff_str = ""
        lines.append(f"{html.escape(item.name)}:  <b>{amount_str}</b>{diff_str}")
        total += item.amount

    lines.append(f"\n<b>Итого:  {format_money(float(total))}</b>")
    return "\n".join(lines)


def format_wealth(items: list) -> str:
    """Formats wealth items with assets/liabilities breakdown and net worth."""
    assets = [i for i in items if i.type == "A"]
    liabilities = [i for i in items if i.type == "P"]

    lines = ["📊 <b>Финансовый баланс</b>\n"]

    lines.append("💚 <b>АКТИВЫ</b>")
    total_assets = Decimal("0")
    if assets:
        for item in assets:
            note = f"  <i>{html.escape(item.note)}</i>" if item.note else ""
            lines.append(f"  {html.escape(item.name)}  —  {format_money(float(item.amount))}{note}")
            total_assets += item.amount
    else:
        lines.append("  <i>Нет данных</i>")
    lines.append(f"  <b>Итого активов:  {format_money(float(total_assets))}</b>")

    lines.append("")
    lines.append("🔴 <b>ПАССИВЫ</b>")
    total_liabilities = Decimal("0")
    if liabilities:
        for item in liabilities:
            note = f"  <i>{html.escape(item.note)}</i>" if item.note else ""
            lines.append(f"  {html.escape(item.name)}  —  {format_money(float(item.amount))}{note}")
            total_liabilities += item.amount
    else:
        lines.append("  <i>Нет данных</i>")
    lines.append(f"  <b>Итого пассивов:  {format_money(float(total_liabilities))}</b>")

    net = total_assets - total_liabilities
    sign = "+" if net >= 0 else ""
    lines.append(f"<b>Чистый капитал:  {sign}{format_money(float(net))}</b>")

    return "\n".join(lines)


def log_exceptions(error_text: str) -> Callable:
    """Декоратор: логирует исключения и отправляет сообщение пользователю."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception:
                message_or_callback = args[0]
                state = args[1] if len(args) > 1 else None

                user_id = None
                if hasattr(message_or_callback, "from_user") and message_or_callback.from_user:
                    user_id = message_or_callback.from_user.id

                logging.exception(f"{error_text} [user_id={user_id}]")

                try:
                    if hasattr(message_or_callback, "edit_text"):
                        try:
                            await message_or_callback.edit_text(error_text)
                        except TelegramBadRequest:
                            await message_or_callback.answer(error_text)
                    else:
                        await message_or_callback.answer(error_text)
                except Exception:
                    pass
                if state:
                    await state.clear()
                return None

        return wrapper

    return decorator
