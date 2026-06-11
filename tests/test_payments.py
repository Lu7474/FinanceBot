"""Tests for payment reminders: CRUD, recurrence on mark_paid, reminder selection."""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from conftest import test_session
from sqlalchemy import select

from core.database.models import Record, User, moscow_now
from core.database.requests import (
    add_record,
    add_user_category,
    create_account,
    create_payment,
    delete_payment,
    get_active_payments,
    get_payment,
    get_payments_to_remind,
    mark_paid,
    merge_user_categories,
    rename_user_category,
    update_payment,
)
from core.exceptions import PaymentAlreadyPaid, PaymentNotFound
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
    category: str | None = None,
) -> int:
    if due_date is None:
        due_date = date(2026, 7, 1)
    async with test_session() as s:
        payment = await create_payment(
            s, user_id, title, amount, due_date, period, category=category
        )
        pid = payment.id
        await s.commit()
        return pid


async def _user_records(user_id: int) -> list[Record]:
    async with test_session() as s:
        rows = await s.scalars(select(Record).where(Record.user_id == user_id))
        return list(rows)


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


# ==================== mark_paid idempotency (expected_due token) ====================


@pytest.mark.asyncio
async def test_mark_paid_expected_due_match_ok(session):
    user_id = await _make_user(2022)
    due = date(2026, 6, 15)
    pid = await _make_payment(user_id, due_date=due, period="month")
    async with test_session() as s:
        payment, next_due = await mark_paid(s, pid, user_id, expected_due=due)
        await s.commit()
    assert next_due == date(2026, 7, 15)


@pytest.mark.asyncio
async def test_mark_paid_double_tap_rejected(session):
    """Second tap carries the old due_date token → PaymentAlreadyPaid,
    the cycle must not roll twice."""
    user_id = await _make_user(2023)
    due = date(2026, 6, 15)
    pid = await _make_payment(user_id, due_date=due, period="month")
    async with test_session() as s:
        await mark_paid(s, pid, user_id, expected_due=due)
        await s.commit()
    async with test_session() as s:
        with pytest.raises(PaymentAlreadyPaid):
            await mark_paid(s, pid, user_id, expected_due=due)
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.due_date == date(2026, 7, 15)  # rolled exactly once


@pytest.mark.asyncio
async def test_mark_paid_double_tap_one_time_rejected(session):
    """One-time payment: second tap hits is_active=False → PaymentAlreadyPaid."""
    user_id = await _make_user(2024)
    due = date(2026, 6, 15)
    pid = await _make_payment(user_id, due_date=due, period="none")
    async with test_session() as s:
        await mark_paid(s, pid, user_id, expected_due=due)
        await s.commit()
    async with test_session() as s:
        with pytest.raises(PaymentAlreadyPaid):
            await mark_paid(s, pid, user_id, expected_due=due)


@pytest.mark.asyncio
async def test_mark_paid_without_token_keeps_old_behavior(session):
    """No expected_due → no guard (legacy callers stay valid)."""
    user_id = await _make_user(2025)
    pid = await _make_payment(user_id, due_date=date(2026, 6, 15), period="month")
    async with test_session() as s:
        await mark_paid(s, pid, user_id)
        await mark_paid(s, pid, user_id)  # rolls twice, but explicitly unguarded
        await s.commit()
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.due_date == date(2026, 8, 15)


@pytest.mark.asyncio
async def test_double_pay_creates_single_record(session):
    """Record + mark_paid share one transaction: the rejected second attempt
    must roll back its Record too."""
    user_id = await _make_user(2026)
    due = date(2026, 6, 15)
    pid = await _make_payment(
        user_id, amount=Decimal("8000"), due_date=due, period="month"
    )
    for attempt in range(2):
        try:
            async with test_session() as s:
                payment = await get_payment(s, pid, user_id)
                await add_record(
                    s,
                    user_id,
                    "-",
                    Decimal("8000"),
                    category=payment.category or "не указано",
                )
                await mark_paid(s, pid, user_id, expected_due=due)
                await s.commit()
        except PaymentAlreadyPaid:
            assert attempt == 1  # only the second tap is rejected

    records = await _user_records(user_id)
    assert len(records) == 1


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


# ==================== Category field ====================


@pytest.mark.asyncio
async def test_create_payment_with_category(session):
    user_id = await _make_user(2030)
    pid = await _make_payment(user_id, category="страховка")
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.category == "страховка"


@pytest.mark.asyncio
async def test_create_payment_without_category(session):
    user_id = await _make_user(2031)
    pid = await _make_payment(user_id)
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.category is None


@pytest.mark.asyncio
async def test_update_payment_set_category(session):
    user_id = await _make_user(2032)
    pid = await _make_payment(user_id)
    async with test_session() as s:
        await update_payment(s, pid, user_id, category="налоги")
        await s.commit()
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.category == "налоги"


