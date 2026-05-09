"""Tests for record edit feature: DB functions, parsers, formatters, keyboards."""

import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.models import Account, Base, Record, User
from core.database.requests import get_record_by_id, update_record
from core.keyboards import record_edit_field_keyboard
from core.utils import (
    format_record_card,
    parse_edit_amount,
    parse_edit_date,
)

# ==================== Test DB setup ====================

test_engine = create_async_engine("sqlite+aiosqlite:///test_records_edit.sqlite3")
test_session = async_sessionmaker(test_engine)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def session():
    async with test_session() as s:
        yield s


@pytest_asyncio.fixture
async def user_and_record(session):
    """Creates user with one income record and one account."""
    user = User(tg_id=100, name="Test")
    session.add(user)
    await session.flush()

    acc = Account(user_id=user.id, name="Карта")
    session.add(acc)
    await session.flush()

    tz = ZoneInfo("Europe/Moscow")
    record = Record(
        user_id=user.id,
        operation="+",
        amount=Decimal("1500.00"),
        category="Кафе",
        created_at=datetime(2025, 1, 15, 12, 0, tzinfo=tz),
        account_id=acc.id,
    )
    session.add(record)
    await session.commit()
    await session.refresh(user)
    await session.refresh(acc)
    await session.refresh(record)
    return user, acc, record


# ==================== 1. update_record changes only passed fields ====================


@pytest.mark.asyncio
async def test_update_record_changes_only_passed_fields(session, user_and_record):
    user, acc, record = user_and_record
    original_category = record.category

    updated = await update_record(
        session, record.id, user.id, amount=Decimal("2000.00")
    )

    assert updated is not None
    assert updated.amount == Decimal("2000.00")
    assert updated.category == original_category  # unchanged


# ==================== 2. update_record does not touch another user's record ====================


@pytest.mark.asyncio
async def test_update_record_rejects_wrong_user(session, user_and_record):
    _, _, record = user_and_record
    record_id = record.id
    original_user_id = record.user_id

    other_user = User(tg_id=999, name="Other")
    session.add(other_user)
    await session.commit()
    await session.refresh(other_user)

    result = await update_record(
        session, record_id, other_user.id, amount=Decimal("9999.00")
    )

    assert result is None
    # Original unchanged
    fetched = await get_record_by_id(session, record_id, original_user_id)
    assert fetched.amount == Decimal("1500.00")


# ==================== 3. update_record rejects a foreign account ====================


@pytest.mark.asyncio
async def test_update_record_rejects_foreign_account(session, user_and_record):
    user, _, record = user_and_record
    record_id = record.id
    user_id = user.id

    other_user = User(tg_id=888, name="Other2")
    session.add(other_user)
    await session.flush()
    foreign_acc = Account(user_id=other_user.id, name="Чужой")
    session.add(foreign_acc)
    await session.commit()
    await session.refresh(foreign_acc)

    result = await update_record(session, record_id, user_id, account_id=foreign_acc.id)

    assert result is None


# ==================== 4. parse_edit_amount handles various formats ====================


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1500", Decimal("1500")),
        ("1 500", Decimal("1500")),
        ("1500,50", Decimal("1500.50")),
        ("1500.50", Decimal("1500.50")),
    ],
)
def test_parse_edit_amount_valid(text, expected):
    result = parse_edit_amount(text)
    assert result == expected


@pytest.mark.parametrize("text", ["0", "-100", "abc", "", "   "])
def test_parse_edit_amount_invalid(text):
    assert parse_edit_amount(text) is None


# ==================== 5. parse_edit_date accepts DD.MM and DD.MM.YY, rejects future ====================


def test_parse_edit_date_short_format():
    tz = "Europe/Moscow"
    now = datetime.now(ZoneInfo(tz))
    past_date = now - timedelta(days=5)
    text = past_date.strftime("%d.%m")
    result = parse_edit_date(text, tz)
    assert result is not None
    assert result.day == past_date.day
    assert result.month == past_date.month


def test_parse_edit_date_two_digit_year():
    tz = "Europe/Moscow"
    result = parse_edit_date("15.01.25", tz)
    assert result is not None
    assert result.day == 15
    assert result.month == 1


def test_parse_edit_date_rejects_future():
    tz = "Europe/Moscow"
    future = datetime.now(ZoneInfo(tz)) + timedelta(days=2)
    text = future.strftime("%d.%m.%y")
    assert parse_edit_date(text, tz) is None


# ==================== 6. format_record_card contains key fields ====================


def test_format_record_card_contains_key_fields(user_and_record):
    # use a simple mock-like object
    class FakeAccount:
        name = "Карта Сбер"

    class FakeRecord:
        id = 42
        operation = "-"
        amount = Decimal("3500.00")
        category = "Кафе"
        created_at = datetime(2025, 6, 1, 12, 0)
        account = FakeAccount()

    card = format_record_card(FakeRecord())
    assert "42" in card
    assert "3 500" in card or "3500" in card
    assert "Кафе" in card
    assert "01.06.2025" in card
    assert "Карта Сбер" in card


# ==================== 7. record_edit_field_keyboard shows account button only when has_accounts ====================


def test_record_edit_field_keyboard_with_accounts():
    kb = record_edit_field_keyboard(1, has_accounts=True)
    all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Счёт" in all_texts


def test_record_edit_field_keyboard_without_accounts():
    kb = record_edit_field_keyboard(1, has_accounts=False)
    all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Счёт" not in all_texts
