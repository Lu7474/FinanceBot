"""Tests for move_and_delete_account, set_account_balance, get_account_balance."""
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Record
from core.database.requests import (
    add_record,
    create_account,
    get_account_balance,
    get_accounts,
    move_and_delete_account,
    set_account_balance,
    set_user,
)


async def _make_user(session, tg_id: int = 1) -> int:
    user = await set_user(session, tg_id, name="Test")
    return user.id


async def _make_account(session, user_id: int, name: str = "Счёт") -> int:
    acc = await create_account(session, user_id, name)
    return acc.id


# ==================== get_account_balance ====================

@pytest.mark.asyncio
async def test_get_account_balance_empty(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id)
    balance = await get_account_balance(session, acc_id, user_id)
    assert balance == Decimal("0")


@pytest.mark.asyncio
async def test_get_account_balance_with_records(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id)
    await add_record(session, user_id, "+", Decimal("1000"), account_id=acc_id)
    await add_record(session, user_id, "-", Decimal("250"), account_id=acc_id)
    balance = await get_account_balance(session, acc_id, user_id)
    assert balance == Decimal("750")


@pytest.mark.asyncio
async def test_get_account_balance_nonexistent(session):
    balance = await get_account_balance(session, 99999)
    assert balance == Decimal("0")


@pytest.mark.asyncio
async def test_get_account_balance_ignores_other_accounts(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Счёт1")
    acc2_id = await _make_account(session, user_id, "Счёт2")
    await add_record(session, user_id, "+", Decimal("5000"), account_id=acc1_id)
    await add_record(session, user_id, "+", Decimal("9000"), account_id=acc2_id)
    assert await get_account_balance(session, acc1_id, user_id) == Decimal("5000")
    assert await get_account_balance(session, acc2_id, user_id) == Decimal("9000")


# ==================== set_account_balance ====================

@pytest.mark.asyncio
async def test_set_account_balance_from_zero(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id)
    ok = await set_account_balance(session, acc_id, Decimal("50000"), user_id)
    assert ok is True
    assert await get_account_balance(session, acc_id, user_id) == Decimal("50000")


@pytest.mark.asyncio
async def test_set_account_balance_with_existing_records(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id)
    await add_record(session, user_id, "+", Decimal("10000"), account_id=acc_id)
    ok = await set_account_balance(session, acc_id, Decimal("15000"), user_id)
    assert ok is True
    assert await get_account_balance(session, acc_id, user_id) == Decimal("15000")


@pytest.mark.asyncio
async def test_set_account_balance_idempotent(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id)
    await set_account_balance(session, acc_id, Decimal("10000"), user_id)
    await set_account_balance(session, acc_id, Decimal("20000"), user_id)
    assert await get_account_balance(session, acc_id, user_id) == Decimal("20000")


@pytest.mark.asyncio
async def test_set_account_balance_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    acc_id = await _make_account(session, user1_id)
    ok = await set_account_balance(session, acc_id, Decimal("99999"), user2_id)
    assert ok is False


@pytest.mark.asyncio
async def test_set_account_balance_nonexistent(session):
    user_id = await _make_user(session)
    ok = await set_account_balance(session, 99999, Decimal("100"), user_id)
    assert ok is False


# ==================== move_and_delete_account ====================

@pytest.mark.asyncio
async def test_move_and_delete_account_moves_records(session):
    user_id = await _make_user(session)
    src_id = await _make_account(session, user_id, "Источник")
    dst_id = await _make_account(session, user_id, "Цель")
    await add_record(session, user_id, "+", Decimal("500"), account_id=src_id)
    await add_record(session, user_id, "-", Decimal("100"), account_id=src_id)

    ok = await move_and_delete_account(session, src_id, user_id, dst_id)
    assert ok is True

    result = await session.execute(select(Record).where(Record.account_id == dst_id))
    moved = result.scalars().all()
    assert len(moved) == 2


@pytest.mark.asyncio
async def test_move_and_delete_account_deletes_source(session):
    user_id = await _make_user(session)
    src_id = await _make_account(session, user_id, "Источник")
    dst_id = await _make_account(session, user_id, "Цель")
    await move_and_delete_account(session, src_id, user_id, dst_id)

    accounts = await get_accounts(session, user_id)
    names = [a.name for a in accounts]
    assert "Источник" not in names
    assert "Цель" in names


@pytest.mark.asyncio
async def test_move_and_delete_account_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    src_id = await _make_account(session, user1_id, "Источник")
    dst_id = await _make_account(session, user1_id, "Цель")
    ok = await move_and_delete_account(session, src_id, user2_id, dst_id)
    assert ok is False


@pytest.mark.asyncio
async def test_move_and_delete_account_nonexistent_source(session):
    user_id = await _make_user(session)
    dst_id = await _make_account(session, user_id, "Цель")
    ok = await move_and_delete_account(session, 99999, user_id, dst_id)
    assert ok is False


@pytest.mark.asyncio
async def test_move_and_delete_account_empty_source(session):
    user_id = await _make_user(session)
    src_id = await _make_account(session, user_id, "Пустой")
    dst_id = await _make_account(session, user_id, "Цель")
    ok = await move_and_delete_account(session, src_id, user_id, dst_id)
    assert ok is True
    accounts = await get_accounts(session, user_id)
    assert len(accounts) == 1
    assert accounts[0].name == "Цель"
