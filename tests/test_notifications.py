"""Tests for notification formatters and DB query functions."""
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Record, User
from core.database.requests.notifications import (
    get_daily_summary_data,
    get_monthly_summary_data,
    get_weekly_summary_data,
)
from core.database.requests.users import (
    get_last_record_date,
    get_notifiable_users,
    update_last_reminded,
    set_user,
)
from core.scheduler import (
    format_daily_summary,
    format_monthly_summary,
    format_weekly_summary,
)


# ==================== Helpers ====================


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


_NOW = datetime(2025, 5, 14, 15, 0, 0)  # Wednesday
_WEEK_START = datetime(2025, 5, 8, 0, 0, 0)   # Monday
_WEEK_END   = datetime(2025, 5, 14, 23, 59, 59, 999999)  # Sunday


# ==================== format_weekly_summary ====================


def test_format_weekly_empty():
    assert format_weekly_summary({}) == ""


def test_format_weekly_same_month():
    data = {
        "week_start": date(2025, 5, 8),
        "week_end": date(2025, 5, 14),
        "income": Decimal("5000"),
        "expense": Decimal("3000"),
        "top_categories": [("Еда", Decimal("1500")), ("Транспорт", Decimal("1000"))],
        "prev_expense": Decimal("2500"),
    }
    text = format_weekly_summary(data)
    assert "8–14 мая" in text
    assert "5 000" in text  # income
    assert "3 000" in text  # expense
    assert "Еда" in text
    assert "+500" in text   # delta vs prev week


def test_format_weekly_cross_month():
    data = {
        "week_start": date(2025, 4, 28),
        "week_end": date(2025, 5, 4),
        "income": Decimal("0"),
        "expense": Decimal("1000"),
        "top_categories": [],
        "prev_expense": Decimal("1000"),
    }
    text = format_weekly_summary(data)
    assert "апр" in text.lower() or "28" in text
    assert "без изменений" in text


def test_format_weekly_negative_delta():
    data = {
        "week_start": date(2025, 5, 8),
        "week_end": date(2025, 5, 14),
        "income": Decimal("0"),
        "expense": Decimal("1000"),
        "top_categories": [],
        "prev_expense": Decimal("2000"),
    }
    text = format_weekly_summary(data)
    assert "−" in text  # saved 1000 vs prev week


def test_format_weekly_escapes_html():
    data = {
        "week_start": date(2025, 5, 8),
        "week_end": date(2025, 5, 14),
        "income": Decimal("0"),
        "expense": Decimal("500"),
        "top_categories": [("<script>", Decimal("500"))],
        "prev_expense": Decimal("0"),
    }
    text = format_weekly_summary(data)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


# ==================== format_monthly_summary ====================


def test_format_monthly_empty():
    assert format_monthly_summary({}, {}, 5, 2025) == ""


def test_format_monthly_no_prev_plain_text():
    curr = {"income": Decimal("10000"), "expense": Decimal("7000"), "top_categories": []}
    text = format_monthly_summary(curr, {}, 5, 2025)
    assert "<pre>" not in text
    assert "10 000" in text
    assert "7 000" in text
    assert "Май" in text


def test_format_monthly_with_prev_html_table():
    curr = {
        "income": Decimal("10000"),
        "expense": Decimal("7000"),
        "top_categories": [("Еда", Decimal("3000"))],
    }
    prev = {"income": Decimal("8000"), "expense": Decimal("9000"), "top_categories": []}
    text = format_monthly_summary(curr, prev, 5, 2025)
    assert "<pre>" in text
    assert "Доходы" in text
    assert "Расходы" in text
    assert "Еда" in text


def test_format_monthly_with_prev_markers():
    curr = {"income": Decimal("12000"), "expense": Decimal("6000"), "top_categories": []}
    prev = {"income": Decimal("10000"), "expense": Decimal("8000"), "top_categories": []}
    text = format_monthly_summary(curr, prev, 5, 2025)
    # income up → ✅, expense down → ✅
    assert text.count("✅") >= 2


# ==================== format_daily_summary ====================


def test_format_daily_empty():
    assert format_daily_summary({}) == ""


def test_format_daily_only_expenses():
    data = {
        "date": date(2025, 5, 14),
        "expense_by_cat": [("Еда", Decimal("500")), ("Кафе", Decimal("300"))],
        "total_income": Decimal("0"),
        "total_expense": Decimal("800"),
        "month_total_expense": Decimal("5000"),
    }
    text = format_daily_summary(data)
    assert "Еда" in text
    assert "Кафе" in text
    assert "800" in text
    assert "5 000" in text
    assert "Доходы" not in text  # no income line


