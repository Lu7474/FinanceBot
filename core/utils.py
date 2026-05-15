"""
Common utilities: money formatting, locale constants, exception decorator.
"""

import html
import logging
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def format_date_ru(d: date_type) -> str:
    """Formats date as '15 марта 2025'."""
    return f"{d.day} {RU_MONTHS_GEN[d.month]} {d.year}"


def format_snapshot(
    items: list, prev_items: list | None, snapshot_date: date_type
) -> str:
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
            lines.append(
                f"  {html.escape(item.name)}  —  {format_money(float(item.amount))}{note}"
            )
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
            lines.append(
                f"  {html.escape(item.name)}  —  {format_money(float(item.amount))}{note}"
            )
            total_liabilities += item.amount
    else:
        lines.append("  <i>Нет данных</i>")
    lines.append(f"  <b>Итого пассивов:  {format_money(float(total_liabilities))}</b>")

    net = total_assets - total_liabilities
    sign = "+" if net >= 0 else ""
    lines.append(f"<b>Чистый капитал:  {sign}{format_money(float(net))}</b>")

    return "\n".join(lines)


SYSTEM_KEYWORDS: dict[str, str] = {
    "такси": "Транспорт",
    "убер": "Транспорт",
    "яндекс.такси": "Транспорт",
    "автобус": "Транспорт",
    "метро": "Транспорт",
    "маршрутка": "Транспорт",
    "кафе": "Кафе",
    "ресторан": "Кафе",
    "макдак": "Кафе",
    "кофе": "Кафе",
    "продукты": "Еда",
    "магазин": "Еда",
    "пятёрочка": "Еда",
    "дикси": "Еда",
    "аптека": "Здоровье",
    "лекарства": "Здоровье",
    "кино": "Развлечения",
    "театр": "Развлечения",
    "концерт": "Развлечения",
    "зарплата": "Зарплата",
    "оклад": "Зарплата",
}


def parse_search_query(query: str) -> dict:
    """Parses raw search string into structured filter.

    Returns:
        {"type": "gt" | "lt" | "eq" | "text", "value": float | str, "operation": "+" | "-"}
    """
    q = query.strip()
    operation = None
    amount_query = q

    if q[:1] in {"+", "-"} and q[1:].lstrip().startswith((">", "<", "=")):
        operation = q[0]
        amount_query = q[1:].strip()
    else:
        aliases = {
            "income": "+",
            "доход": "+",
            "доходы": "+",
            "expense": "-",
            "expenses": "-",
            "расход": "-",
            "расходы": "-",
        }
        q_lower = q.casefold()
        for prefix, op in aliases.items():
            if q_lower.startswith(prefix):
                rest = q[len(prefix):].strip()
                if rest.startswith((">", "<", "=")):
                    operation = op
                    amount_query = rest
                    break

    if amount_query.startswith(">"):
        try:
            result = {"type": "gt", "value": float(amount_query[1:].strip())}
            if operation:
                result["operation"] = operation
            return result
        except ValueError:
            pass
    elif amount_query.startswith("<"):
        try:
            result = {"type": "lt", "value": float(amount_query[1:].strip())}
            if operation:
                result["operation"] = operation
            return result
        except ValueError:
            pass
    elif amount_query.startswith("="):
        try:
            result = {"type": "eq", "value": float(amount_query[1:].strip())}
            if operation:
                result["operation"] = operation
            return result
        except ValueError:
            pass
    return {"type": "text", "value": q}


def format_day_total(total: float) -> str:
    """Returns '+2 700₽' or '−3 650₽'."""
    sign = "+" if total >= 0 else "−"
    return f"{sign}{abs(total):,.0f}₽".replace(",", " ")


def normalize_category(text: str) -> str:
    """Capitalizes first letter, strips whitespace."""
    text = text.strip()
    return text[0].upper() + text[1:] if text else text


def parse_edit_amount(text: str):
    """Parse amount string: '1500', '1 500', '1500.50', '1500,50'.

    Returns Decimal on success or None if invalid.
    """
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return value


def parse_edit_date(text: str, tz: str):
    """Parse 'DD.MM' (current year) or 'DD.MM.YY' date strings.

    Returns timezone-aware datetime at 12:00 or None if invalid/future.
    """
    from zoneinfo import ZoneInfo as _ZI

    text = text.strip()
    parts = text.split(".")
    now = datetime.now(_ZI(tz))

    if len(parts) == 2:
        fmt = "%d.%m"
        try:
            parsed = datetime.strptime(text, fmt).replace(year=now.year)
        except ValueError:
            return None
    elif len(parts) == 3 and len(parts[2]) == 2:
        fmt = "%d.%m.%y"
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            return None
    elif len(parts) == 3 and len(parts[2]) == 4:
        fmt = "%d.%m.%Y"
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            return None
    else:
        return None

    aware = parsed.replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=_ZI(tz))
    if aware > now:
        return None
    return aware


def format_record_card(record) -> str:
    """Render a record detail card for Telegram HTML mode."""
    op_label = "Доход" if record.operation == "+" else "Расход"
    sign = "+" if record.operation == "+" else "−"
    amount_str = f"{sign}{float(record.amount):,.0f}₽".replace(",", " ")

    date_str = record.created_at.strftime("%d.%m.%Y")
    category = html.escape(record.category or "не указано")
    account_str = html.escape(record.account.name) if record.account else "—"

    return (
        f"📋 <b>Запись #{record.id}</b>\n\n"
        f"Тип: {op_label}\n"
        f"Сумма: <b>{amount_str}</b>\n"
        f"Категория: {category}\n"
        f"Дата: {date_str}\n"
        f"Счёт: {account_str}"
    )


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
                if (
                    hasattr(message_or_callback, "from_user")
                    and message_or_callback.from_user
                ):
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
