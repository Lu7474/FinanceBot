"""Tests for admin queries: cascade delete, ban, stats, top users, CSV export."""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from config import TIMEZONE
from core.database.models import (
    Account,
    Budget,
    CategoryKeyword,
    Goal,
    GoalDeposit,
    Record,
    SavingsItem,
    SavingsSnapshot,
    User,
    UserCategory,
    WealthItem,
)
from core.database.requests.admin import (
    ban_user,
    count_users,
    delete_user_cascade,
    find_users_by_name,
    get_active_user_tg_ids,
    get_all_tg_ids,
    get_all_users,
    get_bot_stats,
    get_power_user_tg_ids,
    get_top_users,
    get_user_records_csv,
    get_user_stats,
)
from tests.conftest import test_session

# ---------- helpers ----------


async def _make_user(session, tg_id: int = 1, name: str = "u", banned: bool = False) -> tuple[int, int]:
    """Returns (user_pk, tg_id) — pulled before next commit expires the row."""
    user = User(tg_id=tg_id, name=name, is_banned=banned)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user.id, user.tg_id


async def _make_record(
    session, user_id: int, op: str = "-", amount: str = "100",
    category: str = "Еда", at: datetime | None = None,
) -> None:
    rec = Record(
        user_id=user_id,
        operation=op,
        amount=Decimal(amount),
        category=category,
        created_at=at or datetime.now(ZoneInfo(TIMEZONE)),
    )
    session.add(rec)
    await session.commit()


# ---------- delete_user_cascade ----------


@pytest.mark.asyncio
async def test_delete_user_cascade_removes_user(session):
    user_pk, _ = await _make_user(session, tg_id=42)
    ok = await delete_user_cascade(session, tg_id=42)
    assert ok is True
    remaining = await session.scalar(select(User).where(User.id == user_pk))
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_user_cascade_returns_false_for_unknown(session):
    ok = await delete_user_cascade(session, tg_id=999)
    assert ok is False


@pytest.mark.asyncio
async def test_delete_user_cascade_removes_all_related_data():
    """Isolated-session test: setup, delete, and verify each in a separate session.

    Mirrors production: admin handler opens a fresh session for delete_user_cascade,
    so identity map is empty and any reliance on ORM-cascade shortcuts would fail here.
    """
    # ----- setup session -----
    async with test_session() as s:
        user = User(tg_id=7, name="u")
        s.add(user)
        await s.commit()
        await s.refresh(user)
        uid = user.id

        s.add(Account(user_id=uid, name="Наличные"))
        s.add(Record(user_id=uid, operation="-", amount=Decimal("10"), category="Еда"))
        s.add(Budget(user_id=uid, category="Еда", amount=Decimal("1000")))
        cat = UserCategory(user_id=uid, name="Еда", cat_type="-")
        s.add(cat)
        await s.flush()
        s.add(CategoryKeyword(user_id=uid, category_id=cat.id, keyword="хлеб"))
        s.add(WealthItem(user_id=uid, type="A", name="Квартира", amount=Decimal("1000000")))
        snap = SavingsSnapshot(user_id=uid, date=datetime.now(ZoneInfo(TIMEZONE)).date())
        s.add(snap)
        await s.flush()
        s.add(SavingsItem(snapshot_id=snap.id, name="Наличные", amount=Decimal("500")))
        goal = Goal(user_id=uid, name="Машина", target_amount=Decimal("500000"))
        s.add(goal)
        await s.flush()
        s.add(GoalDeposit(goal_id=goal.id, amount=Decimal("1000")))
        await s.commit()

    # ----- delete session (fresh, empty identity map) -----
    async with test_session() as s:
        ok = await delete_user_cascade(s, tg_id=7)
        assert ok is True
        await s.commit()

    # ----- verify session (also fresh) -----
    async with test_session() as s:
        for model in (Account, Record, Budget, UserCategory, CategoryKeyword,
                      WealthItem, SavingsSnapshot, Goal):
            rows = (await s.execute(select(model).where(model.user_id == uid))).scalars().all()
            assert rows == [], f"{model.__name__} not cleaned"

        assert (await s.execute(select(SavingsItem))).scalars().all() == []
        assert (await s.execute(select(GoalDeposit))).scalars().all() == []
        assert (await s.execute(select(User).where(User.id == uid))).scalar_one_or_none() is None


