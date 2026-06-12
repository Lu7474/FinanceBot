"""Tests for backup/export DB getters in core/database/requests/backup.py.

Covers get_all_records_for_export (filters + account eager-load),
get_all_budgets_for_backup, get_latest_snapshot_for_backup and
get_wealth_items_for_backup. bulk_insert_records is covered in
test_export_import.py.
"""

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import (
    Account,
    Budget,
    Record,
    SavingsItem,
    SavingsSnapshot,
    WealthItem,
)
from core.database.requests.backup import (
    get_all_budgets_for_backup,
    get_all_records_for_export,
    get_latest_snapshot_for_backup,
    get_wealth_items_for_backup,
)
from core.database.requests.users import set_user

# ==================== Helpers ====================


async def _make_user(session, tg_id: int = 1) -> int:
    user = await set_user(session, tg_id, name="Test")
    return user.id


def _rec(user_id, op, amount, cat, dt, account_id=None) -> Record:
    return Record(
        user_id=user_id,
        operation=op,
        amount=Decimal(str(amount)),
        category=cat,
        created_at=dt,
        account_id=account_id,
    )


# ==================== get_all_records_for_export ====================


@pytest.mark.asyncio
async def test_get_all_records_no_filter_ordered(session):
    uid = await _make_user(session)
    session.add(_rec(uid, "-", 100, "Еда", datetime(2025, 5, 3)))
    session.add(_rec(uid, "+", 200, "ЗП", datetime(2025, 5, 1)))
    session.add(_rec(uid, "-", 50, "Кафе", datetime(2025, 5, 2)))
    await session.commit()

    rows = await get_all_records_for_export(session, uid)
    # Ordered by created_at ascending.
    assert [r.created_at for r in rows] == [
        datetime(2025, 5, 1),
        datetime(2025, 5, 2),
        datetime(2025, 5, 3),
    ]


@pytest.mark.asyncio
async def test_get_all_records_operation_filter(session):
    uid = await _make_user(session)
    session.add(_rec(uid, "-", 100, "Еда", datetime(2025, 5, 1)))
    session.add(_rec(uid, "+", 200, "ЗП", datetime(2025, 5, 2)))
    await session.commit()

    rows = await get_all_records_for_export(session, uid, operation="+")
    assert len(rows) == 1
    assert rows[0].operation == "+"


@pytest.mark.asyncio
async def test_get_all_records_date_range_inclusive(session):
    uid = await _make_user(session)
    session.add(_rec(uid, "-", 1, "Еда", datetime(2025, 5, 1, 8, 0)))
    session.add(_rec(uid, "-", 2, "Еда", datetime(2025, 5, 15, 23, 0)))
    session.add(_rec(uid, "-", 3, "Еда", datetime(2025, 6, 1, 0, 0)))
    await session.commit()

    rows = await get_all_records_for_export(
        session, uid, date_from=date(2025, 5, 1), date_to=date(2025, 5, 15)
    )
    amounts = sorted(int(r.amount) for r in rows)
    # Boundaries inclusive (max.time on date_to keeps the 23:00 record), June excluded.
    assert amounts == [1, 2]


@pytest.mark.asyncio
async def test_get_all_records_eager_loads_account(session):
    uid = await _make_user(session)
    acc = Account(user_id=uid, name="Наличные")
    session.add(acc)
    await session.flush()
    session.add(_rec(uid, "-", 100, "Еда", datetime(2025, 5, 1), account_id=acc.id))
    await session.commit()
    session.expunge_all()  # force detach so lazy access would fail without eager-load

    rows = await get_all_records_for_export(session, uid)
    # selectinload(Record.account) → safe to read on detached instance.
    assert rows[0].account.name == "Наличные"


@pytest.mark.asyncio
async def test_get_all_records_scoped_to_user(session):
    uid = await _make_user(session, tg_id=1)
    other = await _make_user(session, tg_id=2)
    session.add(_rec(uid, "-", 100, "Еда", datetime(2025, 5, 1)))
    session.add(_rec(other, "-", 999, "Еда", datetime(2025, 5, 1)))
    await session.commit()

    rows = await get_all_records_for_export(session, uid)
    assert len(rows) == 1
    assert int(rows[0].amount) == 100


# ==================== get_all_budgets_for_backup ====================


@pytest.mark.asyncio
async def test_get_all_budgets_only_active(session):
    uid = await _make_user(session)
    session.add(Budget(user_id=uid, category="Еда", amount=Decimal("5000")))
    session.add(
        Budget(user_id=uid, category="Кафе", amount=Decimal("1000"), is_active=False)
    )
    await session.commit()

    budgets = await get_all_budgets_for_backup(session, uid)
    assert len(budgets) == 1
    assert budgets[0].category == "Еда"


@pytest.mark.asyncio
async def test_get_all_budgets_empty(session):
    uid = await _make_user(session)
    assert await get_all_budgets_for_backup(session, uid) == []


# ==================== get_latest_snapshot_for_backup ====================


@pytest.mark.asyncio
async def test_get_latest_snapshot_none(session):
    uid = await _make_user(session)
    assert await get_latest_snapshot_for_backup(session, uid) is None


@pytest.mark.asyncio
async def test_get_latest_snapshot_picks_newest_with_items(session):
    uid = await _make_user(session)
    old = SavingsSnapshot(user_id=uid, date=date(2025, 1, 1))
    new = SavingsSnapshot(user_id=uid, date=date(2025, 5, 1))
    session.add_all([old, new])
    await session.flush()
    session.add(SavingsItem(snapshot_id=new.id, type="A", name="Вклад", amount=Decimal("100000")))
    session.add(SavingsItem(snapshot_id=old.id, type="A", name="Старое", amount=Decimal("1")))
    await session.commit()
    session.expunge_all()

    snap = await get_latest_snapshot_for_backup(session, uid)
    assert snap is not None
    assert snap.date == date(2025, 5, 1)
    # items eager-loaded via selectinload.
    assert [i.name for i in snap.items] == ["Вклад"]


# ==================== get_wealth_items_for_backup ====================


@pytest.mark.asyncio
async def test_get_wealth_items_empty(session):
    uid = await _make_user(session)
    assert await get_wealth_items_for_backup(session, uid) == []


@pytest.mark.asyncio
async def test_get_wealth_items_returns_all(session):
    uid = await _make_user(session)
    session.add(WealthItem(user_id=uid, type="A", name="Квартира", amount=Decimal("5000000")))
    session.add(WealthItem(user_id=uid, type="L", name="Ипотека", amount=Decimal("3000000")))
    await session.commit()

    items = await get_wealth_items_for_backup(session, uid)
    assert {i.name for i in items} == {"Квартира", "Ипотека"}
