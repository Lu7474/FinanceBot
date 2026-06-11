"""Tests for user category CRUD and smart category suggestion."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from conftest import test_session  # session factory for independent sessions
from sqlalchemy import select

from core.database.models import CategoryKeyword, User
from core.database.requests import (
    add_record,
    add_user_category,
    count_records_with_category,
    delete_user_category,
    get_budgets,
    get_user_categories,
    learn_keyword,
    merge_user_categories,
    rename_user_category,
    seed_default_categories,
    set_budget,
    suggest_category,
)

# ==================== Helpers ====================


async def _make_user(tg_id: int = 100) -> int:
    """Creates a user in a separate session and returns user.id."""
    async with test_session() as s:
        user = User(tg_id=tg_id, name="Test")
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


# ==================== Tests ====================


@pytest.mark.asyncio
async def test_add_user_category(session):
    user_id = await _make_user()

    async with test_session() as s:
        cat = await add_user_category(s, user_id, "Еда", "-")

    assert cat is not None
    assert cat.name == "Еда"
    assert cat.cat_type == "-"
    assert cat.sort_order == 1


@pytest.mark.asyncio
async def test_add_user_category_duplicate(session):
    user_id = await _make_user()

    async with test_session() as s:
        cat1 = await add_user_category(s, user_id, "Еда", "-")
        await s.commit()

    async with test_session() as s:
        cat2 = await add_user_category(s, user_id, "Еда", "-")

    assert cat1 is not None
    assert cat2 is None  # duplicate rejected


@pytest.mark.asyncio
async def test_add_user_category_limit(session):
    user_id = await _make_user()

    for i in range(30):
        async with test_session() as s:
            cat = await add_user_category(s, user_id, f"Кат{i}", "-")
            assert cat is not None, f"Category {i} should be created"
            await s.commit()

    async with test_session() as s:
        extra = await add_user_category(s, user_id, "Лишняя", "-")
    assert extra is None


@pytest.mark.asyncio
async def test_rename_user_category(session):
    user_id = await _make_user()

    async with test_session() as s:
        cat = await add_user_category(s, user_id, "Старое", "-")
        cat_id = cat.id
        await s.commit()

    async with test_session() as s:
        await add_record(s, user_id, "-", Decimal("100"), "Старое")
        await s.commit()

    async with test_session() as s:
        ok = await rename_user_category(s, cat_id, user_id, "Новое")
        await s.commit()
    assert ok

    async with test_session() as s:
        cats = await get_user_categories(s, user_id)
    assert any(c.name == "Новое" for c in cats)
    assert not any(c.name == "Старое" for c in cats)

    async with test_session() as s:
        new_count = await count_records_with_category(s, user_id, "Новое")
        old_count = await count_records_with_category(s, user_id, "Старое")
    assert new_count == 1
    assert old_count == 0


@pytest.mark.asyncio
async def test_rename_category_updates_budget(session):
    """Rename must keep Budget.category in sync — otherwise the budget
    silently stops tracking the renamed category."""
    user_id = await _make_user()

    async with test_session() as s:
        cat = await add_user_category(s, user_id, "Старое", "-")
        cat_id = cat.id
        await set_budget(s, user_id, "Старое", Decimal("5000"))
        await s.commit()

    async with test_session() as s:
        ok = await rename_user_category(s, cat_id, user_id, "Новое")
        await s.commit()
    assert ok

    async with test_session() as s:
        budgets = await get_budgets(s, user_id)
    assert len(budgets) == 1
    assert budgets[0].category == "Новое"
    assert budgets[0].amount == Decimal("5000")


@pytest.mark.asyncio
async def test_rename_category_budget_collision_keeps_both(session):
    """Orphaned budget already exists under the new name: rename succeeds,
    the old budget is left untouched (unique user_id+category index)."""
    user_id = await _make_user()

    async with test_session() as s:
        cat = await add_user_category(s, user_id, "Старое", "-")
        cat_id = cat.id
        await set_budget(s, user_id, "Старое", Decimal("5000"))
        await set_budget(s, user_id, "Новое", Decimal("9000"))  # orphan
        await s.commit()

    async with test_session() as s:
        ok = await rename_user_category(s, cat_id, user_id, "Новое")
        await s.commit()
    assert ok

    async with test_session() as s:
        budgets = {b.category: b.amount for b in await get_budgets(s, user_id)}
    assert budgets == {"Старое": Decimal("5000"), "Новое": Decimal("9000")}


@pytest.mark.asyncio
async def test_merge_categories_moves_budget(session):
    user_id = await _make_user()

    async with test_session() as s:
        src = await add_user_category(s, user_id, "Кафе", "-")
        dst = await add_user_category(s, user_id, "Еда", "-")
        src_id, dst_id = src.id, dst.id
        await set_budget(s, user_id, "Кафе", Decimal("3000"))
        await s.commit()

    async with test_session() as s:
        moved = await merge_user_categories(s, src_id, dst_id, user_id)
        await s.commit()
    assert moved is not None

    async with test_session() as s:
        budgets = await get_budgets(s, user_id)
    assert len(budgets) == 1
    assert budgets[0].category == "Еда"
    assert budgets[0].amount == Decimal("3000")


@pytest.mark.asyncio
async def test_delete_user_category(session):
    user_id = await _make_user()

    async with test_session() as s:
        cat = await add_user_category(s, user_id, "Удалимая", "-")
        cat_id = cat.id
        await s.commit()

    async with test_session() as s:
        await add_record(s, user_id, "-", Decimal("200"), "Удалимая")
        await s.commit()

    async with test_session() as s:
        ok = await delete_user_category(s, cat_id, user_id)
        await s.commit()
    assert ok

    async with test_session() as s:
        cats = await get_user_categories(s, user_id)
    assert not any(c.name == "Удалимая" for c in cats)

    async with test_session() as s:
        count = await count_records_with_category(s, user_id, "Удалимая")
    assert count == 1  # records unchanged


@pytest.mark.asyncio
async def test_seed_default_categories(session):
    user_id = await _make_user()

    async with test_session() as s:
        await seed_default_categories(s, user_id)
        await s.commit()

    async with test_session() as s:
        cats = await get_user_categories(s, user_id)
    assert len(cats) == 8

    # Second call is no-op
    async with test_session() as s:
        await seed_default_categories(s, user_id)
        await s.commit()

    async with test_session() as s:
        cats2 = await get_user_categories(s, user_id)
    assert len(cats2) == 8


@pytest.mark.asyncio
async def test_suggest_category_user_rule(session):
    user_id = await _make_user()

    async with test_session() as s:
        await seed_default_categories(s, user_id)
        await s.commit()

    async with test_session() as s:
        cats = await get_user_categories(s, user_id)
        transport_cat = next(c for c in cats if c.name == "Транспорт")
        transport_id = transport_cat.id

    # Learn user rule: "метро" → Транспорт
    async with test_session() as s:
        await learn_keyword(s, user_id, "метро", transport_id)
        await s.commit()

    # User rule should match
    async with test_session() as s:
        result = await suggest_category(s, user_id, "метро сегодня")
    assert result == "Транспорт"


@pytest.mark.asyncio
async def test_suggest_category_system(session):
    user_id = await _make_user()

    async with test_session() as s:
        await seed_default_categories(s, user_id)
        await s.commit()

    # "продукты" is in SYSTEM_KEYWORDS → "Еда", and "Еда" is in user's categories
    async with test_session() as s:
        result = await suggest_category(s, user_id, "продукты")
    assert result == "Еда"


@pytest.mark.asyncio
async def test_suggest_category_no_match(session):
    user_id = await _make_user()

    async with test_session() as s:
        await seed_default_categories(s, user_id)
        await s.commit()

    async with test_session() as s:
        result = await suggest_category(s, user_id, "абракадабра xyz")
    assert result is None


@pytest.mark.asyncio
async def test_suggest_category_category_not_in_list(session):
    user_id = await _make_user()
    # No default categories — user has no "Еда"

    async with test_session() as s:
        result = await suggest_category(s, user_id, "продукты")
    assert result is None


@pytest.mark.asyncio
async def test_learn_keyword(session):
    user_id = await _make_user()

    async with test_session() as s:
        await seed_default_categories(s, user_id)
        await s.commit()

    async with test_session() as s:
        cats = await get_user_categories(s, user_id)
        eat_cat = next(c for c in cats if c.name == "Еда")
        eat_id = eat_cat.id

    async with test_session() as s:
        await learn_keyword(s, user_id, "поход пятёрочку молоком", eat_id)
        await s.commit()

    async with test_session() as s:
        result = await s.execute(
            select(CategoryKeyword).where(CategoryKeyword.user_id == user_id)
        )
        keywords = result.scalars().all()

    assert 1 <= len(keywords) <= 3
    for kw in keywords:
        assert len(kw.keyword) >= 3
        assert not kw.keyword.isdigit()


@pytest.mark.asyncio
async def test_merge_user_categories(session):
    user_id = await _make_user()

    async with test_session() as s:
        source = await add_user_category(s, user_id, "Кофе", "-")
        target = await add_user_category(s, user_id, "Кафе", "-")
        source_id, target_id = source.id, target.id
        await s.commit()

    async with test_session() as s:
        await add_record(s, user_id, "-", Decimal("250"), "Кофе")
        await add_record(s, user_id, "-", Decimal("180"), "Кофе")
        await add_record(s, user_id, "-", Decimal("900"), "Кафе")
        await learn_keyword(s, user_id, "старбакс кофе утром", source_id)
        await s.commit()

    async with test_session() as s:
        moved = await merge_user_categories(s, source_id, target_id, user_id)
        await s.commit()
    assert moved == 2  # only the two "Кофе" records

    async with test_session() as s:
        cats = await get_user_categories(s, user_id)
    assert not any(c.name == "Кофе" for c in cats)  # source removed
    assert any(c.name == "Кафе" for c in cats)

    async with test_session() as s:
        kofe = await count_records_with_category(s, user_id, "Кофе")
        kafe = await count_records_with_category(s, user_id, "Кафе")
    assert kofe == 0
    assert kafe == 3  # 2 moved + 1 original

    # Source keywords re-pointed to target, not orphaned
    async with test_session() as s:
        result = await s.execute(
            select(CategoryKeyword).where(CategoryKeyword.user_id == user_id)
        )
        keywords = result.scalars().all()
    assert keywords  # not deleted
    assert all(kw.category_id == target_id for kw in keywords)


@pytest.mark.asyncio
async def test_merge_missing_category_returns_none(session):
    user_id = await _make_user()

    async with test_session() as s:
        target = await add_user_category(s, user_id, "Кафе", "-")
        target_id = target.id
        await s.commit()

    async with test_session() as s:
        moved = await merge_user_categories(s, 999999, target_id, user_id)
    assert moved is None


@pytest.mark.asyncio
async def test_sort_order_increment(session):
    user_id = await _make_user()

    async with test_session() as s:
        cat1 = await add_user_category(s, user_id, "Первая", "-")
        await s.commit()
        await s.refresh(cat1)
    async with test_session() as s:
        cat2 = await add_user_category(s, user_id, "Вторая", "-")
        await s.commit()
        await s.refresh(cat2)
    async with test_session() as s:
        cat3 = await add_user_category(s, user_id, "Третья", "+")
        await s.commit()
        await s.refresh(cat3)

    assert cat1.sort_order == 1
    assert cat2.sort_order == 2
    assert cat3.sort_order == 3
