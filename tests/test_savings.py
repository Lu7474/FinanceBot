"""Tests for savings snapshots and wealth items CRUD."""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import User
from core.database.requests import (
    add_snapshot_item,
    add_wealth_item,
    delete_snapshot,
    delete_snapshot_item,
    delete_wealth_item,
    get_latest_snapshot,
    get_snapshot,
    get_snapshot_by_id,
    get_snapshots_dates,
    get_wealth_items,
    update_snapshot_item,
    update_wealth_item,
    upsert_snapshot,
)


async def _make_user(session, tg_id: int = 1) -> int:
    user = User(tg_id=tg_id, name="Test")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user.id


# ==================== upsert_snapshot ====================

@pytest.mark.asyncio
async def test_upsert_snapshot_creates_new(session):
    user_id = await _make_user(session)
    d = date(2025, 1, 15)
    snap = await upsert_snapshot(session, user_id, d, [("Наличные", Decimal("5000")), ("Карта", Decimal("10000"))])
    assert snap is not None
    assert snap.user_id == user_id
    assert snap.date == d


@pytest.mark.asyncio
async def test_upsert_snapshot_replaces_items(session):
    user_id = await _make_user(session)
    d = date(2025, 1, 15)
    await upsert_snapshot(session, user_id, d, [("Старый", Decimal("1000"))])
    await upsert_snapshot(session, user_id, d, [("Новый", Decimal("9999"))])
    result = await get_snapshot(session, user_id, d)
    assert len(result.items) == 1
    assert result.items[0].name == "Новый"
    assert result.items[0].amount == Decimal("9999")


@pytest.mark.asyncio
async def test_upsert_snapshot_empty_items(session):
    user_id = await _make_user(session)
    d = date(2025, 3, 1)
    snap = await upsert_snapshot(session, user_id, d, [])
    assert snap is not None
    result = await get_snapshot(session, user_id, d)
    assert result.items == []


# ==================== get_snapshots_dates ====================

@pytest.mark.asyncio
async def test_get_snapshots_dates_empty(session):
    user_id = await _make_user(session)
    dates = await get_snapshots_dates(session, user_id)
    assert dates == []


@pytest.mark.asyncio
async def test_get_snapshots_dates_sorted(session):
    user_id = await _make_user(session)
    await upsert_snapshot(session, user_id, date(2025, 3, 1), [])
    await upsert_snapshot(session, user_id, date(2025, 1, 1), [])
    await upsert_snapshot(session, user_id, date(2025, 2, 1), [])
    dates = await get_snapshots_dates(session, user_id)
    assert dates == [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)]


@pytest.mark.asyncio
async def test_get_snapshots_dates_isolated_by_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    await upsert_snapshot(session, user1_id, date(2025, 1, 1), [])
    dates = await get_snapshots_dates(session, user2_id)
    assert dates == []


# ==================== get_snapshot / get_snapshot_by_id ====================

@pytest.mark.asyncio
async def test_get_snapshot_returns_items(session):
    user_id = await _make_user(session)
    d = date(2025, 5, 10)
    await upsert_snapshot(session, user_id, d, [("Счёт1", Decimal("3000")), ("Счёт2", Decimal("7000"))])
    snap = await get_snapshot(session, user_id, d)
    assert snap is not None
    assert len(snap.items) == 2
    names = {item.name for item in snap.items}
    assert names == {"Счёт1", "Счёт2"}


@pytest.mark.asyncio
async def test_get_snapshot_nonexistent(session):
    user_id = await _make_user(session)
    snap = await get_snapshot(session, user_id, date(2020, 1, 1))
    assert snap is None


@pytest.mark.asyncio
async def test_get_snapshot_by_id_success(session):
    user_id = await _make_user(session)
    snap = await upsert_snapshot(session, user_id, date(2025, 6, 1), [("A", Decimal("100"))])
    result = await get_snapshot_by_id(session, snap.id, user_id)
    assert result is not None
    assert result.id == snap.id


@pytest.mark.asyncio
async def test_get_snapshot_by_id_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    snap = await upsert_snapshot(session, user1_id, date(2025, 6, 1), [("A", Decimal("100"))])
    result = await get_snapshot_by_id(session, snap.id, user2_id)
    assert result is None


# ==================== get_latest_snapshot ====================

@pytest.mark.asyncio
async def test_get_latest_snapshot_empty(session):
    user_id = await _make_user(session)
    snap = await get_latest_snapshot(session, user_id)
    assert snap is None


