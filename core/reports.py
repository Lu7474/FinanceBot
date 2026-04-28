"""
Report text generation and DB queries for available periods.
"""
import html
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from zoneinfo import ZoneInfo

from config import MAX_CAPTION_LENGTH, TIMEZONE
from core.database.models import Record
from core.utils import RU_MONTHS, format_money


def make_report_text(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    date: datetime,
    report_type: str,
    records: Optional[List[Any]] = None,
) -> str:
    month_name = RU_MONTHS[date.month]
    title_type = "Доходы" if report_type == "income" else "Расходы"
    icon = "💵" if report_type == "income" else "🛒"
    operation_sign = "+" if report_type == "income" else "-"

    lines = [f"📊 <b>{title_type}</b> • {month_name} {date.year}\n"]

    lines.append("📁 <b>По категориям:</b>")
    for name, amount in sorted(categories.items(), key=lambda x: -x[1]):
        lines.append(f"  {icon} {html.escape(name)} — {format_money(amount)}")

    if records:
        filtered = [r for r in records if (r.operation if hasattr(r, "operation") else r["operation"]) == operation_sign]
        if filtered:
            lines.append("\n📅 <b>По датам:</b>")
            for r in filtered:
                if hasattr(r, "amount"):
                    amount = r.amount
                    category = r.category
                    rec_date = r.created_at
                else:
                    amount = r["amount"]
                    category = r["category"]
                    rec_date = r["created_at"]
                short_date = rec_date.strftime("%d.%m")
                lines.append(f"  {short_date} — {operation_sign}{format_money(amount)} {html.escape(category)}")

    lines.append(f"\n💰 <b>Итого:</b> {format_money(total)}")

    result = "\n".join(lines)

    if len(result) > MAX_CAPTION_LENGTH:
        result = result[:MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"

    return result


async def get_available_years_and_months(
    session: Any, user_id: int, operation: Optional[str] = None
) -> Dict[int, List[int]]:
    now = datetime.now(ZoneInfo(TIMEZONE))
    current_year = now.year
    current_month = now.month

    stmt = select(
        func.extract("year", Record.created_at).label("year"),
        func.extract("month", Record.created_at).label("month"),
    ).where(Record.user_id == user_id)

    if operation is not None:
        stmt = stmt.where(Record.operation == operation)

    stmt = stmt.distinct()

    result = await session.execute(stmt)
    rows = result.fetchall()

    if not rows:
        return {}

    data = defaultdict(set)
    for row in rows:
        year = int(row.year)
        month = int(row.month)
        if year > current_year or (year == current_year and month > current_month):
            continue
        data[year].add(month)

    return {year: sorted(months) for year, months in data.items()}


def make_comparison_text(
    current_categories: Dict[str, Decimal],
    prev_categories: Dict[str, Decimal],
    current_total: Decimal,
    prev_total: Decimal,
    current_month: Tuple[int, int],
    prev_month: Tuple[int, int],
    report_type: str,
    avg_monthly: Optional[Decimal] = None,
) -> str:
    """Формирует текст сравнения двух месяцев."""
    icon = "💵" if report_type == "income" else "🛒"

    cur_name = f"{RU_MONTHS[current_month[1]]} {current_month[0]}"
    prev_name = f"{RU_MONTHS[prev_month[1]]} {prev_month[0]}"

    diff = current_total - prev_total
    if prev_total > 0:
        diff_pct = (diff / prev_total) * 100
        pct_str = f" ({diff_pct:+.0f}%)"
    else:
        pct_str = ""

    if diff > 0:
        diff_icon = "📈"
        diff_sign = "+"
    elif diff < 0:
        diff_icon = "📉"
        diff_sign = ""
    else:
        diff_icon = "➡️"
        diff_sign = ""

    lines = [
        f"📊 <b>Сравнение: {cur_name} vs {prev_name}</b>\n",
        "💰 <b>Итого:</b>",
        f"   {cur_name}: {format_money(current_total)}",
        f"   {prev_name}: {format_money(prev_total)}",
        f"   Разница: {diff_sign}{format_money(abs(diff))}{pct_str} {diff_icon}\n",
    ]

    all_categories = set(current_categories.keys()) | set(prev_categories.keys())
    changes = []
    for cat in all_categories:
        cur_val = current_categories.get(cat, Decimal(0))
        prev_val = prev_categories.get(cat, Decimal(0))
        cat_diff = cur_val - prev_val
        if cat_diff != 0:
            changes.append((cat, cur_val, prev_val, cat_diff))

    changes.sort(key=lambda x: abs(x[3]), reverse=True)

    if changes:
        lines.append(f"{icon} <b>По категориям:</b>")
        is_income = report_type == "income"
        for cat, cur_val, prev_val, cat_diff in changes[:5]:
            if cat_diff > 0:
                color = "🟢" if is_income else "🔴"
                sign = "+"
            else:
                color = "🔴" if is_income else "🟢"
                sign = ""
            lines.append(f"   {color} {html.escape(cat)}: {format_money(prev_val)} → {format_money(cur_val)} ({sign}{format_money(cat_diff)})")

    if avg_monthly:
        lines.append(f"\n📈 <b>Средний за период:</b> {format_money(avg_monthly)}/мес")

    result = "\n".join(lines)

    if len(result) > MAX_CAPTION_LENGTH:
        result = result[:MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"

    return result
