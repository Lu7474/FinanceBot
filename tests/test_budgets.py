"""Tests for Budget CRUD and alert logic."""

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
        await s.commit()

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
        await s.commit()

    # Manually set alert flags
    async with test_session() as s:
        budget = await s.scalar(select(Budget).where(Budget.user_id == user_id))
        budget.alerted_80 = True
        budget.alerted_100 = True
        await s.commit()

    # Update via set_budget — flags must reset
    async with test_session() as s:
        await set_budget(s, user_id, "Транспорт", Decimal("7000"))
        await s.commit()

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
        await s.commit()

    async with test_session() as s:
        ok = await delete_budget(s, budget_id, user_id)
        await s.commit()
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
        await s.commit()

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
        await s.commit()

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
        await s.commit()

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
        await s.commit()

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
        await s.commit()

    await _add_expense(user_id, "Кафе", Decimal("4500"))

    async with test_session() as s:
        alerts1 = await check_and_alert_budget(s, user_id, "Кафе", Decimal("4500"))
        await s.commit()
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


# ==================== Tests: N+1 query prevention ====================


@pytest.mark.asyncio
async def test_get_budget_status_no_n_plus_one(session):
    """get_budget_status must issue ≤ 2 queries for any number of budgets (1 SELECT budgets + 1 batch GROUP BY)."""
    from sqlalchemy import event

    from tests.conftest import test_engine

    user_id = await _make_user(299)
    now = datetime.now(ZoneInfo(TIMEZONE))
    async with test_session() as s:
        for cat in ("Еда", "Транспорт", "Развлечения"):
            await set_budget(s, user_id, cat, Decimal("5000"))
        await s.commit()

    await _add_expense(user_id, "Еда", Decimal("1000"))
    await _add_expense(user_id, "Транспорт", Decimal("500"))

    query_log: list[str] = []

    def _count(conn, cursor, stmt, params, ctx, executemany):
        query_log.append(stmt)

    event.listen(test_engine.sync_engine, "before_cursor_execute", _count)
    try:
        async with test_session() as s:
            status = await get_budget_status(s, user_id, now.month, now.year)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", _count)

    assert len(status) == 3
    assert len(query_log) <= 2, (
        f"N+1 detected: {len(query_log)} queries for 3 budgets. Queries:\n"
        + "\n---\n".join(query_log)
    )
