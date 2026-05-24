"""Tests for delete_records_bulk: period filtering, system-category exclusion, user isolation."""

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Record, User
from core.database.requests import (
    BALANCE_SET_CATEGORY,
    TRANSFER_CATEGORY,
    add_record,
    count_records,
    delete_records_bulk,
    get_user_by_tg_id,
)


async def _make_user(session, tg_id: int) -> int:
    """Create user and return DB id."""
    user = User(tg_id=tg_id, name=f"u{tg_id}")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    fetched = await get_user_by_tg_id(session, tg_id)
    assert fetched is not None
    return fetched.id


@pytest.mark.asyncio
async def test_delete_records_bulk_basic(session):
    """Bulk-delete wipes all user's regular records."""
    user_id = await _make_user(session, 1001)
    for i in range(5):
        await add_record(session, user_id, "-", Decimal("100.00"), f"cat{i}")
    await session.commit()

    deleted = await delete_records_bulk(session, user_id, "all")
    await session.commit()

    assert deleted == 5
    assert await count_records(session, user_id, "all") == 0


@pytest.mark.asyncio
async def test_delete_records_bulk_excludes_system(session):
    """Bulk-delete preserves TRANSFER_CATEGORY and BALANCE_SET_CATEGORY records."""
    user_id = await _make_user(session, 1002)
    await add_record(session, user_id, "-", Decimal("100.00"), "еда")
    await add_record(session, user_id, "+", Decimal("500.00"), "зарплата")
    await add_record(session, user_id, "-", Decimal("50.00"), TRANSFER_CATEGORY)
    await add_record(session, user_id, "+", Decimal("50.00"), TRANSFER_CATEGORY)
    await add_record(session, user_id, "+", Decimal("1000.00"), BALANCE_SET_CATEGORY)
    await session.commit()

    deleted = await delete_records_bulk(session, user_id, "all")
    await session.commit()

    assert deleted == 2  # only the regular two

    # System records must remain — fetch raw to bypass count_records'
    # SYSTEM_CATEGORIES filter.
    total = await session.scalar(
        select(func.count(Record.id)).where(Record.user_id == user_id)
    )
    assert total == 3


@pytest.mark.asyncio
async def test_delete_records_bulk_isolation(session):
    """Bulk-delete touches only the target user's rows."""
    user1 = await _make_user(session, 2001)
    user2 = await _make_user(session, 2002)
    for _ in range(3):
        await add_record(session, user1, "-", Decimal("10.00"), "user1")
    for _ in range(3):
        await add_record(session, user2, "-", Decimal("20.00"), "user2")
    await session.commit()

    deleted = await delete_records_bulk(session, user1, "all")
    await session.commit()

    assert deleted == 3
    assert await count_records(session, user1, "all") == 0
    assert await count_records(session, user2, "all") == 3


@pytest.mark.asyncio
async def test_delete_records_bulk_period_filter(session):
    """Bulk-delete with within='range' obeys date_from/date_to bounds."""
    user_id = await _make_user(session, 3001)
    may = datetime(2026, 5, 10, 12, 0, 0)
    june = datetime(2026, 6, 10, 12, 0, 0)
    for _ in range(2):
        await add_record(session, user_id, "-", Decimal("10.00"), "may", created_at=may)
    for _ in range(3):
        await add_record(
            session, user_id, "-", Decimal("20.00"), "june", created_at=june
        )
    await session.commit()

    deleted = await delete_records_bulk(
        session,
        user_id,
        "range",
        date_from=datetime(2026, 5, 1, 0, 0, 0),
        date_to=datetime(2026, 5, 31, 23, 59, 59),
    )
    await session.commit()

    assert deleted == 2
    assert await count_records(session, user_id, "all") == 3


@pytest.mark.asyncio
async def test_delete_records_bulk_empty(session):
    """Bulk-delete on an empty range returns 0 without raising."""
    user_id = await _make_user(session, 4001)
    deleted = await delete_records_bulk(session, user_id, "all")
    await session.commit()
    assert deleted == 0
