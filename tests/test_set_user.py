"""Tests for set_user atomicity: creation, idempotency, race-condition handling."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Account, User, UserCategory
from core.database.requests import get_user_by_tg_id, set_user


@pytest.mark.asyncio
async def test_set_user_creates_new_user_with_seeded_categories(session):
    """New user gets 8 default categories in the same transaction."""
    user = await set_user(session, tg_id=100, name="Alice")

    assert user is not None
    assert user.tg_id == 100
    assert user.name == "Alice"

    count = await session.scalar(
        select(func.count(UserCategory.id)).where(UserCategory.user_id == user.id)
    )
    assert count == 8


@pytest.mark.asyncio
async def test_set_user_with_default_account_creates_account(session):
    """default_account_name creates account atomically with user."""
    user = await set_user(
        session, tg_id=101, name="Bob", default_account_name="Наличные"
    )

    assert user is not None
    accounts = list(
        await session.scalars(select(Account).where(Account.user_id == user.id))
    )
    assert len(accounts) == 1
    assert accounts[0].name == "Наличные"


@pytest.mark.asyncio
async def test_set_user_idempotent_updates_name_only(session):
    """Re-calling set_user with same tg_id updates name; no duplicate categories/accounts."""
    first = await set_user(
        session, tg_id=102, name="Old", default_account_name="Наличные"
    )
    assert first is not None
    first_id = first.id

    second = await set_user(
        session, tg_id=102, name="New", default_account_name="Наличные"
    )
    assert second is not None
    assert second.id == first_id
    assert second.name == "New"

    cat_count = await session.scalar(
        select(func.count(UserCategory.id)).where(UserCategory.user_id == first_id)
    )
    assert cat_count == 8

    acc_count = await session.scalar(
        select(func.count(Account.id)).where(Account.user_id == first_id)
    )
    assert acc_count == 1


@pytest.mark.asyncio
async def test_set_user_handles_race_condition_returns_existing():
    """Race simulation: flush raises IntegrityError → rollback + re-read returns existing user."""
    existing_user = MagicMock(spec=User)
    existing_user.id = 999
    existing_user.tg_id = 103
    existing_user.name = "Original"

    session = MagicMock()
    session.add = MagicMock()
    session.scalar = AsyncMock(side_effect=[None, existing_user])
    session.flush = AsyncMock(
        side_effect=IntegrityError("UNIQUE constraint", {}, Exception("tg_id"))
    )
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    result = await set_user(session, tg_id=103, name="Race")

    assert result is existing_user
    session.rollback.assert_awaited_once()
    assert session.scalar.await_count == 2
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_set_user_without_default_account_creates_no_account(session):
    """Without default_account_name, no account is created."""
    user = await set_user(session, tg_id=104, name="NoAcc")
    assert user is not None

    acc_count = await session.scalar(
        select(func.count(Account.id)).where(Account.user_id == user.id)
    )
    assert acc_count == 0


@pytest.mark.asyncio
async def test_set_user_get_after_create(session):
    """set_user followed by get_user_by_tg_id returns same row."""
    created = await set_user(session, tg_id=105, name="Charlie")
    found = await get_user_by_tg_id(session, 105)
    assert found is not None
    assert found.id == created.id
