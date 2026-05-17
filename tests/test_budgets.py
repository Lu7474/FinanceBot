"""Tests for Budget CRUD, alert logic, and weekday report."""

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from conftest import test_session
from sqlalchemy import select

from config import TIMEZONE
from core.database.models import Budget, Record, User
from core.database.requests import (
    check_and_alert_budget,
    delete_budget,
    get_budget_status,
    get_budgets,
    get_weekday_report,
    reset_budget_alerts_if_new_month,
    set_budget,
)

# ==================== Helpers ====================


async def _make_user(tg_id: int = 200) -> int:
    async with test_session() as s:
        user = User(tg_id=tg_id, name="BudgetTest")
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


async def _add_expense(
    user_id: int, category: str, amount: Decimal, dt: datetime | None = None
) -> None:
    async with test_session() as s:
        now = dt or datetime.now(ZoneInfo(TIMEZONE))
        s.add(
            Record(
                user_id=user_id,
                operation="-",
                amount=amount,
                category=category,
                created_at=now,
            )
        )
        await s.commit()


# ==================== Tests: set_budget ====================


@pytest.mark.asyncio
async def test_set_budget_creates(session):
    user_id = await _make_user(201)
    async with test_session() as s:
        await set_budget(s, user_id, "Еда", Decimal("10000"))

    async with test_session() as s:
        budgets = await get_budgets(s, user_id)
    assert len(budgets) == 1
    assert budgets[0].category == "Еда"
    assert budgets[0].amount == Decimal("10000")


@pytest.mark.asyncio
async def test_set_budget_upsert_resets_flags(session):
    user_id = await _make_user(202)

    async with test_session() as s:
        await set_budget(s, user_id, "Транспорт", Decimal("5000"))

    # Manually set alert flags
    async with test_session() as s:
        budget = await s.scalar(select(Budget).where(Budget.user_id == user_id))
        budget.alerted_80 = True
        budget.alerted_100 = True
        await s.commit()

    # Update via set_budget — flags must reset
    async with test_session() as s:
        await set_budget(s, user_id, "Транспорт", Decimal("7000"))

    async with test_session() as s:
        budget = await s.scalar(select(Budget).where(Budget.user_id == user_id))
    assert budget.amount == Decimal("7000")
    assert budget.alerted_80 is False
    assert budget.alerted_100 is False


# ==================== Tests: delete_budget ====================


@pytest.mark.asyncio
async def test_delete_budget(session):
    user_id = await _make_user(203)
    async with test_session() as s:
        await set_budget(s, user_id, "Кафе", Decimal("3000"))
        budgets = await get_budgets(s, user_id)
    budget_id = budgets[0].id

    async with test_session() as s:
        ok = await delete_budget(s, budget_id, user_id)
    assert ok is True

    async with test_session() as s:
        budgets = await get_budgets(s, user_id)
    assert len(budgets) == 0


@pytest.mark.asyncio
async def test_delete_budget_wrong_user(session):
    user_id = await _make_user(204)
    other_id = await _make_user(205)
    async with test_session() as s:
        await set_budget(s, user_id, "Здоровье", Decimal("2000"))
        budgets = await get_budgets(s, user_id)
    budget_id = budgets[0].id

    async with test_session() as s:
        ok = await delete_budget(s, budget_id, other_id)
    assert ok is False

    async with test_session() as s:
        budgets = await get_budgets(s, user_id)
    assert len(budgets) == 1


# ==================== Tests: get_budget_status ====================


@pytest.mark.asyncio
async def test_get_budget_status(session):
    user_id = await _make_user(206)
    now = datetime.now(ZoneInfo(TIMEZONE))

    async with test_session() as s:
        await set_budget(s, user_id, "Еда", Decimal("10000"))

    await _add_expense(user_id, "Еда", Decimal("4000"))

    async with test_session() as s:
        status = await get_budget_status(s, user_id, now.month, now.year)

    assert len(status) == 1
    row = status[0]
    assert row["category"] == "Еда"
    assert row["spent"] == Decimal("4000")
    assert row["limit"] == Decimal("10000")
    assert row["pct"] == 40


# ==================== Tests: check_and_alert_budget ====================


