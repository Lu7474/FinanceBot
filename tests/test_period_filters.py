"""Tests for period filters not covered elsewhere: yesterday, week, month30, prev_month."""
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Record
from core.database.requests import get_records, set_user

TZ = ZoneInfo("Europe/Moscow")


async def _make_user(session, tg_id: int = 1) -> int:
    user = await set_user(session, tg_id, name="Test")
    return user.id


def _rec(user_id: int, dt: datetime, cat: str) -> Record:
    return Record(
        user_id=user_id,
        operation="+",
        amount=Decimal("100"),
        category=cat,
        created_at=dt,
    )


# ==================== yesterday ====================

@pytest.mark.asyncio
async def test_filter_yesterday_includes_only_yesterday(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    yesterday_noon = (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    two_days_noon = (now - timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)

    session.add(_rec(user_id, yesterday_noon, "yesterday"))
    session.add(_rec(user_id, today_noon, "today"))
    session.add(_rec(user_id, two_days_noon, "two_days"))
    await session.commit()

    records = await get_records(session, user_id, "yesterday")
    categories = {r.category for r in records}
    assert "yesterday" in categories
    assert "today" not in categories
    assert "two_days" not in categories


# ==================== week ====================

@pytest.mark.asyncio
async def test_filter_week_includes_last_7_days(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, now - timedelta(days=3), "in_week"))
    session.add(_rec(user_id, now - timedelta(days=6), "in_week"))
    session.add(_rec(user_id, now - timedelta(days=8), "old"))
    await session.commit()

    records = await get_records(session, user_id, "week")
    categories = {r.category for r in records}
    assert "in_week" in categories
    assert "old" not in categories


@pytest.mark.asyncio
async def test_filter_week_includes_today(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, now, "today"))
    await session.commit()

    records = await get_records(session, user_id, "week")
    assert any(r.category == "today" for r in records)


# ==================== month30 ====================

@pytest.mark.asyncio
async def test_filter_month30_includes_last_30_days(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    session.add(_rec(user_id, now - timedelta(days=15), "in_30"))
    session.add(_rec(user_id, now - timedelta(days=29), "in_30"))
    session.add(_rec(user_id, now - timedelta(days=31), "old"))
    await session.commit()

    records = await get_records(session, user_id, "month30")
    categories = {r.category for r in records}
    assert "in_30" in categories
    assert "old" not in categories


# ==================== prev_month ====================

@pytest.mark.asyncio
async def test_filter_prev_month_includes_only_prev_month(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_prev = first_this_month - timedelta(days=1)
    mid_prev = last_prev.replace(day=15, hour=12, minute=0, second=0, microsecond=0)

    session.add(_rec(user_id, mid_prev, "prev_month"))
    session.add(_rec(user_id, now, "curr_month"))
    await session.commit()

    records = await get_records(session, user_id, "prev_month")
    categories = {r.category for r in records}
    assert "prev_month" in categories
    assert "curr_month" not in categories


@pytest.mark.asyncio
async def test_filter_prev_month_excludes_two_months_ago(session):
    user_id = await _make_user(session)
    now = datetime.now(TZ)
    first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_prev = first_this_month - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    two_months_ago = (first_prev - timedelta(days=1)).replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    mid_prev = last_prev.replace(day=15, hour=12, minute=0, second=0, microsecond=0)

    session.add(_rec(user_id, mid_prev, "prev_month"))
    session.add(_rec(user_id, two_months_ago, "two_months_ago"))
    await session.commit()

    records = await get_records(session, user_id, "prev_month")
    categories = {r.category for r in records}
    assert "prev_month" in categories
    assert "two_months_ago" not in categories
