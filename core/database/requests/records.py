"""CRUD for Record: count, list, totals, add, delete, get-by-id, update, duplicate check."""

import logging
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import TIMEZONE
from core.database.models import Account, Record

from ._common import SYSTEM_CATEGORIES, VALID_OPERATIONS, apply_period_filter

_ALLOWED_EDIT_FIELDS = frozenset({"amount", "category", "created_at", "account_id"})


async def count_records(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    """Count user's records filtered by period (paging)."""
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        query = select(func.count(Record.id)).where(
            Record.user_id == user_id,
            Record.category.not_in(SYSTEM_CATEGORIES),
        )
        query = apply_period_filter(query, within, date_from, date_to, now=now)
        result = await session.execute(query)
        return result.scalar() or 0
    except Exception as e:
        logging.exception(f"Ошибка при подсчёте записей пользователя {user_id}: {e}")
        return 0


async def get_records(
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
) -> List[Record]:
    """Fetch user records filtered by period with optional pagination/filters."""
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        conditions = [Record.user_id == user_id]
        if not include_transfers:
            conditions.append(Record.category.not_in(SYSTEM_CATEGORIES))
        if account_id is not None:
            conditions.append(Record.account_id == account_id)
        if operation_filter is not None:
            conditions.append(Record.operation == operation_filter)
        if category_filter is not None:
            conditions.append(Record.category == category_filter)
        query = select(Record).where(*conditions)
        query = apply_period_filter(query, within, date_from, date_to, now=now)
        query = query.order_by(Record.created_at.asc())

        if limit is not None:
            query = query.limit(limit).offset(offset)

        result = await session.execute(query)
        return result.scalars().all()
    except Exception as e:
        logging.exception(f"Ошибка при получении записей пользователя {user_id}: {e}")
        return []


async def get_totals(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> tuple[Decimal, Decimal]:
    """Return (income_sum, expense_sum) for the period as a single query."""
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        query = select(
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(Record.user_id == user_id, Record.category.not_in(SYSTEM_CATEGORIES))

        query = apply_period_filter(query, within, date_from, date_to, now=now)
        result = await session.execute(query)
        row = result.one()

        return Decimal(str(row.income)), Decimal(str(row.expense))
    except Exception as e:
        logging.exception(f"Ошибка при получении сумм пользователя {user_id}: {e}")
        return Decimal("0"), Decimal("0")


async def add_record(
    session: AsyncSession,
    user_id: int,
    operation: str,
    amount: Decimal,
    category: str = "не указано",
    created_at: Optional[datetime] = None,
    account_id: Optional[int] = None,
) -> bool:
    """Add a new income/expense record. Raises ValueError on bad operation."""
    if operation not in VALID_OPERATIONS:
        logging.error(f"Некорректная операция: {operation!r} (ожидается '+' или '-')")
        raise ValueError(f"operation must be '+' or '-', got {operation!r}")

    try:
        record = Record(
            user_id=user_id,
            operation=operation,
            amount=amount,
            category=category,
            account_id=account_id,
        )
        if created_at is not None:
            record.created_at = created_at
        session.add(record)
        return True
    except Exception as e:
        logging.exception(f"Ошибка при добавлении записи для user_id {user_id}: {e}")
        return False


async def delete_record(session: AsyncSession, user_id: int, record_id: int) -> bool:
    """Delete record by id, validating ownership."""
    try:
        result = await session.execute(
            delete(Record).where(Record.id == record_id, Record.user_id == user_id)
        )
        await session.flush()
        return result.rowcount > 0
    except Exception as e:
        await session.rollback()
        logging.exception(
            f"Ошибка при удалении записи {record_id} пользователя {user_id}: {e}"
        )
        return False


async def get_record_by_id(
    session: AsyncSession, record_id: int, user_id: int
) -> Record | None:
    """Return user's record by id with eager-loaded account, or None."""
    return await session.scalar(
        select(Record)
        .options(selectinload(Record.account))
        .where(Record.id == record_id, Record.user_id == user_id)
    )


async def update_record(
    session: AsyncSession, record_id: int, user_id: int, **fields
) -> Record | None:
    """Update allowed fields on a record. Validates ownership and account_id."""
    unknown = set(fields) - _ALLOWED_EDIT_FIELDS
    if unknown:
        logging.error(f"update_record: disallowed fields {unknown}")
        return None

    try:
        record = await session.scalar(
            select(Record).where(Record.id == record_id, Record.user_id == user_id)
        )
        if not record:
            return None

        if "account_id" in fields and fields["account_id"] is not None:
            acc = await session.scalar(
                select(Account).where(
                    Account.id == fields["account_id"],
                    Account.user_id == user_id,
                )
            )
            if not acc:
                return None

        for key, value in fields.items():
            setattr(record, key, value)

        await session.flush()

        result = await session.execute(
            select(Record)
            .options(selectinload(Record.account))
            .where(Record.id == record_id)
        )
        return result.scalar_one_or_none()

    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in update_record for record_id {record_id}: {e}")
        return None


async def check_duplicate_record(
    session: AsyncSession,
    user_id: int,
    record_date: date_type,
    operation: str,
    amount: Decimal,
    category: str,
) -> bool:
    """Check if a record with identical date/operation/amount/category exists."""
    existing = await session.scalar(
        select(Record.id).where(
            Record.user_id == user_id,
            Record.operation == operation,
            Record.amount == amount,
            Record.category == category,
            func.date(Record.created_at) == record_date.isoformat(),
        )
    )
    return existing is not None


async def check_duplicates_batch(
    session: AsyncSession,
    user_id: int,
    rows: List[dict],
) -> set:
    """Batch dup-check for import: one SELECT for the whole date range.

    Each row must have keys: date (date), operation (str), amount (Decimal), category (str).
    Returns set of indices of `rows` that match an existing record.
    """
    if not rows:
        return set()

    dates = [r["date"] for r in rows]
    min_d, max_d = min(dates), max(dates)

    result = await session.execute(
        select(
            func.date(Record.created_at).label("d"),
            Record.operation,
            Record.amount,
            Record.category,
        ).where(
            Record.user_id == user_id,
            func.date(Record.created_at).between(min_d.isoformat(), max_d.isoformat()),
        )
    )
    existing = {
        (str(row.d), row.operation, Decimal(str(row.amount)), row.category)
        for row in result.fetchall()
    }

    duplicates = set()
    for idx, r in enumerate(rows):
        key = (r["date"].isoformat(), r["operation"], Decimal(str(r["amount"])), r["category"])
        if key in existing:
            duplicates.add(idx)
    return duplicates
