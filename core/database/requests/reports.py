"""Aggregated read queries used by reports & history views."""

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import TIMEZONE
from core.database.models import Goal, GoalDeposit, Payment, Record, moscow_now
from core.utils import parse_search_query, strip_search_needle, today_msk

from ._common import SYSTEM_CATEGORIES, apply_period_filter
from .accounts import get_account_balances


async def get_categories_summary(
    session: AsyncSession,
    user_id: int | list[int],
    operation: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Decimal]:
    """Sum-by-category via SQL GROUP BY. Returns {category: total}.

    user_id accepts a single id (personal) or a list (family scope).
    """
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    query = (
        select(
            Record.category,
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.user_id.in_(user_ids),
            Record.operation == operation,
            Record.category.not_in(SYSTEM_CATEGORIES),
        )
        .group_by(Record.category)
    )

    if date_from and date_to:
        # Record.created_at is TIMESTAMP WITHOUT TIME ZONE — asyncpg rejects
        # tz-aware datetimes here. Strip tzinfo (callers pass Moscow wall-clock).
        if date_from.tzinfo is not None:
            date_from = date_from.replace(tzinfo=None)
        if date_to.tzinfo is not None:
            date_to = date_to.replace(tzinfo=None)
        query = query.where(Record.created_at.between(date_from, date_to))

    result = await session.execute(query)
    rows = result.fetchall()

    return {(row.category or "Без категории"): Decimal(str(row.total)) for row in rows}


async def get_history_data(
    session: AsyncSession,
    user_id: int | list[int],
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    account_id: Optional[int] = None,
    include_transfers: bool = False,
    operation_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    newest_first: bool = False,
) -> tuple[int, Decimal, Decimal, List[Record]]:
    """Single call returning (total_count, income_sum, expense_sum, records).

    user_id accepts a single id (personal) or a list (family scope).
    """
    now = datetime.now(ZoneInfo(TIMEZONE))

    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    base_conditions: list[ColumnElement[bool]] = [Record.user_id.in_(user_ids)]
    if not include_transfers:
        base_conditions.append(Record.category.not_in(SYSTEM_CATEGORIES))
    if account_id is not None:
        base_conditions.append(Record.account_id == account_id)
    if operation_filter is not None:
        base_conditions.append(Record.operation == operation_filter)
    if category_filter is not None:
        base_conditions.append(Record.category == category_filter)

    count_totals_query = select(
        func.count(Record.id).label("cnt"),
        func.coalesce(
            func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
        ).label("income"),
        func.coalesce(
            func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
        ).label("expense"),
    ).where(*base_conditions)
    count_totals_query = apply_period_filter(
        count_totals_query, within, date_from, date_to, now=now
    )

    result = await session.execute(count_totals_query)
    row = result.one()
    total_count = row.cnt
    income_sum = Decimal(str(row.income))
    expense_sum = Decimal(str(row.expense))

    records_query = select(Record).where(*base_conditions)
    records_query = apply_period_filter(
        records_query, within, date_from, date_to, now=now
    )
    order_col = Record.created_at.desc() if newest_first else Record.created_at.asc()
    records_query = records_query.order_by(order_col)

    if limit is not None:
        records_query = records_query.limit(limit).offset(offset)

    records_result = await session.execute(records_query)
    records = records_result.scalars().all()

    return total_count, income_sum, expense_sum, records