def test_format_daily_with_income():
    data = {
        "date": date(2025, 5, 14),
        "expense_by_cat": [],
        "total_income": Decimal("3000"),
        "total_expense": Decimal("0"),
        "month_total_expense": Decimal("0"),
    }
    text = format_daily_summary(data)
    assert "Доходы" in text
    assert "3 000" in text


def test_format_daily_balance_sign():
    data = {
        "date": date(2025, 5, 14),
        "expense_by_cat": [("Кафе", Decimal("200"))],
        "total_income": Decimal("1000"),
        "total_expense": Decimal("200"),
        "month_total_expense": Decimal("200"),
    }
    text = format_daily_summary(data)
    assert "+" in text  # positive balance


# ==================== get_weekly_summary_data ====================


@pytest.mark.asyncio
async def test_get_weekly_no_records(session):
    user_id = await _make_user(session)
    data = await get_weekly_summary_data(session, user_id, _WEEK_START, _WEEK_END)
    assert data == {}


@pytest.mark.asyncio
async def test_get_weekly_income_and_expense(session):
    user_id = await _make_user(session)
    mid_week = datetime(2025, 5, 10, 12, 0, 0)
    session.add(_rec(user_id, "+", 5000, "Зарплата", mid_week))
    session.add(_rec(user_id, "-", 1200, "Еда", mid_week))
    session.add(_rec(user_id, "-", 800, "Транспорт", mid_week))
    await session.commit()

    data = await get_weekly_summary_data(session, user_id, _WEEK_START, _WEEK_END)
    assert data["income"] == Decimal("5000")
    assert data["expense"] == Decimal("2000")
    assert len(data["top_categories"]) == 2
    # sorted by amount desc
    assert data["top_categories"][0][0] == "Еда"


@pytest.mark.asyncio
async def test_get_weekly_excludes_system_categories(session):
    user_id = await _make_user(session)
    mid_week = datetime(2025, 5, 10, 12, 0, 0)
    session.add(_rec(user_id, "-", 9999, "Перевод", mid_week))
    session.add(_rec(user_id, "-", 9999, "Установка баланса", mid_week))
    await session.commit()

    data = await get_weekly_summary_data(session, user_id, _WEEK_START, _WEEK_END)
    assert data == {}


@pytest.mark.asyncio
async def test_get_weekly_prev_expense(session):
    user_id = await _make_user(session)
    mid_week = datetime(2025, 5, 10, 12, 0, 0)
    prev_week = mid_week - timedelta(days=7)
    session.add(_rec(user_id, "-", 1000, "Еда", mid_week))
    session.add(_rec(user_id, "-", 3000, "Еда", prev_week))
    await session.commit()

    data = await get_weekly_summary_data(session, user_id, _WEEK_START, _WEEK_END)
    assert data["expense"] == Decimal("1000")
    assert data["prev_expense"] == Decimal("3000")


@pytest.mark.asyncio
async def test_get_weekly_top5_limit(session):
    user_id = await _make_user(session)
    mid_week = datetime(2025, 5, 10, 12, 0, 0)
    for i in range(7):
        session.add(_rec(user_id, "-", 100 + i, f"cat{i}", mid_week))
    await session.commit()

    data = await get_weekly_summary_data(session, user_id, _WEEK_START, _WEEK_END)
    assert len(data["top_categories"]) == 5


# ==================== get_monthly_summary_data ====================


@pytest.mark.asyncio
async def test_get_monthly_no_records(session):
    user_id = await _make_user(session)
    data = await get_monthly_summary_data(session, user_id, 5, 2025)
    assert data == {}


@pytest.mark.asyncio
async def test_get_monthly_correct_month(session):
    user_id = await _make_user(session)
    in_month = datetime(2025, 5, 15, 12, 0, 0)
    out_month = datetime(2025, 4, 30, 23, 59, 59)
    session.add(_rec(user_id, "-", 1000, "Еда", in_month))
    session.add(_rec(user_id, "-", 500, "Кафе", out_month))
    await session.commit()

    data = await get_monthly_summary_data(session, user_id, 5, 2025)
    assert data["expense"] == Decimal("1000")


@pytest.mark.asyncio
async def test_get_monthly_december_boundary(session):
    user_id = await _make_user(session)
    dec = datetime(2025, 12, 31, 23, 0, 0)
    jan = datetime(2026, 1, 1, 0, 0, 0)
    session.add(_rec(user_id, "-", 999, "Еда", dec))
    session.add(_rec(user_id, "-", 1, "Кафе", jan))
    await session.commit()

    data = await get_monthly_summary_data(session, user_id, 12, 2025)
    assert data["expense"] == Decimal("999")


