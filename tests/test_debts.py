"""Tests for debt CRUD, partial payments, reminder selection, cascade deletes."""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from conftest import test_session

from core.database.models import Debt, DebtPayment, User, moscow_now
from core.database.requests import (
    add_payment,
    count_closed_debts,
    create_debt,
    delete_debt,
    get_active_debts,
    get_closed_debts,
    get_debt,
    get_debt_payments,
    get_debts_to_remind,
)
from core.exceptions import (
    DebtAlreadyClosed,
    DebtNotFound,
    PaymentExceedsRemaining,
)

# ==================== Helpers ====================


async def _make_user(tg_id: int, notify_debts: bool = True) -> int:
    async with test_session() as s:
        user = User(tg_id=tg_id, name=f"DebtTest{tg_id}", notify_debts=notify_debts)
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


async def _make_debt(
    user_id: int,
    direction: str = "I",
    person: str = "Петя",
    amount: Decimal = Decimal("5000"),
    due_date=None,
    description: str | None = None,
) -> int:
    async with test_session() as s:
        debt = await create_debt(
            s, user_id, direction, person, amount, description, due_date
        )
        debt_id = debt.id
        await s.commit()
        return debt_id


# ==================== Create ====================


@pytest.mark.asyncio
async def test_create_debt_incoming(session):
    user_id = await _make_user(1001)
    debt_id = await _make_debt(
        user_id, direction="I", person="Петя", amount=Decimal("5000")
    )

    async with test_session() as s:
        d = await get_debt(s, debt_id, user_id)
        assert d.direction == "I"
        assert d.person_name == "Петя"
        assert d.amount == Decimal("5000")
        assert d.remaining == Decimal("5000")
        assert d.is_closed is False


@pytest.mark.asyncio
async def test_create_debt_outgoing(session):
    user_id = await _make_user(1002)
    due = date.today() + timedelta(days=30)
    debt_id = await _make_debt(
        user_id, direction="O", person="Банк", amount=Decimal("15000"), due_date=due
    )

    async with test_session() as s:
        d = await get_debt(s, debt_id, user_id)
        assert d.direction == "O"
        assert d.person_name == "Банк"
        assert d.due_date == due


@pytest.mark.asyncio
async def test_create_debt_validates_amount(session):
    user_id = await _make_user(1003)
    async with test_session() as s:
        with pytest.raises(ValueError):
            await create_debt(s, user_id, "I", "X", Decimal("0"), None, None)


# ==================== Partial payment ====================


@pytest.mark.asyncio
async def test_partial_payment_reduces_remaining(session):
    user_id = await _make_user(1004)
    debt_id = await _make_debt(user_id, amount=Decimal("5000"))

    async with test_session() as s:
        debt, just_closed = await add_payment(
            s, debt_id, user_id, Decimal("1500"), "наличные"
        )
        assert just_closed is False
        assert debt.remaining == Decimal("3500")
        assert debt.is_closed is False
        await s.commit()

    async with test_session() as s:
        payments = await get_debt_payments(s, debt_id)
        assert len(payments) == 1
        assert payments[0].amount == Decimal("1500")
        assert payments[0].note == "наличные"


@pytest.mark.asyncio
async def test_multiple_partial_payments(session):
    user_id = await _make_user(1005)
    debt_id = await _make_debt(user_id, amount=Decimal("10000"))

    for amount in (Decimal("1000"), Decimal("2000"), Decimal("500")):
        async with test_session() as s:
            await add_payment(s, debt_id, user_id, amount, None)
            await s.commit()

    async with test_session() as s:
        d = await get_debt(s, debt_id, user_id)
        assert d.remaining == Decimal("6500")
        assert d.is_closed is False
        payments = await get_debt_payments(s, debt_id)
        assert len(payments) == 3


# ==================== Auto-close ====================


