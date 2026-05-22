"""Capital tracking: SavingsSnapshot/SavingsItem + WealthItem (assets/liabilities)."""

import logging
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database.models import (
    SavingsItem,
    SavingsSnapshot,
    WealthItem,
)


async def get_snapshots_dates(session: AsyncSession, user_id: int) -> list[date_type]:
    """Returns all dates with savings snapshots for user, sorted ascending."""
    result = await session.execute(
        select(SavingsSnapshot.date)
        .where(SavingsSnapshot.user_id == user_id)
        .order_by(SavingsSnapshot.date.asc())
    )
    return list(result.scalars().all())


async def get_snapshot(
    session: AsyncSession, user_id: int, snapshot_date: date_type
) -> SavingsSnapshot | None:
    """Returns snapshot with eagerly loaded items for the given date."""
    result = await session.execute(
        select(SavingsSnapshot)
        .options(selectinload(SavingsSnapshot.items))
        .where(
            SavingsSnapshot.user_id == user_id, SavingsSnapshot.date == snapshot_date
        )
    )
    return result.scalar_one_or_none()


async def get_snapshot_by_id(
    session: AsyncSession, snapshot_id: int, user_id: int
) -> SavingsSnapshot | None:
    """Returns snapshot by id with items (validates ownership)."""
    result = await session.execute(
        select(SavingsSnapshot)
        .options(selectinload(SavingsSnapshot.items))
        .where(SavingsSnapshot.id == snapshot_id, SavingsSnapshot.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_latest_snapshot(
    session: AsyncSession, user_id: int
) -> SavingsSnapshot | None:
    """Returns the most recent snapshot with items."""
    result = await session.execute(
        select(SavingsSnapshot)
        .options(selectinload(SavingsSnapshot.items))
        .where(SavingsSnapshot.user_id == user_id)
        .order_by(SavingsSnapshot.date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_snapshot(
    session: AsyncSession,
    user_id: int,
    snapshot_date: date_type,
    items: list[tuple[str, Decimal]],
) -> SavingsSnapshot | None:
    """Creates or replaces snapshot for the given date. items = [(name, amount), ...]."""
    try:
        result = await session.execute(
            select(SavingsSnapshot).where(
                SavingsSnapshot.user_id == user_id,
                SavingsSnapshot.date == snapshot_date,
            )
        )
        snapshot = result.scalar_one_or_none()
        if snapshot:
            await session.execute(
                delete(SavingsItem).where(SavingsItem.snapshot_id == snapshot.id)
            )
        else:
            snapshot = SavingsSnapshot(user_id=user_id, date=snapshot_date)
            session.add(snapshot)
            await session.flush()

        for name, amount in items:
            session.add(SavingsItem(snapshot_id=snapshot.id, name=name, amount=amount))
        await session.flush()
        return snapshot
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in upsert_snapshot for user_id {user_id}: {e}")
        return None


async def add_snapshot_item(
    session: AsyncSession,
    snapshot_id: int,
    user_id: int,
    name: str,
    amount: Decimal,
) -> SavingsItem | None:
    """Adds a new item to an existing snapshot (validates ownership)."""
    try:
        snap = await session.get(SavingsSnapshot, snapshot_id)
        if not snap or snap.user_id != user_id:
            return None
        item = SavingsItem(snapshot_id=snapshot_id, name=name, amount=amount)
        session.add(item)
        await session.flush()
        return item
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in add_snapshot_item: {e}")
        return None


async def update_snapshot_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
    amount: Decimal,
) -> bool:
    """Updates amount for a savings item (validates ownership via snapshot join)."""
    try:
        result = await session.execute(
            select(SavingsItem)
            .join(SavingsSnapshot)
            .where(SavingsItem.id == item_id, SavingsSnapshot.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return False
        item.amount = amount
        await session.flush()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in update_snapshot_item: {e}")
        return False


async def delete_snapshot_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
) -> date_type | None:
    """Deletes a savings item. Returns snapshot date on success, None on failure."""
    try:
        result = await session.execute(
            select(SavingsItem)
            .join(SavingsSnapshot)
            .where(SavingsItem.id == item_id, SavingsSnapshot.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return None
        snap = await session.get(SavingsSnapshot, item.snapshot_id)
        assert snap is not None
        snap_date = snap.date
        await session.delete(item)
        await session.flush()
        session.expire(snap, ["items"])
        return snap_date
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in delete_snapshot_item: {e}")
        return None


async def delete_snapshot(
    session: AsyncSession,
    snapshot_id: int,
    user_id: int,
) -> bool:
    """Deletes entire snapshot and all its items (cascade)."""
    try:
        snap = await session.get(SavingsSnapshot, snapshot_id)
        if not snap or snap.user_id != user_id:
            return False
        await session.delete(snap)
        await session.flush()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in delete_snapshot: {e}")
        return False


# ---------- Wealth items (assets/liabilities) ----------


async def get_wealth_items(session: AsyncSession, user_id: int) -> list[WealthItem]:
    """Returns all wealth items for user ordered by type then name."""
    result = await session.execute(
        select(WealthItem)
        .where(WealthItem.user_id == user_id)
        .order_by(WealthItem.type.asc(), WealthItem.name.asc())
    )
    return list(result.scalars().all())


async def add_wealth_item(
    session: AsyncSession,
    user_id: int,
    type_: str,
    name: str,
    amount: Decimal,
    note: str | None = None,
) -> WealthItem | None:
    """Creates a new wealth item."""
    try:
        item = WealthItem(
            user_id=user_id, type=type_, name=name, amount=amount, note=note
        )
        session.add(item)
        await session.flush()
        return item
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in add_wealth_item: {e}")
        return None


async def update_wealth_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
    **fields,
) -> bool:
    """Updates fields of a wealth item (validates ownership)."""
    try:
        item = await session.get(WealthItem, item_id)
        if not item or item.user_id != user_id:
            return False
        for key, value in fields.items():
            setattr(item, key, value)
        await session.flush()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in update_wealth_item: {e}")
        return False


async def delete_wealth_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
) -> bool:
    """Deletes a wealth item (validates ownership)."""
    try:
        item = await session.get(WealthItem, item_id)
        if not item or item.user_id != user_id:
            return False
        await session.delete(item)
        await session.flush()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in delete_wealth_item: {e}")
        return False