@pytest.mark.asyncio
async def test_alert_at_80_percent(session):
    user_id = await _make_user(207)
    async with test_session() as s:
        await set_budget(s, user_id, "Развлечения", Decimal("10000"))

    await _add_expense(user_id, "Развлечения", Decimal("8000"))

    async with test_session() as s:
        alerts = await check_and_alert_budget(
            s, user_id, "Развлечения", Decimal("8000")
        )
    assert len(alerts) == 1
    assert "80%" in alerts[0]


@pytest.mark.asyncio
async def test_alert_at_100_percent(session):
    user_id = await _make_user(208)
    async with test_session() as s:
        await set_budget(s, user_id, "Связь", Decimal("1000"))

    await _add_expense(user_id, "Связь", Decimal("1100"))

    async with test_session() as s:
        alerts = await check_and_alert_budget(s, user_id, "Связь", Decimal("1100"))
    assert len(alerts) == 1
    assert "превышен" in alerts[0]


@pytest.mark.asyncio
async def test_alert_not_repeated(session):
    user_id = await _make_user(209)
    async with test_session() as s:
        await set_budget(s, user_id, "Кафе", Decimal("5000"))

    await _add_expense(user_id, "Кафе", Decimal("4500"))

    async with test_session() as s:
        alerts1 = await check_and_alert_budget(s, user_id, "Кафе", Decimal("4500"))
    assert len(alerts1) == 1

    # Second call — flag already set, no repeat
    async with test_session() as s:
        alerts2 = await check_and_alert_budget(s, user_id, "Кафе", Decimal("100"))
    assert len(alerts2) == 0


@pytest.mark.asyncio
async def test_no_alert_without_budget(session):
    user_id = await _make_user(210)
    await _add_expense(user_id, "Прочее", Decimal("5000"))
    async with test_session() as s:
        alerts = await check_and_alert_budget(s, user_id, "Прочее", Decimal("5000"))
    assert alerts == []


# ==================== Tests: reset_budget_alerts_if_new_month ====================


@pytest.mark.asyncio
async def test_reset_alerts_new_month(session):
    user_id = await _make_user(211)
    async with test_session() as s:
        await set_budget(s, user_id, "Еда", Decimal("10000"))
        budget = await s.scalar(select(Budget).where(Budget.user_id == user_id))
        budget.alerted_80 = True
        budget.alerted_100 = True
        budget.last_reset_month = 202501  # January 2025
        await s.commit()

    async with test_session() as s:
        budget = await s.scalar(select(Budget).where(Budget.user_id == user_id))
        reset = await reset_budget_alerts_if_new_month(s, budget)
        await s.commit()

    assert reset is True

    async with test_session() as s:
        budget = await s.scalar(select(Budget).where(Budget.user_id == user_id))
    assert budget.alerted_80 is False
    assert budget.alerted_100 is False


# ==================== Tests: get_weekday_report ====================


@pytest.mark.asyncio
async def test_weekday_report_grouping(session):
    user_id = await _make_user(212)
    now = datetime.now(ZoneInfo(TIMEZONE))

    # Monday = weekday() == 0
    from datetime import timedelta

    today = now.date()
    days_since_monday = today.weekday()
    last_monday = today - timedelta(days=days_since_monday)
    monday_dt = datetime(
        last_monday.year,
        last_monday.month,
        last_monday.day,
        0,
        0,
        tzinfo=ZoneInfo(TIMEZONE),
    )

    await _add_expense(user_id, "Еда", Decimal("500"), monday_dt)
    await _add_expense(user_id, "Еда", Decimal("300"), monday_dt)

    async with test_session() as s:
        date_from = datetime(now.year, now.month, 1, tzinfo=ZoneInfo(TIMEZONE))
        date_to = now
        data = await get_weekday_report(s, user_id, "-", date_from, date_to)

    assert 0 in data  # Monday key exists
    assert data[0] == Decimal("800")  # 500 + 300


@pytest.mark.asyncio
async def test_weekday_report_zero_days(session):
    user_id = await _make_user(213)
    now = datetime.now(ZoneInfo(TIMEZONE))

    async with test_session() as s:
        date_from = datetime(now.year, now.month, 1, tzinfo=ZoneInfo(TIMEZONE))
        date_to = now
        data = await get_weekday_report(s, user_id, "-", date_from, date_to)

    assert len(data) == 7
    assert all(v == Decimal("0") for v in data.values())