@pytest.mark.asyncio
async def test_payment_equal_remaining_closes_debt(session):
    user_id = await _make_user(1006)
    debt_id = await _make_debt(user_id, amount=Decimal("3000"))

    async with test_session() as s:
        debt, just_closed = await add_payment(
            s, debt_id, user_id, Decimal("3000"), None
        )
        assert just_closed is True
        assert debt.is_closed is True
        assert debt.closed_at is not None
        assert debt.remaining == Decimal("0")
        await s.commit()


@pytest.mark.asyncio
async def test_payment_after_partial_closes_debt(session):
    user_id = await _make_user(1007)
    debt_id = await _make_debt(user_id, amount=Decimal("3000"))

    async with test_session() as s:
        _, jc1 = await add_payment(s, debt_id, user_id, Decimal("1000"), None)
        await s.commit()
        assert jc1 is False

    async with test_session() as s:
        debt, jc2 = await add_payment(s, debt_id, user_id, Decimal("2000"), None)
        assert jc2 is True
        assert debt.is_closed is True
        await s.commit()


# ==================== Guard rails ====================


@pytest.mark.asyncio
async def test_payment_exceeds_remaining(session):
    user_id = await _make_user(1008)
    debt_id = await _make_debt(user_id, amount=Decimal("1000"))

    async with test_session() as s:
        with pytest.raises(PaymentExceedsRemaining):
            await add_payment(s, debt_id, user_id, Decimal("2000"), None)


@pytest.mark.asyncio
async def test_payment_on_closed_debt_raises(session):
    user_id = await _make_user(1009)
    debt_id = await _make_debt(user_id, amount=Decimal("100"))

    async with test_session() as s:
        await add_payment(s, debt_id, user_id, Decimal("100"), None)
        await s.commit()

    async with test_session() as s:
        with pytest.raises(DebtAlreadyClosed):
            await add_payment(s, debt_id, user_id, Decimal("1"), None)


@pytest.mark.asyncio
async def test_get_debt_not_found_raises(session):
    user_id = await _make_user(1010)
    async with test_session() as s:
        with pytest.raises(DebtNotFound):
            await get_debt(s, 99999, user_id)


# ==================== Cascades ====================


@pytest.mark.asyncio
async def test_delete_debt_cascades_payments(session):
    user_id = await _make_user(1011)
    debt_id = await _make_debt(user_id, amount=Decimal("1000"))

    async with test_session() as s:
        await add_payment(s, debt_id, user_id, Decimal("100"), None)
        await s.commit()

    async with test_session() as s:
        await delete_debt(s, debt_id, user_id)
        await s.commit()

    async with test_session() as s:
        result = await s.execute(
            select(DebtPayment).where(DebtPayment.debt_id == debt_id)
        )
        assert result.first() is None


@pytest.mark.asyncio
async def test_delete_user_cascades_debts(session):
    user_id = await _make_user(1012)
    debt_id = await _make_debt(user_id)

    async with test_session() as s:
        await add_payment(s, debt_id, user_id, Decimal("100"), None)
        await s.commit()

    async with test_session() as s:
        user = await s.get(User, user_id)
        await s.delete(user)
        await s.commit()

    async with test_session() as s:
        res = await s.execute(select(Debt).where(Debt.id == debt_id))
        assert res.first() is None
        res2 = await s.execute(
            select(DebtPayment).where(DebtPayment.debt_id == debt_id)
        )
        assert res2.first() is None


# ==================== Listings ====================


@pytest.mark.asyncio
async def test_active_and_archive_separation(session):
    user_id = await _make_user(1013)
    active_id = await _make_debt(user_id, amount=Decimal("500"))
    closed_id = await _make_debt(user_id, amount=Decimal("100"))

    async with test_session() as s:
        await add_payment(s, closed_id, user_id, Decimal("100"), None)
        await s.commit()

    async with test_session() as s:
        active = await get_active_debts(s, user_id)
        assert [d.id for d in active] == [active_id]
        archive = await get_closed_debts(s, user_id, limit=10, offset=0)
        assert [d.id for d in archive] == [closed_id]
        assert await count_closed_debts(s, user_id) == 1