# ---------- ban_user ----------


@pytest.mark.asyncio
async def test_ban_user_sets_flag(session):
    await _make_user(session, tg_id=10)
    ok = await ban_user(session, tg_id=10, is_banned=True)
    assert ok is True
    user = await session.scalar(select(User).where(User.tg_id == 10))
    assert user.is_banned is True


@pytest.mark.asyncio
async def test_ban_user_unbans(session):
    await _make_user(session, tg_id=11, banned=True)
    ok = await ban_user(session, tg_id=11, is_banned=False)
    assert ok is True
    user = await session.scalar(select(User).where(User.tg_id == 11))
    assert user.is_banned is False


@pytest.mark.asyncio
async def test_ban_user_returns_false_for_unknown(session):
    ok = await ban_user(session, tg_id=9999, is_banned=True)
    assert ok is False


# ---------- get_bot_stats ----------


@pytest.mark.asyncio
async def test_get_bot_stats_empty(session):
    stats = await get_bot_stats(session)
    assert stats["total_users"] == 0
    assert stats["banned_users"] == 0
    assert stats["total_records"] == 0
    assert stats["new_today"] == 0
    assert stats["active_week"] == 0


@pytest.mark.asyncio
async def test_get_bot_stats_counts_correctly(session):
    u1_id, _ = await _make_user(session, tg_id=1, name="a")
    await _make_user(session, tg_id=2, name="b", banned=True)
    await _make_record(session, u1_id)
    await _make_record(session, u1_id, category="Перевод")  # system, must be excluded
    session.add(Account(user_id=u1_id, name="Карта"))
    await session.commit()

    stats = await get_bot_stats(session)
    assert stats["total_users"] == 2
    assert stats["banned_users"] == 1
    assert stats["total_accounts"] == 1
    assert stats["total_records"] == 1  # 'Перевод' excluded
    assert stats["new_today"] == 2
    assert stats["active_week"] == 1


# ---------- get_top_users ----------


@pytest.mark.asyncio
async def test_get_top_users_sorted_by_record_count(session):
    u1_id, _ = await _make_user(session, tg_id=1, name="a")
    u2_id, _ = await _make_user(session, tg_id=2, name="b")
    u3_id, _ = await _make_user(session, tg_id=3, name="c")
    for _ in range(3):
        await _make_record(session, u1_id)
    for _ in range(5):
        await _make_record(session, u2_id)
    await _make_record(session, u3_id)

    top = await get_top_users(session, limit=5)
    counts = [cnt for _, cnt in top]
    assert counts == sorted(counts, reverse=True)
    assert top[0][1] == 5
    assert top[0][0].tg_id == 2


@pytest.mark.asyncio
async def test_get_top_users_excludes_system_categories(session):
    uid, _ = await _make_user(session, tg_id=1)
    await _make_record(session, uid, category="Перевод")
    await _make_record(session, uid, category="Установка баланса")
    top = await get_top_users(session)
    assert top == []


# ---------- get_all_users / count_users ----------


@pytest.mark.asyncio
async def test_get_all_users_filters(session):
    await _make_user(session, tg_id=1, name="alice")
    await _make_user(session, tg_id=2, name="bob", banned=True)

    all_users = await get_all_users(session)
    assert len(all_users) == 2

    active = await get_all_users(session, filter_mode="active")
    assert [u.tg_id for u in active] == [1]

    banned = await get_all_users(session, filter_mode="banned")
    assert [u.tg_id for u in banned] == [2]


@pytest.mark.asyncio
async def test_count_users_filters(session):
    await _make_user(session, tg_id=1)
    await _make_user(session, tg_id=2, banned=True)
    await _make_user(session, tg_id=3, banned=True)

    assert await count_users(session) == 3
    assert await count_users(session, filter_mode="active") == 1
    assert await count_users(session, filter_mode="banned") == 2