@pytest.mark.asyncio
async def test_get_latest_snapshot_returns_newest(session):
    user_id = await _make_user(session)
    await upsert_snapshot(session, user_id, date(2025, 1, 1), [("Old", Decimal("100"))])
    await upsert_snapshot(session, user_id, date(2025, 6, 1), [("New", Decimal("999"))])
    snap = await get_latest_snapshot(session, user_id)
    assert snap.date == date(2025, 6, 1)
    assert len(snap.items) == 1
    assert snap.items[0].name == "New"


# ==================== add_snapshot_item ====================

@pytest.mark.asyncio
async def test_add_snapshot_item_success(session):
    user_id = await _make_user(session)
    d = date(2025, 7, 1)
    snap = await upsert_snapshot(session, user_id, d, [("Карта", Decimal("5000"))])
    item = await add_snapshot_item(session, snap.id, user_id, "Кэш", Decimal("2000"))
    assert item is not None
    assert item.name == "Кэш"
    result = await get_snapshot(session, user_id, d)
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_add_snapshot_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    snap = await upsert_snapshot(session, user1_id, date(2025, 7, 1), [])
    item = await add_snapshot_item(session, snap.id, user2_id, "Чужой", Decimal("100"))
    assert item is None


# ==================== update_snapshot_item ====================

@pytest.mark.asyncio
async def test_update_snapshot_item_success(session):
    user_id = await _make_user(session)
    d = date(2025, 8, 1)
    await upsert_snapshot(session, user_id, d, [("Карта", Decimal("5000"))])
    fetched = await get_snapshot(session, user_id, d)
    item_id = fetched.items[0].id
    ok = await update_snapshot_item(session, item_id, user_id, Decimal("9999"))
    assert ok is True
    updated = await get_snapshot(session, user_id, d)
    assert updated.items[0].amount == Decimal("9999")


@pytest.mark.asyncio
async def test_update_snapshot_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    d = date(2025, 8, 1)
    await upsert_snapshot(session, user1_id, d, [("Карта", Decimal("5000"))])
    fetched = await get_snapshot(session, user1_id, d)
    item_id = fetched.items[0].id
    ok = await update_snapshot_item(session, item_id, user2_id, Decimal("1"))
    assert ok is False


@pytest.mark.asyncio
async def test_update_snapshot_item_nonexistent(session):
    user_id = await _make_user(session)
    ok = await update_snapshot_item(session, 999999, user_id, Decimal("100"))
    assert ok is False


# ==================== delete_snapshot_item ====================

@pytest.mark.asyncio
async def test_delete_snapshot_item_success(session):
    user_id = await _make_user(session)
    d = date(2025, 9, 1)
    await upsert_snapshot(session, user_id, d, [("А", Decimal("1000")), ("Б", Decimal("2000"))])
    fetched = await get_snapshot(session, user_id, d)
    item_id = fetched.items[0].id
    snap_date = await delete_snapshot_item(session, item_id, user_id)
    assert snap_date == d
    updated = await get_snapshot(session, user_id, d)
    assert len(updated.items) == 1


@pytest.mark.asyncio
async def test_delete_snapshot_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    d = date(2025, 9, 1)
    await upsert_snapshot(session, user1_id, d, [("А", Decimal("1000"))])
    fetched = await get_snapshot(session, user1_id, d)
    item_id = fetched.items[0].id
    result = await delete_snapshot_item(session, item_id, user2_id)
    assert result is None


@pytest.mark.asyncio
async def test_delete_snapshot_item_nonexistent(session):
    user_id = await _make_user(session)
    result = await delete_snapshot_item(session, 999999, user_id)
    assert result is None


# ==================== delete_snapshot ====================

@pytest.mark.asyncio
async def test_delete_snapshot_success(session):
    user_id = await _make_user(session)
    d = date(2025, 10, 1)
    snap = await upsert_snapshot(session, user_id, d, [("X", Decimal("500"))])
    ok = await delete_snapshot(session, snap.id, user_id)
    assert ok is True
    result = await get_snapshot(session, user_id, d)
    assert result is None