@pytest.mark.asyncio
async def test_update_payment_clear_category(session):
    user_id = await _make_user(2033)
    pid = await _make_payment(user_id, category="налоги")
    async with test_session() as s:
        await update_payment(s, pid, user_id, clear_category=True)
        await s.commit()
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.category is None


@pytest.mark.asyncio
async def test_rename_category_updates_payment(session):
    """Rename must keep Payment.category in sync, same as Record.category."""
    user_id = await _make_user(2038)
    async with test_session() as s:
        cat = await add_user_category(s, user_id, "страховка", "-")
        cat_id = cat.id
        await s.commit()
    pid = await _make_payment(user_id, category="страховка")
    async with test_session() as s:
        ok = await rename_user_category(s, cat_id, user_id, "страхование")
        await s.commit()
    assert ok
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.category == "страхование"


@pytest.mark.asyncio
async def test_merge_categories_updates_payment(session):
    user_id = await _make_user(2039)
    async with test_session() as s:
        src = await add_user_category(s, user_id, "жкх", "-")
        dst = await add_user_category(s, user_id, "коммуналка", "-")
        src_id, dst_id = src.id, dst.id
        await s.commit()
    pid = await _make_payment(user_id, category="жкх")
    async with test_session() as s:
        moved = await merge_user_categories(s, src_id, dst_id, user_id)
        await s.commit()
    assert moved is not None
    async with test_session() as s:
        p = await get_payment(s, pid, user_id)
    assert p.category == "коммуналка"


# ==================== Pay → expense record (handler transaction) ====================
# Мимикрирует _record_and_finish из handlers/payments.py: запись расхода и
# mark_paid идут в одной сессии/коммите.


@pytest.mark.asyncio
async def test_pay_writes_record_and_rolls_payment(session):
    user_id = await _make_user(2034)
    pid = await _make_payment(
        user_id,
        amount=Decimal("8000"),
        due_date=date(2026, 6, 15),
        period="month",
        category="страховка",
    )
    async with test_session() as s:
        payment = await get_payment(s, pid, user_id)
        await add_record(
            s,
            user_id,
            "-",
            payment.amount,
            category=payment.category or "не указано",
            account_id=None,
        )
        payment, next_due = await mark_paid(s, pid, user_id)
        await s.commit()

    records = await _user_records(user_id)
    assert len(records) == 1
    rec = records[0]
    assert rec.operation == "-"
    assert rec.amount == Decimal("8000")
    assert rec.category == "страховка"
    assert rec.account_id is None
    assert next_due == date(2026, 7, 15)


@pytest.mark.asyncio
async def test_pay_record_gets_account_and_default_category(session):
    user_id = await _make_user(2035)
    async with test_session() as s:
        account = await create_account(s, user_id, "Карта")
        acc_id = account.id
        await s.commit()
    pid = await _make_payment(user_id, amount=Decimal("3000"), category=None)
    async with test_session() as s:
        payment = await get_payment(s, pid, user_id)
        await add_record(
            s,
            user_id,
            "-",
            payment.amount,
            category=payment.category or "не указано",
            account_id=acc_id,
        )
        await mark_paid(s, pid, user_id)
        await s.commit()

    records = await _user_records(user_id)
    assert len(records) == 1
    assert records[0].category == "не указано"
    assert records[0].account_id == acc_id


@pytest.mark.asyncio
async def test_pay_floating_amount_uses_entered_value(session):
    user_id = await _make_user(2036)
    pid = await _make_payment(user_id, amount=None, period="month")
    entered = Decimal("4321.50")  # пользователь ввёл фактическую сумму
    async with test_session() as s:
        payment = await get_payment(s, pid, user_id)
        await add_record(
            s, user_id, "-", entered, category=payment.category or "не указано"
        )
        await mark_paid(s, pid, user_id)
        await s.commit()

    records = await _user_records(user_id)
    assert len(records) == 1
    assert records[0].amount == Decimal("4321.50")


@pytest.mark.asyncio
async def test_pay_skip_record_keeps_balance_untouched(session):
    """«Нет» в подтверждении: mark_paid без записи — старое поведение."""
    user_id = await _make_user(2037)
    pid = await _make_payment(user_id, amount=Decimal("8000"), period="month")
    async with test_session() as s:
        payment, next_due = await mark_paid(s, pid, user_id)
        await s.commit()

    assert await _user_records(user_id) == []
    assert next_due is not None


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
        # Anchored to the fixed `today`, not real now: already reminded today
        p.last_reminded_at = datetime(2026, 6, 10, 9, 0)
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
        p.last_reminded_at = datetime(2026, 6, 2, 9, 0)
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
        p.last_reminded_at = datetime(2026, 6, 7, 9, 0)  # too soon
        await s.commit()
    async with test_session() as s:
        pairs = await get_payments_to_remind(s, today)
    assert pairs == []