# ==================== Reminders ====================


@pytest.mark.asyncio
async def test_reminder_picks_tomorrow_and_today(session):
    user_id = await _make_user(2001)
    today = moscow_now().date()

    await _make_debt(user_id, person="Tomorrow", due_date=today + timedelta(days=1))
    await _make_debt(user_id, person="Today", due_date=today)
    await _make_debt(user_id, person="FutureFar", due_date=today + timedelta(days=10))
    await _make_debt(user_id, person="NoDate", due_date=None)

    async with test_session() as s:
        pairs = await get_debts_to_remind(s, today)
        names = sorted(d.person_name for d, _ in pairs)
        assert names == ["Today", "Tomorrow"]


@pytest.mark.asyncio
async def test_reminder_overdue_after_7_days(session):
    user_id = await _make_user(2002)
    today = moscow_now().date()

    fresh_id = await _make_debt(
        user_id, person="FreshOverdue", due_date=today - timedelta(days=2)
    )
    stale_id = await _make_debt(
        user_id, person="StaleOverdue", due_date=today - timedelta(days=14)
    )

    # FreshOverdue was reminded yesterday → not yet (need 7 days gap)
    async with test_session() as s:
        d = await s.get(Debt, fresh_id)
        d.last_reminded_at = moscow_now() - timedelta(days=1)
        await s.commit()

    # StaleOverdue was reminded 10 days ago → eligible again
    async with test_session() as s:
        d = await s.get(Debt, stale_id)
        d.last_reminded_at = moscow_now() - timedelta(days=10)
        await s.commit()

    async with test_session() as s:
        pairs = await get_debts_to_remind(s, today)
        names = sorted(d.person_name for d, _ in pairs)
        assert names == ["StaleOverdue"]


@pytest.mark.asyncio
async def test_reminder_overdue_exactly_7_days_eligible(session):
    """Boundary: last_reminded_at exactly 7 days ago → eligible for new reminder."""
    user_id = await _make_user(2006)
    today = moscow_now().date()
    debt_id = await _make_debt(
        user_id, person="SevenDaysAgo", due_date=today - timedelta(days=20)
    )

    async with test_session() as s:
        d = await s.get(Debt, debt_id)
        d.last_reminded_at = moscow_now() - timedelta(days=7)
        await s.commit()

    async with test_session() as s:
        pairs = await get_debts_to_remind(s, today)
        names = [d.person_name for d, _ in pairs]
        assert "SevenDaysAgo" in names


@pytest.mark.asyncio
async def test_reminder_skips_if_reminded_today(session):
    user_id = await _make_user(2003)
    today = moscow_now().date()
    debt_id = await _make_debt(user_id, person="Already", due_date=today)

    async with test_session() as s:
        d = await s.get(Debt, debt_id)
        d.last_reminded_at = moscow_now()
        await s.commit()

    async with test_session() as s:
        pairs = await get_debts_to_remind(s, today)
        assert pairs == []


@pytest.mark.asyncio
async def test_reminder_skips_when_notify_off(session):
    user_id = await _make_user(2004, notify_debts=False)
    today = moscow_now().date()
    await _make_debt(user_id, person="Off", due_date=today)

    async with test_session() as s:
        pairs = await get_debts_to_remind(s, today)
        assert pairs == []


@pytest.mark.asyncio
async def test_reminder_skips_closed_debts(session):
    user_id = await _make_user(2005)
    today = moscow_now().date()
    debt_id = await _make_debt(
        user_id, person="Closed", amount=Decimal("100"), due_date=today
    )

    async with test_session() as s:
        await add_payment(s, debt_id, user_id, Decimal("100"), None)
        await s.commit()

    async with test_session() as s:
        pairs = await get_debts_to_remind(s, today)
        assert pairs == []