# ---------- find_users_by_name ----------


@pytest.mark.asyncio
async def test_find_users_by_name_case_insensitive(session):
    await _make_user(session, tg_id=1, name="Alice")
    await _make_user(session, tg_id=2, name="Bob")
    res = await find_users_by_name(session, "ali")
    assert len(res) == 1
    assert res[0].name == "Alice"


# ---------- get_active_user_tg_ids / get_power_user_tg_ids ----------


@pytest.mark.asyncio
async def test_get_active_user_tg_ids_includes_recent(session):
    uid, _ = await _make_user(session, tg_id=1)
    await _make_record(session, uid)
    ids = await get_active_user_tg_ids(session, days=7)
    assert ids == [1]


@pytest.mark.asyncio
async def test_get_active_user_tg_ids_excludes_old(session):
    uid, _ = await _make_user(session, tg_id=1)
    old = datetime.now(ZoneInfo(TIMEZONE)) - timedelta(days=30)
    await _make_record(session, uid, at=old)
    ids = await get_active_user_tg_ids(session, days=7)
    assert ids == []


@pytest.mark.asyncio
async def test_get_active_user_tg_ids_excludes_banned(session):
    uid, _ = await _make_user(session, tg_id=1, banned=True)
    await _make_record(session, uid)
    ids = await get_active_user_tg_ids(session, days=7)
    assert ids == []


@pytest.mark.asyncio
async def test_get_power_user_tg_ids_threshold(session):
    u1_id, _ = await _make_user(session, tg_id=1)
    u2_id, _ = await _make_user(session, tg_id=2)
    for _ in range(15):
        await _make_record(session, u1_id)
    for _ in range(3):
        await _make_record(session, u2_id)

    power = await get_power_user_tg_ids(session, min_records=10)
    assert power == [1]


# ---------- get_all_tg_ids ----------


@pytest.mark.asyncio
async def test_get_all_tg_ids_skip_banned_by_default(session):
    await _make_user(session, tg_id=1)
    await _make_user(session, tg_id=2, banned=True)
    ids = await get_all_tg_ids(session)
    assert ids == [1]


@pytest.mark.asyncio
async def test_get_all_tg_ids_with_banned(session):
    await _make_user(session, tg_id=1)
    await _make_user(session, tg_id=2, banned=True)
    ids = await get_all_tg_ids(session, skip_banned=False)
    assert sorted(ids) == [1, 2]


# ---------- get_user_stats ----------


@pytest.mark.asyncio
async def test_get_user_stats_aggregates(session):
    uid, _ = await _make_user(session, tg_id=1)
    await _make_record(session, uid, op="+", amount="1000", category="Зарплата")
    await _make_record(session, uid, op="-", amount="200", category="Еда")
    await _make_record(session, uid, op="-", amount="100", category="Перевод")  # system

    stats = await get_user_stats(session, user_id=uid)
    assert stats["total_records"] == 2  # system excluded
    assert stats["income_count"] == 1
    assert stats["expense_count"] == 1
    assert stats["income_sum"] == Decimal("1000")
    assert stats["expense_sum"] == Decimal("200")


# ---------- get_user_records_csv ----------


@pytest.mark.asyncio
async def test_get_user_records_csv_has_bom_and_headers(session):
    uid, _ = await _make_user(session, tg_id=1)
    await _make_record(session, uid, op="-", amount="500", category="Еда")
    raw = await get_user_records_csv(session, user_id=uid)

    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = raw.decode("utf-8-sig")
    assert "Дата" in text and "Тип" in text and "Категория" in text and "Счёт" in text
    assert "Расход" in text
    assert "500" in text
    assert "Еда" in text


@pytest.mark.asyncio
async def test_get_user_records_csv_empty_user(session):
    uid, _ = await _make_user(session, tg_id=1)
    raw = await get_user_records_csv(session, user_id=uid)
    text = raw.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1  # header only
