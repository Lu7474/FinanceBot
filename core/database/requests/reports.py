"""Aggregated read queries used by reports & history views."""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import TIMEZONE
from core.database.models import Record, moscow_now
from core.utils import parse_search_query

from ._common import SYSTEM_CATEGORIES, apply_period_filter


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
    records_query = records_query.order_by(Record.created_at.asc())

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
