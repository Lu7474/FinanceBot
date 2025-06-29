import io
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import matplotlib

matplotlib.use("Agg")  # Для работы matplotlib без GUI
import matplotlib.pyplot as plt
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from core.database.models import Record


# Если категорий больше 7, объединяем остальные в "Прочее"
MAX_CATEGORIES_IN_PIE = 7

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


def parse_date(text: str) -> Optional[datetime]:
    text = text.lower().strip()
    text = text.replace("г.", "").replace("г", "")

    # Форматы: день.месяц.год
    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            if dt > datetime.now():
                return None
            return dt
        except ValueError:
            continue

    # Форматы: день месяц год (на русском)
    for fmt in ("%d %B %Y", "%d %B %y"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.year < 100:
                dt = dt.replace(year=2000 + dt.year)
            if dt > datetime.now():
                return None
            return dt
        except ValueError:
            continue

    return None


def make_report_text(categories: dict, total: float, date: datetime) -> str:
    month_name = RU_MONTHS[date.month]
    lines = [f"📊 Траты за {month_name} {date.year}\n"]

    for name, amount in sorted(categories.items(), key=lambda x: -x[1]):
        lines.append(f"{name} — {amount:,.0f}₽".replace(",", "."))

    lines.append(f"\nИтого: {total:,.0f}₽".replace(",", "."))
    return "\n".join(lines)


async def get_available_years_and_months(session, user_id: int) -> dict[int, list[int]]:
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    current_year = now.year
    current_month = now.month

    stmt = select(
        func.extract("year", Record.created_at).label("year"),
        func.extract("month", Record.created_at).label("month"),
    ).where(Record.user_id == user_id)

    result = await session.execute(stmt)
    rows = result.fetchall()

    if not rows:
        return {}  # Явно возвращаем пустой словарь при отсутствии данных

    data = defaultdict(set)
    for row in rows:
        year = int(row.year)
        month = int(row.month)
        # Пропускаем будущие месяцы
        if year > current_year or (year == current_year and month > current_month):
            continue
        data[year].add(month)

    return {year: sorted(months) for year, months in data.items()}


def build_report_pie(
    categories: dict, total: float, date: datetime
) -> Tuple[Optional[io.BytesIO], str]:
    fig = None  # Инициализация переменной
    if not categories:
        return None, "Нет данных для построения отчета"
    try:
        month_name = RU_MONTHS[date.month]
        fig, ax = plt.subplots(figsize=(4, 4))

        # Сортируем категории по убыванию суммы
        sorted_categories = dict(sorted(categories.items(), key=lambda x: -x[1]))

        # Если категорий больше MAX_CATEGORIES_IN_PIE, объединяем остальные в "Прочее"
        if len(sorted_categories) > MAX_CATEGORIES_IN_PIE:
            other_sum = sum(sorted_categories.values()) - sum(
                list(sorted_categories.values())[:MAX_CATEGORIES_IN_PIE]
            )
            sorted_categories = dict(
                list(sorted_categories.items())[:MAX_CATEGORIES_IN_PIE]
            )
            sorted_categories["Прочее"] = other_sum

        ax.pie(
            sorted_categories.values(),
            labels=sorted_categories.keys(),
            autopct="%1.1f%%",
        )
        ax.set_title(f"Расходы за {month_name} {date.year}")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=300, bbox_inches="tight")
        buf.seek(0)
        caption = make_report_text(categories, total, date)
        return buf, caption
    except Exception:
        return None, "Ошибка при построении отчета"
    finally:
        if fig is not None:
            plt.close(fig)


def make_history_text(records: list[Any]) -> str:
    if not records:
        return "Нет записей за указанный период."

    answer = "🕘 История операций:\n\n"
    sumadd = sum(r.amount for r in records if r.operation == "+")
    sumspent = sum(r.amount for r in records if r.operation == "-")
    remaining = sumadd - sumspent

    for r in records:
        category = f" - {r.category}" if getattr(r, "category", None) else ""
        symbol = "➖" if r.operation == "-" else "➕"
        answer += f"{symbol} {r.amount:,.0f}₽{category} ({r.created_at.strftime('%d.%m.%Y')})\n"

    answer += f"\nСумма доходов: {sumadd:,.0f}₽".replace(",", ".")
    answer += f"\nСумма расходов: {sumspent:,.0f}₽".replace(",", ".")
    answer += f"\nОстаток: {remaining:,.0f}₽".replace(",", ".")
    return answer


def log_exceptions(error_text):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception:
                logging.exception(error_text)
                message_or_callback = args[0]
                state = args[1] if len(args) > 1 else None
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

        return wrapper

    return decorator
