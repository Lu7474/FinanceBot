"""Tests for payment reminders: CRUD, recurrence on mark_paid, reminder selection."""

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from conftest import test_session

from core.database.models import User, moscow_now
from core.database.requests import (
    create_payment,
    delete_payment,
    get_active_payments,
    get_payment,
    get_payments_to_remind,
    mark_paid,
    update_payment,
)
from core.exceptions import PaymentNotFound
from core.utils import next_due_date

# ==================== Helpers ====================


async def _make_user(
    tg_id: int, notify_payments: bool = True, is_banned: bool = False
) -> int:
    async with test_session() as s:
        user = User(
            tg_id=tg_id,
            name=f"PayTest{tg_id}",
            notify_payments=notify_payments,
            is_banned=is_banned,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


async def _make_payment(
    user_id: int,
    title: str = "ОСАГО",
    amount: Decimal | None = Decimal("8000"),
    due_date: date | None = None,
    period: str = "none",
) -> int:
    if due_date is None:
        due_date = date(2026, 7, 1)
    async with test_session() as s:
        payment = await create_payment(s, user_id, title, amount, due_date, period)
        pid = payment.id
        await s.commit()
        return pid


# ==================== next_due_date ====================


def test_next_due_month_simple():
    assert next_due_date(date(2026, 1, 10), "month") == date(2026, 2, 10)


def test_next_due_month_end_clamp():
    # 31 Jan + 1 month → 28 Feb (2026 not leap)
    assert next_due_date(date(2026, 1, 31), "month") == date(2026, 2, 28)


def test_next_due_month_year_rollover():
    assert next_due_date(date(2026, 12, 15), "month") == date(2027, 1, 15)


def test_next_due_year_simple():
    assert next_due_date(date(2026, 7, 1), "year") == date(2027, 7, 1)


def test_next_due_year_leap_clamp():
    # 29 Feb 2024 + 1 year → 28 Feb 2025
    assert next_due_date(date(2024, 2, 29), "year") == date(2025, 2, 28)


def test_next_due_rejects_none():
    with pytest.raises(ValueError):
        next_due_date(date(2026, 1, 1), "none")


# ==================== Create / read ====================


@pytest.mark.asyncio
async def test_create_payment(session):
    user_id = await _make_user(2001)
    pid = await _make_payment(user_id, title="Налог", amount=Decimal("3500"))
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.title == "Налог"
    assert p.amount == Decimal("3500")
    assert p.is_active is True


@pytest.mark.asyncio
async def test_create_payment_floating_amount(session):
    user_id = await _make_user(2002)
    pid = await _make_payment(user_id, title="Коммуналка", amount=None, period="month")
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.amount is None
    assert p.period == "month"


@pytest.mark.asyncio
async def test_get_active_sorted_by_due(session):
    user_id = await _make_user(2003)
    await _make_payment(user_id, title="Поздний", due_date=date(2026, 9, 1))
    await _make_payment(user_id, title="Ранний", due_date=date(2026, 6, 1))
    async with test_session() as s:
        active = await get_active_payments(s, user_id)
    assert [p.title for p in active] == ["Ранний", "Поздний"]


@pytest.mark.asyncio
async def test_get_payment_not_found(session):
    user_id = await _make_user(2004)
    async with test_session() as s:
        with pytest.raises(PaymentNotFound):
            await get_payment(s, 999999, user_id)


@pytest.mark.asyncio
async def test_get_payment_ownership(session):
    owner = await _make_user(2005)
    other = await _make_user(2006)
    pid = await _make_payment(owner)
    async with test_session() as s:
        with pytest.raises(PaymentNotFound):
            await get_payment(s, pid, other)


# ==================== mark_paid ====================


@pytest.mark.asyncio
async def test_mark_paid_one_time_closes(session):
    user_id = await _make_user(2007)
    pid = await _make_payment(user_id, period="none")
    async with test_session() as s:
        payment, next_due = await mark_paid(s, pid, user_id)
        is_active = payment.is_active
        last_paid = payment.last_paid_at
        await s.commit()
    assert next_due is None
    assert is_active is False
    assert last_paid is not None


@pytest.mark.asyncio
async def test_mark_paid_monthly_rolls_forward(session):
    user_id = await _make_user(2008)
    pid = await _make_payment(user_id, due_date=date(2026, 1, 31), period="month")
    async with test_session() as s:
        payment, next_due = await mark_paid(s, pid, user_id)
        due = payment.due_date
        is_active = payment.is_active
        reminded = payment.last_reminded_at
        await s.commit()
    assert next_due == date(2026, 2, 28)
    assert due == date(2026, 2, 28)
    assert is_active is True
    assert reminded is None


@pytest.mark.asyncio
async def test_mark_paid_yearly_rolls_forward(session):
    user_id = await _make_user(2009)
    pid = await _make_payment(user_id, due_date=date(2026, 7, 1), period="year")
    async with test_session() as s:
        payment, next_due = await mark_paid(s, pid, user_id)
        is_active = payment.is_active
        await s.commit()
    assert next_due == date(2027, 7, 1)
    assert is_active is True


@pytest.mark.asyncio
async def test_mark_paid_resets_reminder(session):
    user_id = await _make_user(2010)
    pid = await _make_payment(user_id, due_date=date(2026, 1, 10), period="month")
    # simulate a prior reminder
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
        p.last_reminded_at = moscow_now()
        await s.commit()
    async with test_session() as s:
        await mark_paid(s, pid, user_id)
        await s.commit()
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.last_reminded_at is None


# ==================== Update / delete ====================


@pytest.mark.asyncio
async def test_update_payment_fields(session):
    user_id = await _make_user(2011)
    pid = await _make_payment(user_id)
    async with test_session() as s:
        await update_payment(
            s,
            pid,
            user_id,
            title="ОСАГО 2027",
            amount=Decimal("9000"),
            due_date=date(2027, 7, 1),
            period="year",
        )
        await s.commit()
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.title == "ОСАГО 2027"
    assert p.amount == Decimal("9000")
    assert p.due_date == date(2027, 7, 1)
    assert p.period == "year"


@pytest.mark.asyncio
async def test_update_clear_amount(session):
    user_id = await _make_user(2012)
    pid = await _make_payment(user_id, amount=Decimal("5000"))
    async with test_session() as s:
        await update_payment(s, pid, user_id, clear_amount=True)
        await s.commit()
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.amount is None


@pytest.mark.asyncio
async def test_delete_payment(session):
    user_id = await _make_user(2013)
    pid = await _make_payment(user_id)
    async with test_session() as s:
        await delete_payment(s, pid, user_id)
        await s.commit()
    async with test_session() as s:
        with pytest.raises(PaymentNotFound):
            await get_payment(s, pid, user_id)


@pytest.mark.asyncio
async def test_delete_payment_not_found(session):
    user_id = await _make_user(2014)
    async with test_session() as s:
        with pytest.raises(PaymentNotFound):
            await delete_payment(s, 999999, user_id)


# ==================== get_payments_to_remind ====================


@pytest.mark.asyncio
async def test_remind_due_today_and_tomorrow(session):
    user_id = await _make_user(2015)
    today = date(2026, 6, 10)
    await _make_payment(user_id, title="Сегодня", due_date=today)
    await _make_payment(user_id, title="Завтра", due_date=today + timedelta(days=1))
    await _make_payment(user_id, title="Позже", due_date=today + timedelta(days=5))
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    titles = {p.title for p, _ in pairs}
    assert titles == {"Сегодня", "Завтра"}


@pytest.mark.asyncio
async def test_remind_skips_when_notify_off(session):
    user_id = await _make_user(2016, notify_payments=False)
    today = date(2026, 6, 10)
    await _make_payment(user_id, due_date=today)
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    assert pairs == []


@pytest.mark.asyncio
async def test_remind_skips_banned(session):
    user_id = await _make_user(2017, is_banned=True)
    today = date(2026, 6, 10)
    await _make_payment(user_id, due_date=today)
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    assert pairs == []


@pytest.mark.asyncio
async def test_remind_skips_inactive(session):
    user_id = await _make_user(2018)
    today = date(2026, 6, 10)
    pid = await _make_payment(user_id, due_date=today, period="none")
    async with test_session() as s:
        await mark_paid(s, pid, user_id)  # closes one-time payment
        await s.commit()
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    assert pairs == []


@pytest.mark.asyncio
async def test_remind_dedup_same_day(session):
    user_id = await _make_user(2019)
    today = date(2026, 6, 10)
    pid = await _make_payment(user_id, due_date=today)
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
        p.last_reminded_at = moscow_now()  # already reminded today
        await s.commit()
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    assert pairs == []


@pytest.mark.asyncio
async def test_remind_overdue_weekly_window(session):
    user_id = await _make_user(2020)
    today = date(2026, 6, 10)
    # overdue, last reminded 8 days ago → eligible again
    pid = await _make_payment(user_id, due_date=today - timedelta(days=20))
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
        p.last_reminded_at = moscow_now() - timedelta(days=8)
        await s.commit()
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    assert len(pairs) == 1


@pytest.mark.asyncio
async def test_remind_overdue_within_week_skipped(session):
    user_id = await _make_user(2021)
    today = date(2026, 6, 10)
    pid = await _make_payment(user_id, due_date=today - timedelta(days=20))
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
        p.last_reminded_at = moscow_now() - timedelta(days=3)  # too soon
        await s.commit()
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    assert pairs == []
