"""Aggregated read queries used by reports & history views."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import TIMEZONE
from core.database.models import Record
from core.database.requests._common import now_moscow
from core.utils import parse_search_query

from ._common import SYSTEM_CATEGORIES, apply_period_filter


async def get_categories_summary(
    session: AsyncSession,
    user_id: int,
    operation: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Decimal]:
    """Sum-by-category via SQL GROUP BY. Returns {category: total}."""
    try:
        query = (
            select(
                Record.category,
                func.sum(Record.amount).label("total"),
            )
            .where(
                Record.user_id == user_id,
                Record.operation == operation,
                Record.category.not_in(SYSTEM_CATEGORIES),
            )
            .group_by(Record.category)
        )

        if date_from and date_to:
            query = query.where(Record.created_at.between(date_from, date_to))

        result = await session.execute(query)
        rows = result.fetchall()

        return {
            (row.category or "Без категории"): Decimal(str(row.total)) for row in rows
        }
    except Exception as e:
        logging.exception(
            f"Ошибка при получении сумм по категориям для user_id {user_id}: {e}"
        )
        return {}


async def get_history_data(
    session: AsyncSession,
    user_id: int,
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
    """Single call returning (total_count, income_sum, expense_sum, records)."""
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))

        base_conditions = [Record.user_id == user_id]
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
    except Exception as e:
        logging.exception(
            f"Ошибка при получении данных истории для user_id {user_id}: {e}"
        )
        return 0, Decimal("0"), Decimal("0"), []


async def search_records(
    session: AsyncSession,
    user_id: int,
    query_str: str,
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[int, Decimal, Decimal, List[Record]]:
    """Parses query_str, builds WHERE, returns (total_count, income_sum, expense_sum, records)."""
    try:
        parsed = parse_search_query(query_str)
        conditions = [
            Record.user_id == user_id,
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
            records_q = (
                select(Record).where(*conditions).order_by(Record.created_at.desc())
            )
            result = await session.execute(records_q)
            needle = parsed["value"].casefold()
            matched = [
                record
                for record in result.scalars().all()
                if needle in (record.category or "").casefold()
            ]
            total = len(matched)
            income_sum = sum(
                (record.amount for record in matched if record.operation == "+"),
                Decimal("0"),
            )
            expense_sum = sum(
                (record.amount for record in matched if record.operation == "-"),
                Decimal("0"),
            )
            if limit is not None:
                matched = matched[offset : offset + limit]
            return total, income_sum, expense_sum, matched

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
    except Exception as e:
        logging.exception(f"Ошибка при поиске записей для user_id {user_id}: {e}")
        return 0, Decimal("0"), Decimal("0"), []


async def get_top_categories_for_period(
    session: AsyncSession,
    user_id: int,
    within: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 15,
    operation_filter: Optional[str] = None,
) -> list[str]:
    """Returns top N categories by frequency for given period."""
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        conditions = [
            Record.user_id == user_id,
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
    except Exception as e:
        logging.exception(
            f"Ошибка при получении топ категорий для user_id {user_id}: {e}"
        )
        return []


async def get_monthly_totals(
    session: AsyncSession,
    user_id: int,
    operation: str,
    months_back: int = 12,
) -> list[tuple[int, int, Decimal]]:
    """Sums grouped by (year, month) for the last N months. For trend charts."""
    try:
        now = now_moscow()
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
                Record.user_id == user_id,
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

        return [
            (int(row.year), int(row.month), Decimal(str(row.total))) for row in rows
        ]
    except Exception as e:
        logging.exception(
            f"Ошибка при получении месячных сумм для user_id {user_id}: {e}"
        )
        return []


async def get_weekday_report(
    session: AsyncSession,
    user_id: int,
    operation: str,
    date_from: datetime,
    date_to: datetime,
) -> dict[int, Decimal]:
    """Returns {weekday: total} where weekday 0=Mon..6=Sun. Days with no records = Decimal('0')."""
    result = await session.execute(
        select(
            func.strftime("%w", Record.created_at).label("sqlite_wd"),
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.user_id == user_id,
            Record.operation == operation,
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at.between(date_from, date_to),
        )
        .group_by(func.strftime("%w", Record.created_at))
    )
    rows = result.fetchall()

    # SQLite strftime('%w'): 0=Sun..6=Sat → convert to 0=Mon..6=Sun
    data: dict[int, Decimal] = {i: Decimal("0") for i in range(7)}
    for row in rows:
        sqlite_wd = int(row.sqlite_wd)
        mon_wd = (sqlite_wd - 1) % 7
        data[mon_wd] = Decimal(str(row.total))
    return data