async def search_records(
    session: AsyncSession,
    user_id: int | list[int],
    query_str: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[int, Decimal, Decimal, List[Record]]:
    """Parses query_str, builds WHERE, returns (total_count, income_sum, expense_sum, records).

    user_id accepts a single id (personal) or a list (family scope).
    """
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    parsed = parse_search_query(query_str)
    conditions: list[ColumnElement[bool]] = [
        Record.user_id.in_(user_ids),
        Record.category.not_in(SYSTEM_CATEGORIES),
    ]
    if parsed.get("operation") in {"+", "-"}:
        conditions.append(Record.operation == parsed["operation"])
    if parsed["type"] == "gt":
        conditions.append(Record.amount > parsed["value"])
    elif parsed["type"] == "lt":
        conditions.append(Record.amount < parsed["value"])
    elif parsed["type"] == "eq":
        conditions.append(Record.amount == parsed["value"])
    elif parsed["type"] == "text" and parsed["value"]:
        needle = parsed["value"].lower()
        conditions.append(
            or_(
                func.lower(Record.category).contains(needle, autoescape=True),
                func.lower(Record.description).contains(needle, autoescape=True),
            )
        )

    count_sum_q = select(
        func.count(Record.id).label("cnt"),
        func.coalesce(
            func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
        ).label("income"),
        func.coalesce(
            func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
        ).label("expense"),
    ).where(*conditions)
    row = (await session.execute(count_sum_q)).one()
    total = row.cnt or 0
    income_sum = Decimal(str(row.income))
    expense_sum = Decimal(str(row.expense))

    records_q = select(Record).where(*conditions).order_by(Record.created_at.desc())
    if limit is not None:
        records_q = records_q.limit(limit).offset(offset)

    result = await session.execute(records_q)
    return total, income_sum, expense_sum, list(result.scalars().all())


async def aggregate_search_by_description(
    session: AsyncSession,
    user_id: int | list[int],
    query_str: str,
) -> list[dict]:
    """Groups ALL records matching the search query by description.

    Description is first stripped of the search term (e.g. "газ solaris" → "газ"),
    then normalized (case/space-insensitive) for grouping. Empty result → label
    "(без описания)". Returns list of {label, expense, income, count} sorted by
    expense desc. user_id accepts a single id or a list (family scope).
    """
    _, _, _, records = await search_records(session, user_id, query_str, limit=None)

    groups: dict[str, dict] = {}
    for r in records:
        cleaned = strip_search_needle(r.description or "", query_str)
        key = cleaned.casefold()
        g = groups.get(key)
        if g is None:
            g = {
                "label": cleaned or "(без описания)",
                "expense": Decimal("0"),
                "income": Decimal("0"),
                "count": 0,
            }
            groups[key] = g
        if r.operation == "-":
            g["expense"] += r.amount
        else:
            g["income"] += r.amount
        g["count"] += 1

    return sorted(groups.values(), key=lambda g: g["expense"], reverse=True)


async def get_top_categories_for_period(
    session: AsyncSession,
    user_id: int | list[int],
    within: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 15,
    operation_filter: Optional[str] = None,
) -> list[str]:
    """Returns top N categories by frequency for given period.

    user_id accepts a single id (personal) or a list (family scope).
    """
    now = datetime.now(ZoneInfo(TIMEZONE))
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    conditions: list[ColumnElement[bool]] = [
        Record.user_id.in_(user_ids),
        Record.category.not_in(SYSTEM_CATEGORIES),
    ]
    if operation_filter is not None:
        conditions.append(Record.operation == operation_filter)
    query = (
        select(Record.category, func.count(Record.id).label("cnt"))
        .where(*conditions)
        .group_by(Record.category)
        .order_by(func.count(Record.id).desc())
        .limit(limit)
    )
    query = apply_period_filter(query, within, date_from, date_to, now=now)
    result = await session.execute(query)
    return [row.category for row in result.fetchall()]


async def get_monthly_totals(
    session: AsyncSession,
    user_id: int | list[int],
    operation: str,
    months_back: int = 12,
) -> list[tuple[int, int, Decimal]]:
    """Sums grouped by (year, month) for the last N months. For trend charts.

    user_id accepts a single id (personal) or a list (family scope).
    """
    now = moscow_now()
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    start_date = (now - relativedelta(months=months_back - 1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    query = (
        select(
            func.extract("year", Record.created_at).label("year"),
            func.extract("month", Record.created_at).label("month"),
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.user_id.in_(user_ids),
            Record.operation == operation,
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= start_date,
        )
        .group_by(
            func.extract("year", Record.created_at),
            func.extract("month", Record.created_at),
        )
        .order_by(
            func.extract("year", Record.created_at),
            func.extract("month", Record.created_at),
        )
    )

    result = await session.execute(query)
    rows = result.fetchall()

    return [(int(row.year), int(row.month), Decimal(str(row.total))) for row in rows]


async def get_daily_balance_for_month(
    session: AsyncSession,
    user_id: int | list[int],
    year: int,
    month: int,
) -> list[tuple[int, Decimal]]:
    """Daily net (income − expense) for a single month. For the balance line chart.

    Returns [(day, net), ...] ordered by day — only days that had operations.
    SYSTEM_CATEGORIES excluded (transfers between own accounts don't change net).
    user_id accepts a single id (personal) or a list (family scope).
    """
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    date_from = datetime(year, month, 1)
    if month == 12:
        date_to = datetime(year + 1, 1, 1)
    else:
        date_to = datetime(year, month + 1, 1)

    net_expr = func.sum(
        case((Record.operation == "+", Record.amount), else_=-Record.amount)
    ).label("net")

    query = (
        select(
            func.extract("day", Record.created_at).label("day"),
            net_expr,
        )
        .where(
            Record.user_id.in_(user_ids),
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= date_from,
            Record.created_at < date_to,
        )
        .group_by(func.extract("day", Record.created_at))
        .order_by(func.extract("day", Record.created_at))
    )

    result = await session.execute(query)
    rows = result.fetchall()

    return [(int(row.day), Decimal(str(row.net))) for row in rows]


async def get_month_income_expense(
    session: AsyncSession,
    user_id: int | list[int],
    year: int,
    month: int,
) -> tuple[Decimal, Decimal]:
    """Raw income/expense totals for a single month. For the balance chart caption.

    Sums positive and negative operations separately (NOT daily net), so
    intra-day expenses are not swallowed by larger same-day income.
    SYSTEM_CATEGORIES excluded; user_id accepts a single id or a list.
    """
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    date_from = datetime(year, month, 1)
    if month == 12:
        date_to = datetime(year + 1, 1, 1)
    else:
        date_to = datetime(year, month + 1, 1)

    income_expr = func.sum(
        case((Record.operation == "+", Record.amount), else_=0)
    ).label("income")
    expense_expr = func.sum(
        case((Record.operation == "-", Record.amount), else_=0)
    ).label("expense")

    query = select(income_expr, expense_expr).where(
        Record.user_id.in_(user_ids),
        Record.category.not_in(SYSTEM_CATEGORIES),
        Record.created_at >= date_from,
        Record.created_at < date_to,
    )
    row = (await session.execute(query)).one()
    income = Decimal(str(row.income)) if row.income is not None else Decimal("0")
    expense = Decimal(str(row.expense)) if row.expense is not None else Decimal("0")
    return income, expense


async def get_stacked_data(
    session: AsyncSession,
    user_id: int | list[int],
    operation: str,
    months_count: int,
) -> list[dict]:
    """Sums grouped by (year, month, category) for last N months. For stacked bar charts.

    Returns [{year, month, category, total}, ...] ordered by (year, month).
    Uses func.extract (works on both SQLite and Postgres) — no strftime.
    user_id accepts a single id (personal) or a list (family scope).
    """
    now = moscow_now()
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    start_date = (now - relativedelta(months=months_count - 1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    query = (
        select(
            func.extract("year", Record.created_at).label("year"),
            func.extract("month", Record.created_at).label("month"),
            Record.category,
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.user_id.in_(user_ids),
            Record.operation == operation,
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= start_date,
        )
        .group_by(
            func.extract("year", Record.created_at),
            func.extract("month", Record.created_at),
            Record.category,
        )
        .order_by(
            func.extract("year", Record.created_at),
            func.extract("month", Record.created_at),
        )
    )

    result = await session.execute(query)
    rows = result.fetchall()

    return [
        {
            "year": int(row.year),
            "month": int(row.month),
            "category": row.category or "Без категории",
            "total": Decimal(str(row.total)),
        }
        for row in rows
    ]


async def get_yearly_report(
    session: AsyncSession,
    user_id: int | list[int],
    operation: str,
    year: Optional[int] = None,
    categories: Optional[List[str]] = None,
) -> list[dict]:
    """Sums grouped by (year, month, category) for the yearly report.

    year=None → all time (kept split by year AND month, never merged).
    categories=None/empty → all categories. SYSTEM_CATEGORIES excluded.
    func.extract used (SQLite casts to INTEGER) — Postgres-safe, no strftime.
    Returns [{year, month, category, total}] ordered by (year, month).
    user_id accepts a single id (personal) or a list (family scope).
    """
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    conditions = [
        Record.user_id.in_(user_ids),
        Record.operation == operation,
        Record.category.not_in(SYSTEM_CATEGORIES),
    ]
    if year is not None:
        conditions.append(func.extract("year", Record.created_at) == year)
    if categories:
        conditions.append(Record.category.in_(categories))

    query = (
        select(
            func.extract("year", Record.created_at).label("year"),
            func.extract("month", Record.created_at).label("month"),
            Record.category,
            func.sum(Record.amount).label("total"),
        )
        .where(*conditions)
        .group_by(
            func.extract("year", Record.created_at),
            func.extract("month", Record.created_at),
            Record.category,
        )
        .order_by(
            func.extract("year", Record.created_at),
            func.extract("month", Record.created_at),
        )
    )

    rows = (await session.execute(query)).fetchall()
    return [
        {
            "year": int(row.year),
            "month": int(row.month),
            "category": row.category or "Без категории",
            "total": Decimal(str(row.total)),
        }
        for row in rows
    ]


@dataclass
class FreeToSpend:
    """«Свободные деньги» — сводка трёх источников по личному scope."""

    free: Decimal  # итог: total_balance − earmark − upcoming_payments (не клампится)
    total_balance: Decimal  # Σ балансов счетов (уже за вычетом привязанных депозитов)
    earmark: Decimal  # непривязанный earmark активных целей, >= 0
    upcoming_payments: Decimal  # сумма активных платежей до конца текущего месяца
    payments_no_amount: int  # счётчик платежей с плавающей суммой (amount IS NULL)


async def get_free_to_spend(
    session: AsyncSession,
    user_id: int,
    balances: Optional[List[tuple]] = None,
) -> FreeToSpend:
    """Сколько пользователь может потратить = баланс − отложенное в цели − платежи.

    Личный scope (только свои счета/взносы/платежи). Привязанные к счёту депозиты
    в цели уже уменьшили balance_offset, поэтому из earmark берём ТОЛЬКО
    непривязанную часть (account_id IS NULL) — иначе двойной счёт.

    balances — уже посчитанные [(Account, balance), ...] для переиспользования
    (экран «Мои счета»); если None — считаем сами.
    """
    if balances is None:
        balances = await get_account_balances(session, user_id)
    total_balance = sum((balance for _, balance in balances), Decimal("0"))

    # Непривязанный net-earmark активных целей; net < 0 клампим в 0.
    earmark_raw = await session.scalar(
        select(func.coalesce(func.sum(GoalDeposit.amount), 0))
        .select_from(GoalDeposit)
        .join(Goal, Goal.id == GoalDeposit.goal_id)
        .where(
            GoalDeposit.user_id == user_id,
            GoalDeposit.account_id.is_(None),
            Goal.is_completed.is_(False),
        )
    )
    earmark = Decimal(str(earmark_raw or 0))
    if earmark < 0:
        earmark = Decimal("0")

    # Активные платежи с due_date до конца текущего месяца (включая просроченные).
    today = today_msk()
    last_day = calendar.monthrange(today.year, today.month)[1]
    month_end = date(today.year, today.month, last_day)
    rows = await session.execute(
        select(Payment.amount).where(
            Payment.user_id == user_id,
            Payment.is_active.is_(True),
            Payment.due_date <= month_end,
        )
    )
    upcoming = Decimal("0")
    no_amount = 0
    for (amount,) in rows:
        if amount is None:
            no_amount += 1
        else:
            upcoming += Decimal(str(amount))

    free = total_balance - earmark - upcoming
    return FreeToSpend(
        free=free,
        total_balance=total_balance,
        earmark=earmark,
        upcoming_payments=upcoming,
        payments_no_amount=no_amount,
    )


async def get_categories_for_year(
    session: AsyncSession,
    user_id: int | list[int],
    operation: str,
    year: Optional[int] = None,
) -> list[str]:
    """Distinct categories for a year (or all time), ordered by total desc.

    user_id accepts a single id (personal) or a list (family scope).
    """
    user_ids = [user_id] if isinstance(user_id, int) else list(user_id)
    conditions = [
        Record.user_id.in_(user_ids),
        Record.operation == operation,
        Record.category.not_in(SYSTEM_CATEGORIES),
    ]
    if year is not None:
        conditions.append(func.extract("year", Record.created_at) == year)

    query = (
        select(Record.category)
        .where(*conditions)
        .group_by(Record.category)
        .order_by(func.sum(Record.amount).desc())
    )
    rows = (await session.execute(query)).fetchall()
    return [row.category or "Без категории" for row in rows]