# ==================== get_daily_summary_data ====================


@pytest.mark.asyncio
async def test_get_daily_no_records(session):
    user_id = await _make_user(session)
    data = await get_daily_summary_data(session, user_id, date(2025, 5, 14))
    assert data == {}


@pytest.mark.asyncio
async def test_get_daily_expense_by_cat(session):
    user_id = await _make_user(session)
    today = datetime(2025, 5, 14, 12, 0, 0)
    session.add(_rec(user_id, "-", 500, "Еда", today))
    session.add(_rec(user_id, "-", 200, "Кафе", today))
    await session.commit()

    data = await get_daily_summary_data(session, user_id, date(2025, 5, 14))
    assert data["total_expense"] == Decimal("700")
    cats = [c for c, _ in data["expense_by_cat"]]
    assert "Еда" in cats and "Кафе" in cats


@pytest.mark.asyncio
async def test_get_daily_month_total_includes_all_days(session):
    user_id = await _make_user(session)
    day1 = datetime(2025, 5, 1, 12, 0, 0)
    day14 = datetime(2025, 5, 14, 12, 0, 0)
    session.add(_rec(user_id, "-", 1000, "Еда", day1))
    session.add(_rec(user_id, "-", 500, "Кафе", day14))
    await session.commit()

    data = await get_daily_summary_data(session, user_id, date(2025, 5, 14))
    assert data["month_total_expense"] == Decimal("1500")


@pytest.mark.asyncio
async def test_get_daily_excludes_other_days(session):
    user_id = await _make_user(session)
    today = datetime(2025, 5, 14, 12, 0, 0)
    yesterday = datetime(2025, 5, 13, 23, 59, 59)
    session.add(_rec(user_id, "-", 100, "Еда", today))
    session.add(_rec(user_id, "-", 999, "Кафе", yesterday))
    await session.commit()

    data = await get_daily_summary_data(session, user_id, date(2025, 5, 14))
    assert data["total_expense"] == Decimal("100")


# ==================== get_notifiable_users ====================


@pytest.mark.asyncio
async def test_get_notifiable_excludes_no_records(session):
    await _make_user(session, tg_id=1)
    users = await get_notifiable_users(session)
    assert users == []


@pytest.mark.asyncio
async def test_get_notifiable_includes_user_with_record(session):
    user_id = await _make_user(session, tg_id=1)
    session.add(_rec(user_id, "-", 100, "Еда", datetime(2025, 5, 14, 12, 0, 0)))
    await session.commit()

    users = await get_notifiable_users(session)
    assert len(users) == 1
    assert users[0].id == user_id


@pytest.mark.asyncio
async def test_get_notifiable_excludes_banned(session):
    user_id = await _make_user(session, tg_id=1)
    session.add(_rec(user_id, "-", 100, "Еда", datetime(2025, 5, 14, 12, 0, 0)))
    user = await session.get(User, user_id)
    user.is_banned = True
    await session.commit()

    users = await get_notifiable_users(session)
    assert users == []


# ==================== get_last_record_date ====================


@pytest.mark.asyncio
async def test_get_last_record_date_none(session):
    user_id = await _make_user(session)
    result = await get_last_record_date(session, user_id)
    assert result is None


@pytest.mark.asyncio
async def test_get_last_record_date_returns_latest(session):
    user_id = await _make_user(session)
    session.add(_rec(user_id, "-", 100, "Еда", datetime(2025, 5, 10, 12, 0, 0)))
    session.add(_rec(user_id, "-", 200, "Кафе", datetime(2025, 5, 14, 15, 0, 0)))
    session.add(_rec(user_id, "-", 50, "Транспорт", datetime(2025, 5, 12, 9, 0, 0)))
    await session.commit()

    result = await get_last_record_date(session, user_id)
    assert result == date(2025, 5, 14)


# ==================== update_last_reminded ====================


@pytest.mark.asyncio
async def test_update_last_reminded(session):
    user_id = await _make_user(session)
    user = await session.get(User, user_id)
    assert user.last_reminded_at is None

    await update_last_reminded(session, user_id)

    await session.refresh(user)
    assert user.last_reminded_at is not None


@pytest.mark.asyncio
async def test_update_last_reminded_nonexistent_user(session):
    # Should not raise
    await update_last_reminded(session, 99999)
