"""Tests for the Capital section: snapshots (typed), wealth CRUD, virtual rows."""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import SavingsItem, User
from core.database.requests import (
    add_wealth_item,
    collect_capital_items,
    create_account,
    create_debt,
    create_snapshot_from_wealth,
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


async def _make_account(session, user_id: int, name: str, balance) -> None:
    """Creates an account and pins its balance via balance_offset."""
    acc = await create_account(session, user_id, name)
    assert acc is not None
    acc.balance_offset = Decimal(str(balance))
    await session.commit()


# ==================== upsert_snapshot (typed items) ====================

@pytest.mark.asyncio
async def test_upsert_snapshot_creates_new(session):
    user_id = await _make_user(session)
    d = date(2025, 1, 15)
    snap = await upsert_snapshot(
        session, user_id, d,
        [("A", "Наличные", Decimal("5000")), ("A", "Карта", Decimal("10000"))],
    )
    assert snap is not None
    assert snap.user_id == user_id
    assert snap.date == d


@pytest.mark.asyncio
async def test_upsert_snapshot_persists_type(session):
    user_id = await _make_user(session)
    d = date(2025, 2, 1)
    await upsert_snapshot(
        session, user_id, d,
        [("A", "Вклад", Decimal("100")), ("P", "Кредит", Decimal("200"))],
    )
    snap = await get_snapshot(session, user_id, d)
    by_name = {i.name: i.type for i in snap.items}
    assert by_name == {"Вклад": "A", "Кредит": "P"}


@pytest.mark.asyncio
async def test_upsert_snapshot_replaces_items(session):
    user_id = await _make_user(session)
    d = date(2025, 1, 15)
    await upsert_snapshot(session, user_id, d, [("A", "Старый", Decimal("1000"))])
    await upsert_snapshot(session, user_id, d, [("P", "Новый", Decimal("9999"))])
    result = await get_snapshot(session, user_id, d)
    assert len(result.items) == 1
    assert result.items[0].name == "Новый"
    assert result.items[0].type == "P"
    assert result.items[0].amount == Decimal("9999")


@pytest.mark.asyncio
async def test_upsert_snapshot_empty_items(session):
    user_id = await _make_user(session)
    d = date(2025, 3, 1)
    snap = await upsert_snapshot(session, user_id, d, [])
    assert snap is not None
    result = await get_snapshot(session, user_id, d)
    assert result.items == []


@pytest.mark.asyncio
async def test_savings_item_default_type_is_asset(session):
    """server_default 'A' covers legacy rows migrated without a type."""
    user_id = await _make_user(session)
    snap = await upsert_snapshot(session, user_id, date(2025, 4, 1), [])
    item = SavingsItem(snapshot_id=snap.id, name="legacy", amount=Decimal("1"))
    session.add(item)
    await session.commit()
    await session.refresh(item)
    assert item.type == "A"


# ==================== get_snapshots_dates ====================

@pytest.mark.asyncio
async def test_get_snapshots_dates_empty(session):
    user_id = await _make_user(session)
    assert await get_snapshots_dates(session, user_id) == []


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
    assert await get_snapshots_dates(session, user2_id) == []


# ==================== get_snapshot / get_snapshot_by_id ====================

@pytest.mark.asyncio
async def test_get_snapshot_returns_items(session):
    user_id = await _make_user(session)
    d = date(2025, 5, 10)
    await upsert_snapshot(
        session, user_id, d,
        [("A", "Счёт1", Decimal("3000")), ("A", "Счёт2", Decimal("7000"))],
    )
    snap = await get_snapshot(session, user_id, d)
    assert snap is not None
    assert {item.name for item in snap.items} == {"Счёт1", "Счёт2"}


@pytest.mark.asyncio
async def test_get_snapshot_nonexistent(session):
    user_id = await _make_user(session)
    assert await get_snapshot(session, user_id, date(2020, 1, 1)) is None


@pytest.mark.asyncio
async def test_get_snapshot_by_id_success(session):
    user_id = await _make_user(session)
    snap = await upsert_snapshot(
        session, user_id, date(2025, 6, 1), [("A", "A", Decimal("100"))]
    )
    result = await get_snapshot_by_id(session, snap.id, user_id)
    assert result is not None and result.id == snap.id


@pytest.mark.asyncio
async def test_get_snapshot_by_id_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    snap = await upsert_snapshot(
        session, user1_id, date(2025, 6, 1), [("A", "A", Decimal("100"))]
    )
    assert await get_snapshot_by_id(session, snap.id, user2_id) is None


# ==================== get_latest_snapshot ====================

@pytest.mark.asyncio
async def test_get_latest_snapshot_empty(session):
    user_id = await _make_user(session)
    assert await get_latest_snapshot(session, user_id) is None


@pytest.mark.asyncio
async def test_get_latest_snapshot_returns_newest(session):
    user_id = await _make_user(session)
    await upsert_snapshot(session, user_id, date(2025, 1, 1), [("A", "Old", Decimal("100"))])
    await upsert_snapshot(session, user_id, date(2025, 6, 1), [("A", "New", Decimal("999"))])
    snap = await get_latest_snapshot(session, user_id)
    assert snap.date == date(2025, 6, 1)
    assert snap.items[0].name == "New"


# ==================== update_snapshot_item ====================

@pytest.mark.asyncio
async def test_update_snapshot_item_success(session):
    user_id = await _make_user(session)
    d = date(2025, 8, 1)
    await upsert_snapshot(session, user_id, d, [("A", "Карта", Decimal("5000"))])
    fetched = await get_snapshot(session, user_id, d)
    ok = await update_snapshot_item(session, fetched.items[0].id, user_id, Decimal("9999"))
    assert ok is True
    updated = await get_snapshot(session, user_id, d)
    assert updated.items[0].amount == Decimal("9999")


@pytest.mark.asyncio
async def test_update_snapshot_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    d = date(2025, 8, 1)
    await upsert_snapshot(session, user1_id, d, [("A", "Карта", Decimal("5000"))])
    fetched = await get_snapshot(session, user1_id, d)
    ok = await update_snapshot_item(session, fetched.items[0].id, user2_id, Decimal("1"))
    assert ok is False


@pytest.mark.asyncio
async def test_update_snapshot_item_nonexistent(session):
    user_id = await _make_user(session)
    assert await update_snapshot_item(session, 999999, user_id, Decimal("100")) is False


# ==================== delete_snapshot_item ====================

@pytest.mark.asyncio
async def test_delete_snapshot_item_success(session):
    user_id = await _make_user(session)
    d = date(2025, 9, 1)
    await upsert_snapshot(
        session, user_id, d,
        [("A", "А", Decimal("1000")), ("A", "Б", Decimal("2000"))],
    )
    fetched = await get_snapshot(session, user_id, d)
    snap_date = await delete_snapshot_item(session, fetched.items[0].id, user_id)
    assert snap_date == d
    updated = await get_snapshot(session, user_id, d)
    assert len(updated.items) == 1


@pytest.mark.asyncio
async def test_delete_snapshot_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    d = date(2025, 9, 1)
    await upsert_snapshot(session, user1_id, d, [("A", "А", Decimal("1000"))])
    fetched = await get_snapshot(session, user1_id, d)
    assert await delete_snapshot_item(session, fetched.items[0].id, user2_id) is None


@pytest.mark.asyncio
async def test_delete_snapshot_item_nonexistent(session):
    user_id = await _make_user(session)
    assert await delete_snapshot_item(session, 999999, user_id) is None


# ==================== delete_snapshot ====================

@pytest.mark.asyncio
async def test_delete_snapshot_success(session):
    user_id = await _make_user(session)
    d = date(2025, 10, 1)
    snap = await upsert_snapshot(session, user_id, d, [("A", "X", Decimal("500"))])
    assert await delete_snapshot(session, snap.id, user_id) is True
    assert await get_snapshot(session, user_id, d) is None


@pytest.mark.asyncio
async def test_delete_snapshot_cascades_items(session):
    user_id = await _make_user(session)
    d = date(2025, 10, 2)
    snap = await upsert_snapshot(
        session, user_id, d,
        [("A", "X", Decimal("100")), ("P", "Y", Decimal("200"))],
    )
    snap_id = snap.id
    await delete_snapshot(session, snap_id, user_id)
    from sqlalchemy import select
    result = await session.execute(
        select(SavingsItem).where(SavingsItem.snapshot_id == snap_id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_delete_snapshot_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    snap = await upsert_snapshot(
        session, user1_id, date(2025, 10, 3), [("A", "X", Decimal("500"))]
    )
    assert await delete_snapshot(session, snap.id, user2_id) is False


@pytest.mark.asyncio
async def test_delete_snapshot_nonexistent(session):
    user_id = await _make_user(session)
    assert await delete_snapshot(session, 999999, user_id) is False


# ==================== Wealth: add / get ====================

@pytest.mark.asyncio
async def test_add_wealth_item_asset(session):
    user_id = await _make_user(session)
    item = await add_wealth_item(session, user_id, "A", "Квартира", Decimal("5000000"))
    assert item is not None
    assert item.type == "A"
    assert item.name == "Квартира"
    assert item.note is None


@pytest.mark.asyncio
async def test_add_wealth_item_with_note(session):
    user_id = await _make_user(session)
    item = await add_wealth_item(
        session, user_id, "P", "Ипотека", Decimal("3000000"), "Сбербанк"
    )
    assert item is not None and item.note == "Сбербанк"


@pytest.mark.asyncio
async def test_get_wealth_items_empty(session):
    user_id = await _make_user(session)
    assert await get_wealth_items(session, user_id) == []


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
    assert await get_wealth_items(session, user2_id) == []


# ==================== Wealth: update / delete ====================

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
    assert await update_wealth_item(session, item.id, user2_id, amount=Decimal("1")) is False


@pytest.mark.asyncio
async def test_update_wealth_item_nonexistent(session):
    user_id = await _make_user(session)
    assert await update_wealth_item(session, 999999, user_id, amount=Decimal("1")) is False


@pytest.mark.asyncio
async def test_delete_wealth_item_success(session):
    user_id = await _make_user(session)
    item = await add_wealth_item(session, user_id, "A", "Вклад", Decimal("100000"))
    assert await delete_wealth_item(session, item.id, user_id) is True
    assert await get_wealth_items(session, user_id) == []


@pytest.mark.asyncio
async def test_delete_wealth_item_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    item = await add_wealth_item(session, user1_id, "A", "Вклад", Decimal("100000"))
    assert await delete_wealth_item(session, item.id, user2_id) is False
    assert len(await get_wealth_items(session, user1_id)) == 1


@pytest.mark.asyncio
async def test_delete_wealth_item_nonexistent(session):
    user_id = await _make_user(session)
    assert await delete_wealth_item(session, 999999, user_id) is False


# ==================== collect_capital_items (virtual rows) ====================

@pytest.mark.asyncio
async def test_collect_capital_items_includes_wealth(session):
    user_id = await _make_user(session)
    await add_wealth_item(session, user_id, "A", "Квартира", Decimal("5000000"))
    await add_wealth_item(session, user_id, "P", "Ипотека", Decimal("3000000"))
    items = await collect_capital_items(session, user_id)
    assert ("A", "Квартира", Decimal("5000000")) in items
    assert ("P", "Ипотека", Decimal("3000000")) in items


@pytest.mark.asyncio
async def test_collect_capital_items_debts_directions(session):
    user_id = await _make_user(session)
    await create_debt(session, user_id, "I", "Андрей", Decimal("15000"), None, None)
    await create_debt(session, user_id, "O", "Банк", Decimal("28000"), None, None)
    await session.commit()
    items = await collect_capital_items(session, user_id)
    assert ("A", "Мне должны: Андрей", Decimal("15000")) in items
    assert ("P", "Долг: Банк", Decimal("28000")) in items


@pytest.mark.asyncio
async def test_collect_capital_items_debt_uses_remaining(session):
    user_id = await _make_user(session)
    from core.database.requests import add_payment
    debt = await create_debt(
        session, user_id, "I", "Петя", Decimal("10000"), None, None
    )
    debt_id = debt.id
    await session.commit()
    await add_payment(session, debt_id, user_id, Decimal("4000"), None)
    await session.commit()
    items = await collect_capital_items(session, user_id)
    assert ("A", "Мне должны: Петя", Decimal("6000")) in items


@pytest.mark.asyncio
async def test_collect_capital_items_accounts_sign(session):
    user_id = await _make_user(session)
    await _make_account(session, user_id, "Наличка", "45000")
    await _make_account(session, user_id, "Кредитка", "-5000")
    await _make_account(session, user_id, "Пустой", "0")
    items = await collect_capital_items(session, user_id)
    assert ("A", "Наличка", Decimal("45000")) in items
    assert ("P", "Кредитка", Decimal("5000")) in items
    assert not any(name == "Пустой" for _, name, _ in items)


@pytest.mark.asyncio
async def test_collect_capital_items_empty(session):
    user_id = await _make_user(session)
    assert await collect_capital_items(session, user_id) == []


# ==================== create_snapshot_from_wealth ====================

@pytest.mark.asyncio
async def test_create_snapshot_from_wealth_freezes_all_sources(session):
    user_id = await _make_user(session)
    await add_wealth_item(session, user_id, "A", "Квартира", Decimal("5000000"))
    await create_debt(session, user_id, "I", "Андрей", Decimal("15000"), None, None)
    await _make_account(session, user_id, "Наличка", "45000")
    await session.commit()

    snap = await create_snapshot_from_wealth(session, user_id, date(2026, 6, 12))
    assert snap is not None
    await session.commit()
    fetched = await get_snapshot(session, user_id, date(2026, 6, 12))
    frozen = {(i.type, i.name, i.amount) for i in fetched.items}
    assert ("A", "Квартира", Decimal("5000000")) in frozen
    assert ("A", "Мне должны: Андрей", Decimal("15000")) in frozen
    assert ("A", "Наличка", Decimal("45000")) in frozen


@pytest.mark.asyncio
async def test_create_snapshot_from_wealth_empty_returns_none(session):
    user_id = await _make_user(session)
    snap = await create_snapshot_from_wealth(session, user_id, date(2026, 6, 12))
    assert snap is None
    assert await get_snapshot(session, user_id, date(2026, 6, 12)) is None


@pytest.mark.asyncio
async def test_create_snapshot_from_wealth_overwrites(session):
    user_id = await _make_user(session)
    await add_wealth_item(session, user_id, "A", "Старый", Decimal("100"))
    d = date(2026, 6, 12)
    await create_snapshot_from_wealth(session, user_id, d)
    await session.commit()

    # change capital, re-snapshot same day → upsert replaces
    items = await get_wealth_items(session, user_id)
    await update_wealth_item(session, items[0].id, user_id, amount=Decimal("999"))
    await session.commit()
    await create_snapshot_from_wealth(session, user_id, d)
    await session.commit()

    fetched = await get_snapshot(session, user_id, d)
    assert len(fetched.items) == 1
    assert fetched.items[0].amount == Decimal("999")


# ==================== Handler view builders ====================
#
# _build_capital_view / _build_history_view open their own async_session
# internally. We point that at the shared in-memory test engine and seed data
# through test_session() so the builder's fresh session sees it.

from conftest import test_session  # noqa: E402

import core.handlers.capital as cap_handlers  # noqa: E402
from core.handlers.capital import (  # noqa: E402
    _build_capital_view,
    _build_history_view,
)


@pytest.fixture
def capital_db(monkeypatch):
    monkeypatch.setattr(cap_handlers, "async_session", test_session)


def _cbs(kb) -> set:
    return {b.callback_data for row in kb.inline_keyboard for b in row}


async def _seed_user(tg_id: int = 1) -> int:
    async with test_session() as s:
        user = User(tg_id=tg_id, name="Test")
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


async def _seed_account(user_id: int, name: str, balance) -> None:
    async with test_session() as s:
        acc = await create_account(s, user_id, name)
        acc.balance_offset = Decimal(str(balance))
        await s.commit()


# --- _build_capital_view ---

@pytest.mark.asyncio
async def test_build_capital_view_empty(capital_db):
    user_id = await _seed_user()
    text, kb = await _build_capital_view(user_id)
    assert "Капитал" in text
    assert "Нет данных" in text
    # no manual items → edit/delete hidden
    assert "cap_wealth_edit" not in _cbs(kb)
    assert "cap_wealth_delete" not in _cbs(kb)
    assert {"cap_add", "cap_snapshot", "cap_history"} <= _cbs(kb)


@pytest.mark.asyncio
async def test_build_capital_view_with_manual_shows_edit(capital_db):
    user_id = await _seed_user()
    async with test_session() as s:
        await add_wealth_item(s, user_id, "A", "Квартира", Decimal("5000000"))
        await s.commit()
    text, kb = await _build_capital_view(user_id)
    assert "Квартира" in text
    assert {"cap_wealth_edit", "cap_wealth_delete"} <= _cbs(kb)


@pytest.mark.asyncio
async def test_build_capital_view_virtual_rows_and_net(capital_db):
    user_id = await _seed_user()
    await _seed_account(user_id, "Наличка", "45000")
    await _seed_account(user_id, "Кредитка", "-5000")
    async with test_session() as s:
        await create_debt(s, user_id, "I", "Андрей", Decimal("15000"), None, None)
        await create_debt(s, user_id, "O", "Банк", Decimal("28000"), None, None)
        await s.commit()
    text, kb = await _build_capital_view(user_id)
    assert "💳 Наличка" in text
    assert "Мне должны: Андрей" in text
    assert "Долг: Банк" in text
    # net = 45000 + 15000 - 5000 - 28000 = 27000
    assert "27 000" in text
    # only virtual rows → edit/delete still hidden
    assert "cap_wealth_edit" not in _cbs(kb)


@pytest.mark.asyncio
async def test_build_capital_view_last_snapshot_diff_line(capital_db):
    user_id = await _seed_user()
    async with test_session() as s:
        await add_wealth_item(s, user_id, "A", "Деньги", Decimal("100000"))
        await create_snapshot_from_wealth(s, user_id, date(2025, 1, 1))
        await s.commit()
    text, _ = await _build_capital_view(user_id)
    assert "Последний снимок" in text


# --- _build_history_view ---

@pytest.mark.asyncio
async def test_build_history_view_empty(capital_db):
    user_id = await _seed_user()
    text, kb = await _build_history_view(user_id)
    assert "Снимков пока нет" in text
    assert "cap_to_capital" in _cbs(kb)
    assert not any(c.startswith("cap_date:") for c in _cbs(kb))


@pytest.mark.asyncio
async def test_build_history_view_single_snapshot_no_nav(capital_db):
    user_id = await _seed_user()
    async with test_session() as s:
        await upsert_snapshot(
            s, user_id, date(2025, 6, 1), [("A", "Карта", Decimal("5000"))]
        )
        await s.commit()
    text, kb = await _build_history_view(user_id)
    assert "Карта" in text
    cbs = _cbs(kb)
    assert any(c.startswith("cap_edit:") for c in cbs)
    assert any(c.startswith("cap_delete:") for c in cbs)
    assert not any(c.startswith("cap_date:") for c in cbs)  # single → no nav


@pytest.mark.asyncio
async def test_build_history_view_two_snapshots_nav_and_diff(capital_db):
    user_id = await _seed_user()
    async with test_session() as s:
        await upsert_snapshot(
            s, user_id, date(2025, 5, 1), [("A", "Карта", Decimal("5000"))]
        )
        await upsert_snapshot(
            s, user_id, date(2025, 6, 1), [("A", "Карта", Decimal("6000"))]
        )
        await s.commit()
    # latest (June) compared to May → +1000 diff, prev-nav present
    text, kb = await _build_history_view(user_id)
    assert "+1 000" in text
    assert any(c == "cap_date:2025-05-01" for c in _cbs(kb))


@pytest.mark.asyncio
async def test_build_history_view_targets_specific_date(capital_db):
    user_id = await _seed_user()
    async with test_session() as s:
        await upsert_snapshot(
            s, user_id, date(2025, 5, 1), [("A", "Карта", Decimal("5000"))]
        )
        await upsert_snapshot(
            s, user_id, date(2025, 6, 1), [("A", "Карта", Decimal("6000"))]
        )
        await s.commit()
    # viewing May (oldest) → next-nav to June, no diff markers (no prev)
    text, kb = await _build_history_view(user_id, date(2025, 5, 1))
    assert any(c == "cap_date:2025-06-01" for c in _cbs(kb))
    assert "(+" not in text and "(−" not in text
