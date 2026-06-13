"""
Report text generation and DB queries for available periods.
"""

import html
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from config import (
    MAX_CAPTION_LENGTH,
    MAX_CATEGORIES_IN_PIE,
    MAX_MESSAGE_LENGTH,
    TIMEZONE,
)
from core.database.models import Record
from core.utils import RU_MONTHS, RU_MONTHS_SHORT, format_money


def format_budget_status(budgets: list[dict]) -> str:
    """Formats budget list as progress-bar table."""
    if not budgets:
        return "Нет активных бюджетов.\n\nНажмите ➕ Добавить, чтобы установить лимит."

    lines = []
    for b in budgets:
        pct = b["pct"]
        warn = " ⚠️" if pct >= 100 else ""
        lines.append(
            f"<b>{html.escape(b['category'])}</b> · {pct}%{warn}\n"
            f"└ {format_money(float(b['spent']))} / {format_money(float(b['limit']))}"
        )
    return "\n\n".join(lines)


def _compact_amount(v: Decimal) -> str:
    """Compact rouble amount: 28000 -> '28k', 950 -> '950'."""
    n = int(v)
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def format_budget_trend(trend: dict) -> str:
    """Formats per-month fact vs current limit for active budgets (trend view)."""
    rows = trend["rows"]
    months = trend["months"]
    if not rows:
        return "Нет активных бюджетов.\n\nНажмите ➕ Добавить, чтобы установить лимит."

    first, last = months[0], months[-1]
    period = (
        f"{RU_MONTHS_SHORT[first[1]]} {first[0]} — {RU_MONTHS_SHORT[last[1]]} {last[0]}"
    )
    header = f"📈 <b>Тренд бюджетов</b> · {period}"

    cat_lines = []
    for r in rows:
        cat = html.escape(r["category"])
        parts = []
        for (_yr, mo), s in zip(months, r["spent"]):
            warn = "⚠️" if s > r["limit"] else ""
            parts.append(f"{RU_MONTHS_SHORT[mo]} {_compact_amount(s)}{warn}")
        cat_lines.append(
            f"<b>{cat}</b> · лимит {format_money(r['limit'])}\n"
            f"└ {' · '.join(parts)}   (перерасход: {r['over_count']}/{len(months)} мес)"
        )

    total_over = sum(
        (s - r["limit"]) for r in rows for s in r["spent"] if s > r["limit"]
    )
    months_with_breach = sum(
        1 for i in range(len(months)) if any(r["spent"][i] > r["limit"] for r in rows)
    )
    footer = (
        f"<b>Итого:</b> перерасход {format_money(total_over)} · "
        f"пробои в {months_with_breach}/{len(months)} мес"
    )

    # Trim category lines if total exceeds Telegram message limit.
    hidden = 0
    while cat_lines:
        note = (
            f"\n…ещё {hidden} категори{'я' if hidden == 1 else 'и' if 2 <= hidden <= 4 else 'й'}"
            if hidden
            else ""
        )
        text = "\n\n".join([header, *cat_lines]) + note + "\n\n" + footer
        if len(text) <= MAX_MESSAGE_LENGTH:
            return text
        cat_lines.pop()
        hidden += 1
    return header + "\n\n" + footer


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
        filtered = [
            r
            for r in records
            if (r.operation if hasattr(r, "operation") else r["operation"])
            == operation_sign
        ]
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
                lines.append(
                    f"  {short_date} — {operation_sign}{format_money(amount)} {html.escape(category)}"
                )

    lines.append(f"\n💰 <b>Итого:</b> {format_money(total)}")

    result = "\n".join(lines)

    if len(result) > MAX_CAPTION_LENGTH:
        result = result[: MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"

    return result


def format_period_caption(
    categories: Dict[str, Decimal],
    total: Decimal | float,
    period_label: str,
    report_type: str,
) -> str:
    """Caption for quarter/year period switch (Feature 1). No monthly table."""
    title_type = "Доходы" if report_type == "income" else "Расходы"
    icon = "💵" if report_type == "income" else "🛒"

    lines = [f"📊 <b>{title_type}</b> • {period_label}\n", "📁 <b>По категориям:</b>"]
    for name, amount in sorted(categories.items(), key=lambda x: -x[1]):
        lines.append(f"  {icon} {html.escape(name)} — {format_money(amount)}")
    lines.append(f"\n💰 <b>Итого:</b> {format_money(total)}")

    result = "\n".join(lines)
    if len(result) > MAX_CAPTION_LENGTH:
        result = result[: MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"
    return result


def format_stacked_caption(data: list[dict], operation: str) -> str:
    """Short category legend for stacked chart (Feature 2). No monospace table."""
    title_type = "Расходы" if operation == "-" else "Доходы"
    icon = "💵" if operation == "+" else "🛒"

    months = sorted({(d["year"], d["month"]) for d in data})
    if not months:
        return f"📊 <b>Структура: {title_type.lower()}</b>\n\nНет данных за период."

    (y1, m1), (y2, m2) = months[0], months[-1]
    if (y1, m1) == (y2, m2):
        period = f"{RU_MONTHS[m1]} {y1}"
    else:
        period = f"{RU_MONTHS[m1]} {y1} – {RU_MONTHS[m2]} {y2}"

    cat_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for d in data:
        cat_totals[d["category"]] += d["total"]
    grand = sum(cat_totals.values(), Decimal("0"))

    sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])
    top = sorted_cats[:MAX_CATEGORIES_IN_PIE]
    other = sum((v for _, v in sorted_cats[MAX_CATEGORIES_IN_PIE:]), Decimal("0"))

    lines = [
        f"📊 <b>Структура: {title_type.lower()}</b> • {period}\n",
        "📁 <b>По категориям за период:</b>",
    ]
    for name, amount in top:
        lines.append(f"  {icon} {html.escape(name)} — {format_money(amount)}")
    if other > 0:
        lines.append(f"  {icon} Прочее — {format_money(other)}")
    lines.append(f"\n💰 <b>Итого:</b> {format_money(grand)}")

    result = "\n".join(lines)
    if len(result) > MAX_CAPTION_LENGTH:
        result = result[: MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"
    return result


def format_balance_caption(
    daily_data: list[tuple[int, Decimal]],
    year: int,
    month: int,
    income: Decimal,
    expense: Decimal,
) -> str:
    """Caption for the monthly balance line chart.

    income/expense are raw monthly totals (summed separately, not daily net),
    so intra-day expenses aren't swallowed by larger same-day income; итог = net.
    daily_data provides the lowest/highest cumulative balance during the month.
    """
    month_name = RU_MONTHS[month]
    if not daily_data:
        return (
            f"📈 <b>Динамика баланса</b> • {month_name} {year}\n\nНет данных за месяц."
        )

    net = income - expense

    running = Decimal("0")
    low = high = Decimal("0")
    for _, v in sorted(daily_data):
        running += v
        low = min(low, running)
        high = max(high, running)

    sign = "🟢" if net >= 0 else "🔴"
    lines = [
        f"📈 <b>Динамика баланса</b> • {month_name} {year}\n",
        f"  💵 Доходы — {format_money(income)}",
        f"  🛒 Расходы — {format_money(expense)}",
        f"  {sign} <b>Итог за месяц:</b> {format_money(net)}",
        f"\n  📈 Максимум: {format_money(high)}",
        f"  📉 Минимум: {format_money(low)}",
    ]

    result = "\n".join(lines)
    if len(result) > MAX_CAPTION_LENGTH:
        result = result[: MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"
    return result


def format_yearly_report(data: list[dict], year: Optional[int], operation: str) -> str:
    """Text for the yearly report.

    Specific year → per-month totals (aligned <pre> table) + per-year category block.
    year=None (all time) → per-year totals + all-time category block.
    Caller decides caption vs separate message based on length.
    """
    type_gen = "доходов" if operation == "+" else "расходов"
    icon = "💵" if operation == "+" else "🛒"
    subtitle = str(year) if year is not None else "за всё время"
    lines = [f"📅 <b>Годовой отчёт {type_gen} — {subtitle}</b>"]

    # Per-bucket totals: by month for a single year, by year for all time.
    bucket: Dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    if year is not None:
        for d in data:
            bucket[d["month"]] += d["total"]
        keys = sorted(bucket)
        labels = [RU_MONTHS[m][:3] for m in keys]
        avg_label = "Среднее/мес"
        # Divide by elapsed calendar months (12 for a past year, current month
        # for the ongoing one) — empty months still count, but don't dilute by
        # the unfinished part of the current year.
        now = datetime.now(ZoneInfo(TIMEZONE))
        divisor = 12 if year < now.year else max(now.month, 1)
    else:
        for d in data:
            bucket[d["year"]] += d["total"]
        keys = sorted(bucket)
        labels = [str(k) for k in keys]
        avg_label = "Среднее/год"
        divisor = len(keys) or 1

    grand = sum(bucket.values(), Decimal("0"))

    money_strs = [format_money(bucket[k]) for k in keys]
    width = max((len(s) for s in money_strs), default=0)
    table = "\n".join(f"{lab}  {ms:>{width}}" for lab, ms in zip(labels, money_strs))
    if table:
        lines.append("<pre>" + html.escape(table) + "</pre>")

    # Category breakdown across the whole selected period.
    cat_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for d in data:
        cat_totals[d["category"]] += d["total"]
    sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])

    lines.append("\n📁 <b>По категориям:</b>")
    cap = MAX_CATEGORIES_IN_PIE * 3  # keep text bounded, fold the long tail
    for name, amount in sorted_cats[:cap]:
        lines.append(f"  {icon} {html.escape(name)} — {format_money(amount)}")
    tail = sum((v for _, v in sorted_cats[cap:]), Decimal("0"))
    if tail > 0:
        lines.append(f"  {icon} Прочее — {format_money(tail)}")

    avg = grand / divisor if divisor else Decimal("0")
    lines.append(
        f"\n💰 <b>Итого:</b> {format_money(grand)}  |  {avg_label}: {format_money(avg)}"
    )

    result = "\n".join(lines)
    if len(result) > MAX_MESSAGE_LENGTH:
        result = result[: MAX_MESSAGE_LENGTH - 20] + "\n\n... (обрезано)"
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
            lines.append(
                f"   {color} {html.escape(cat)}: {format_money(prev_val)} → {format_money(cur_val)} ({sign}{format_money(cat_diff)})"
            )

    if avg_monthly:
        lines.append(f"\n📈 <b>Средний за период:</b> {format_money(avg_monthly)}/мес")

    result = "\n".join(lines)

    if len(result) > MAX_CAPTION_LENGTH:
        result = result[: MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"

    return result