@pytest.mark.asyncio
async def test_delete_snapshot_cascades_items(session):
    user_id = await _make_user(session)
    d = date(2025, 10, 2)
    snap = await upsert_snapshot(session, user_id, d, [("X", Decimal("100")), ("Y", Decimal("200"))])
    snap_id = snap.id
    fetched = await get_snapshot(session, user_id, d)
    item_ids = [i.id for i in fetched.items]
    assert len(item_ids) == 2

    await delete_snapshot(session, snap_id, user_id)
    from sqlalchemy import select

    from core.database.models import SavingsItem
    result = await session.execute(
        select(SavingsItem).where(SavingsItem.snapshot_id == snap_id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_delete_snapshot_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    snap = await upsert_snapshot(session, user1_id, date(2025, 10, 3), [("X", Decimal("500"))])
    ok = await delete_snapshot(session, snap.id, user2_id)
    assert ok is False


@pytest.mark.asyncio
async def test_delete_snapshot_nonexistent(session):
    user_id = await _make_user(session)
    ok = await delete_snapshot(session, 999999, user_id)
    assert ok is False


# ==================== Wealth: add / get ====================

@pytest.mark.asyncio
async def test_add_wealth_item_asset(session):
    user_id = await _make_user(session)
    item = await add_wealth_item(session, user_id, "A", "Квартира", Decimal("5000000"))
    assert item is not None
    assert item.type == "A"
    assert item.name == "Квартира"
    assert item.amount == Decimal("5000000")
    assert item.note is None


@pytest.mark.asyncio
async def test_add_wealth_item_with_note(session):
    user_id = await _make_user(session)
    item = await add_wealth_item(session, user_id, "P", "Ипотека", Decimal("3000000"), "Сбербанк")
    assert item is not None
    assert item.note == "Сбербанк"


@pytest.mark.asyncio
async def test_get_wealth_items_empty(session):
    user_id = await _make_user(session)
    items = await get_wealth_items(session, user_id)
    assert items == []


@pytest.mark.asyncio
async def test_get_wealth_items_sorted_type_then_name(session):
    user_id = await _make_user(session)
    await add_wealth_item(session, user_id, "P", "Кредит", Decimal("100000"))
    await add_wealth_item(session, user_id, "A", "Машина", Decimal("500000"))
    await add_wealth_item(session, user_id, "A", "Акции", Decimal("200000"))
    items = await get_wealth_items(session, user_id)
    assert items[0].type == "A" and items[1].type == "A" and items[2].type == "P"
    assert items[0].name == "Акции"
    assert items[1].name == "Машина"


@pytest.mark.asyncio
async def test_get_wealth_items_isolated_by_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    await add_wealth_item(session, user1_id, "A", "Вклад", Decimal("100000"))
    items = await get_wealth_items(session, user2_id)
    assert items == []


# ==================== Wealth: update ====================

@pytest.mark.asyncio
async def test_update_wealth_item_amount(session):
    user_id = await _make_user(session)
    item = await add_wealth_item(session, user_id, "A", "Вклад", Decimal("100000"))
    ok = await update_wealth_item(session, item.id, user_id, amount=Decimal("150000"))
    assert ok is True
    items = await get_wealth_items(session, user_id)
    assert items[0].amount == Decimal("150000")


@pytest.mark.asyncio
async def test_update_wealth_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    item = await add_wealth_item(session, user1_id, "A", "Вклад", Decimal("100000"))
    ok = await update_wealth_item(session, item.id, user2_id, amount=Decimal("1"))
    assert ok is False


@pytest.mark.asyncio
async def test_update_wealth_item_nonexistent(session):
    user_id = await _make_user(session)
    ok = await update_wealth_item(session, 999999, user_id, amount=Decimal("1"))
    assert ok is False


# ==================== Wealth: delete ====================

@pytest.mark.asyncio
async def test_delete_wealth_item_success(session):
    user_id = await _make_user(session)
    item = await add_wealth_item(session, user_id, "A", "Вклад", Decimal("100000"))
    ok = await delete_wealth_item(session, item.id, user_id)
    assert ok is True
    assert await get_wealth_items(session, user_id) == []


@pytest.mark.asyncio
async def test_delete_wealth_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    item = await add_wealth_item(session, user1_id, "A", "Вклад", Decimal("100000"))
    ok = await delete_wealth_item(session, item.id, user2_id)
    assert ok is False
    assert len(await get_wealth_items(session, user1_id)) == 1


@pytest.mark.asyncio
async def test_delete_wealth_item_nonexistent(session):
    user_id = await _make_user(session)
    ok = await delete_wealth_item(session, 999999, user_id)
    assert ok is False
