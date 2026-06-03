"""Export / Import / Backup queries."""

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database.models import Budget, Record, SavingsSnapshot, WealthItem


async def get_all_records_for_export(
    session: AsyncSession,
    user_id: int,
    operation: str | None = None,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
) -> list[Record]:
    """Fetch records for export/backup with optional filters.

    Uses selectinload(Record.account).
    """
    query = (
        select(Record)
        .options(selectinload(Record.account))
        .where(Record.user_id == user_id)
    )
    if operation is not None:
        query = query.where(Record.operation == operation)
    if date_from is not None:
        dt_from = datetime.combine(date_from, datetime.min.time())
        query = query.where(Record.created_at >= dt_from)
    if date_to is not None:
        dt_to = datetime.combine(date_to, datetime.max.time())
        query = query.where(Record.created_at <= dt_to)
    query = query.order_by(Record.created_at)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_all_budgets_for_backup(
    session: AsyncSession, user_id: int
) -> list[Budget]:
    """Fetch all active budgets for backup."""
    result = await session.execute(
        select(Budget).where(Budget.user_id == user_id, Budget.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_latest_snapshot_for_backup(
    session: AsyncSession, user_id: int
) -> SavingsSnapshot | None:
    """Fetch latest savings snapshot with items for backup."""
    result = await session.execute(
        select(SavingsSnapshot)
        .options(selectinload(SavingsSnapshot.items))
        .where(SavingsSnapshot.user_id == user_id)
        .order_by(SavingsSnapshot.date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_wealth_items_for_backup(
    session: AsyncSession, user_id: int
) -> list[WealthItem]:
    """Fetch all wealth items for backup."""
    result = await session.execute(
        select(WealthItem).where(WealthItem.user_id == user_id)
    )
    return list(result.scalars().all())


async def bulk_insert_records(
    session: AsyncSession,
    user_id: int,
    rows: list[dict],
) -> int:
    """Insert records in bulk. Returns count of inserted records.

    Each row: {date, operation, amount, category, account_id, description}
    """
    count = 0
    for row in rows:
        d = row["date"]
        created_at = datetime.combine(d, datetime.min.time())
        session.add(
            Record(
                user_id=user_id,
                operation=row["operation"],
                amount=row["amount"],
                category=row["category"],
                account_id=row.get("account_id"),
                created_at=created_at,
                description=row.get("description"),
            )
        )
        count += 1
    await session.flush()
    return count
