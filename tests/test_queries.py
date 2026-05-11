"""Tests for count_records, get_categories_summary, get_monthly_totals."""
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Record
from core.database.requests import (
    count_records,
    get_categories_summary,
    get_monthly_totals,
    set_user,
)

TZ = ZoneInfo("Europe/Moscow")


async def _make_user(session, tg_id: int = 1) -> int:
    user = await set_user(session, tg_id, name="Test")
    return user.id


def _rec(user_id: int, op: str, amount, cat: str, dt: datetime) -> Record:
    return Record(
        user_id=user_id,
        operation=op,
        amount=Decimal(str(amount)),
        category=cat,
        created_at=dt,
    )


# ==================== count_records ====================

@pytest.mark.asyncio
async def test_count_records_empty(session):
    user_id = await _make_user(session)
    assert await count_records(session, user_id) == 0


@pytest.mark.asyncio
async def test_count_records_all(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    for i in range(5):
        session.add(_rec(user_id, "+", 100, f"cat{i}", now))
    await session.commit()
    assert await count_records(session, user_id) == 5


@pytest.mark.asyncio
async def test_count_records_excludes_system_categories(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, "+", 100, "Обычная", now))
    session.add(_rec(user_id, "+", 100, "Перевод", now))
    session.add(_rec(user_id, "+", 100, "Установка баланса", now))
    await session.commit()
    assert await count_records(session, user_id) == 1


@pytest.mark.asyncio
async def test_count_records_with_period_month(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, "+", 100, "recent", now))
    session.add(_rec(user_id, "+", 100, "old", now - timedelta(days=40)))
    await session.commit()
    assert await count_records(session, user_id, within="month") == 1


@pytest.mark.asyncio
async def test_count_records_isolated_by_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    now = datetime.now(TZ)
    session.add(_rec(user1_id, "+", 100, "Доход", now))
    session.add(_rec(user1_id, "+", 100, "Доход", now))
    await session.commit()
    assert await count_records(session, user2_id) == 0


# ==================== get_categories_summary ====================

@pytest.mark.asyncio
async def test_get_categories_summary_sums_by_category(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, "-", 500, "Еда", now))
    session.add(_rec(user_id, "-", 300, "Еда", now))
    session.add(_rec(user_id, "-", 200, "Транспорт", now))
    await session.commit()

    summary = await get_categories_summary(session, user_id, "-", now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert summary.get("Еда") == Decimal("800")
    assert summary.get("Транспорт") == Decimal("200")


@pytest.mark.asyncio
async def test_get_categories_summary_filters_by_operation(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, "+", 1000, "Зарплата", now))
    session.add(_rec(user_id, "-", 200, "Еда", now))
    await session.commit()

    expense_summary = await get_categories_summary(session, user_id, "-", now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert "Зарплата" not in expense_summary
    assert "Еда" in expense_summary

    income_summary = await get_categories_summary(session, user_id, "+", now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert "Зарплата" in income_summary
    assert "Еда" not in income_summary


@pytest.mark.asyncio
async def test_get_categories_summary_empty(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    summary = await get_categories_summary(session, user_id, "-", now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert summary == {}


@pytest.mark.asyncio
async def test_get_categories_summary_excludes_system_categories(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, "-", 100, "Перевод", now))
    session.add(_rec(user_id, "-", 100, "Обычная", now))
    await session.commit()

    summary = await get_categories_summary(session, user_id, "-", now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert "Перевод" not in summary
    assert "Обычная" in summary


# ==================== get_monthly_totals ====================

@pytest.mark.asyncio
async def test_get_monthly_totals_empty(session):
    user_id = await _make_user(session)
    result = await get_monthly_totals(session, user_id, "+")
    assert result == []


@pytest.mark.asyncio
async def test_get_monthly_totals_current_month_sum(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    dt = now.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    session.add(_rec(user_id, "+", 3000, "Зарплата", dt))
    session.add(_rec(user_id, "+", 2000, "Бонус", dt))
    session.add(_rec(user_id, "-", 500, "Расход", dt))
    await session.commit()

    result = await get_monthly_totals(session, user_id, "+")
    row = next((r for r in result if r[0] == now.year and r[1] == now.month), None)
    assert row is not None
    assert row[2] == Decimal("5000")


@pytest.mark.asyncio
async def test_get_monthly_totals_sorted_ascending(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    for offset in [60, 0, 30]:
        dt = (now - timedelta(days=offset)).replace(day=15, hour=12, minute=0, second=0, microsecond=0)
        session.add(_rec(user_id, "+", 1000, "Доход", dt))
    await session.commit()

    result = await get_monthly_totals(session, user_id, "+")
    assert len(result) >= 2
    pairs = [(y, m) for y, m, _ in result]
    assert pairs == sorted(pairs)


@pytest.mark.asyncio
async def test_get_monthly_totals_excludes_system_categories(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    dt = now.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    session.add(_rec(user_id, "+", 9999, "Перевод", dt))
    session.add(_rec(user_id, "+", 1000, "Зарплата", dt))
    await session.commit()

    result = await get_monthly_totals(session, user_id, "+")
    row = next((r for r in result if r[0] == now.year and r[1] == now.month), None)
    assert row is not None
    assert row[2] == Decimal("1000")


@pytest.mark.asyncio
async def test_get_monthly_totals_isolated_by_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    now = datetime.now(TZ)
    dt = now.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    session.add(_rec(user1_id, "+", 5000, "Доход", dt))
    await session.commit()

    result = await get_monthly_totals(session, user2_id, "+")
    assert result == []
