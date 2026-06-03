"""Tests for the family budget feature: membership, invites, scope, cascade."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from core.database.models import Family, FamilyMember, Record, User, moscow_now
from core.database.requests import (
    MAX_FAMILY_MEMBERS,
    create_family,
    dissolve_family,
    get_family,
    get_family_category_breakdown,
    get_family_member_ids,
    get_family_members,
    get_family_summary,
    get_history_data,
    join_family,
    kick_member,
    leave_family,
    regenerate_invite_code,
    rename_family,
)
from core.database.requests import family as family_module

# ==================== Helpers ====================


async def _mk_user(session, tg_id: int, name: str) -> int:
    user = User(tg_id=tg_id, name=name)
    session.add(user)
    await session.flush()
    return user.id


async def _mk_record(
    session, user_id: int, operation: str, amount: str, category: str = "Еда"
) -> None:
    session.add(
        Record(
            user_id=user_id,
            operation=operation,
            amount=Decimal(amount),
            category=category,
            created_at=moscow_now(),
        )
    )
    await session.flush()


# ==================== create_family ====================


@pytest.mark.asyncio
async def test_create_family(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья Ивановых")

    assert family.id is not None
    assert family.name == "Семья Ивановых"
    assert family.owner_id == owner
    assert len(family.invite_code) == family_module.INVITE_CODE_LENGTH
    assert all(c in family_module.INVITE_CODE_ALPHABET for c in family.invite_code)

    # Owner row exists with role="owner"
    om = await session.scalar(select(FamilyMember).where(FamilyMember.user_id == owner))
    assert om is not None
    assert om.role == "owner"
    assert (await get_family(session, owner)).id == family.id


@pytest.mark.asyncio
async def test_invite_code_collision(session, monkeypatch):
    """A freshly generated code that collides is regenerated."""
    owner = await _mk_user(session, 1, "Иван")
    # Seed an existing family with code "AAAAAAAA"
    session.add(Family(name="X", owner_id=owner, invite_code="AAAAAAAA"))
    await session.flush()

    # choice() yields 8×'A' (collision) then 8×'B' (unique)
    seq = iter("A" * 8 + "B" * 8)
    monkeypatch.setattr(family_module.secrets, "choice", lambda _alphabet: next(seq))

    code = await family_module._generate_invite_code(session)
    assert code == "BBBBBBBB"


# ==================== join_family ====================


@pytest.mark.asyncio
async def test_join_family_success(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")

    joined = await join_family(session, member, family.invite_code)
    assert joined is not None
    assert joined.id == family.id
    assert (await get_family(session, member)).id == family.id


@pytest.mark.asyncio
async def test_join_family_full(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    # Owner is member #1; fill up to MAX
    for i in range(2, MAX_FAMILY_MEMBERS + 1):
        uid = await _mk_user(session, i, f"U{i}")
        assert await join_family(session, uid, family.invite_code) is not None

    extra = await _mk_user(session, 99, "Extra")
    assert await join_family(session, extra, family.invite_code) is None


@pytest.mark.asyncio
async def test_join_already_in_family(session):
    owner = await _mk_user(session, 1, "Иван")
    fam_a = await create_family(session, owner, "A")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, fam_a.invite_code)

    owner_b = await _mk_user(session, 3, "Петр")
    fam_b = await create_family(session, owner_b, "B")
    # Member already in fam_a → cannot join fam_b
    assert await join_family(session, member, fam_b.invite_code) is None


@pytest.mark.asyncio
async def test_join_invalid_code(session):
    member = await _mk_user(session, 2, "Мария")
    assert await join_family(session, member, "ZZZZZZZZ") is None


# ==================== leave_family ====================


@pytest.mark.asyncio
async def test_leave_family_member(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)

    assert await leave_family(session, member) is True
    assert await get_family(session, member) is None


@pytest.mark.asyncio
async def test_leave_family_owner_cannot(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    assert await leave_family(session, owner) is False
    assert (await get_family(session, owner)).id == family.id


# ==================== dissolve_family ====================


@pytest.mark.asyncio
async def test_dissolve_family_owner(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)
    await _mk_record(session, member, "-", "500")
    fam_id = family.id

    assert await dissolve_family(session, fam_id, owner) is True
    # Family + all members gone
    assert await session.get(Family, fam_id) is None
    assert await get_family(session, owner) is None
    assert await get_family(session, member) is None
    # Member's records survive
    remaining = await session.scalar(
        select(func.count(Record.id)).where(Record.user_id == member)
    )
    assert remaining == 1


@pytest.mark.asyncio
async def test_dissolve_family_non_owner(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)

    assert await dissolve_family(session, family.id, member) is False
    assert await session.get(Family, family.id) is not None


# ==================== kick_member ====================


@pytest.mark.asyncio
async def test_kick_member(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)

    # Non-owner cannot kick
    assert await kick_member(session, family.id, member, owner) is False
    # Owner cannot kick themselves
    assert await kick_member(session, family.id, owner, owner) is False
    # Owner kicks member
    assert await kick_member(session, family.id, owner, member) is True
    assert await get_family(session, member) is None


# ==================== regenerate_invite_code ====================


@pytest.mark.asyncio
async def test_regenerate_invite_code(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    old_code = family.invite_code
    member = await _mk_user(session, 2, "Мария")

    # Non-owner → None
    assert await regenerate_invite_code(session, family.id, member) is None

    new_code = await regenerate_invite_code(session, family.id, owner)
    assert new_code is not None
    assert new_code != old_code
    assert len(new_code) == family_module.INVITE_CODE_LENGTH


@pytest.mark.asyncio
async def test_rename_family(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Старое")
    member = await _mk_user(session, 2, "Мария")

    assert await rename_family(session, family.id, member, "Хак") is False
    assert await rename_family(session, family.id, owner, "Новое") is True
    assert (await session.get(Family, family.id)).name == "Новое"


# ==================== scope isolation ====================


@pytest.mark.asyncio
async def test_scope_isolation(session):
    u1 = await _mk_user(session, 1, "Иван")
    u2 = await _mk_user(session, 2, "Мария")
    u3 = await _mk_user(session, 3, "Чужой")
    await _mk_record(session, u1, "-", "100")
    await _mk_record(session, u2, "-", "200")
    await _mk_record(session, u3, "-", "999")  # outsider

    total, income, expense, records = await get_history_data(
        session, [u1, u2], within="all"
    )
    assert total == 2
    assert expense == Decimal("300")
    assert all(r.user_id in (u1, u2) for r in records)


@pytest.mark.asyncio
async def test_get_family_summary(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)

    await _mk_record(session, owner, "+", "85000")
    await _mk_record(session, owner, "-", "12500")
    await _mk_record(session, member, "+", "40000")
    await _mk_record(session, member, "-", "8200")

    summary = await get_family_summary(session, family.id, within="month")
    assert summary[owner]["income"] == Decimal("85000")
    assert summary[owner]["expense"] == Decimal("12500")
    assert summary[member]["income"] == Decimal("40000")
    assert summary[member]["expense"] == Decimal("8200")

    total_income = sum(d["income"] for d in summary.values())
    total_expense = sum(d["expense"] for d in summary.values())
    assert total_income == Decimal("125000")
    assert total_expense == Decimal("20700")


@pytest.mark.asyncio
async def test_family_category_breakdown(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)

    await _mk_record(session, owner, "-", "1000", "Кафе")
    await _mk_record(session, member, "-", "500", "Кафе")
    await _mk_record(session, member, "-", "300", "Транспорт")

    rows = await get_family_category_breakdown(session, family.id, "-", within="month")
    # (Кафе, owner), (Кафе, member), (Транспорт, member) → 3 groups
    assert len(rows) == 3
    cafe_total = sum(r["total"] for r in rows if r["category"] == "Кафе")
    assert cafe_total == Decimal("1500")


@pytest.mark.asyncio
async def test_member_helpers(session):
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)

    members = await get_family_members(session, family.id)
    assert [m.id for m in members] == [owner, member]  # owner first (joined earlier)
    ids = await get_family_member_ids(session, family.id)
    assert ids == [owner, member]


# ==================== delete_user_cascade ====================


@pytest.mark.asyncio
async def test_delete_user_cascade_owner(session):
    from core.database.requests import delete_user_cascade

    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)
    await _mk_record(session, member, "-", "500")
    fam_id = family.id

    assert await delete_user_cascade(session, 1) is True  # owner tg_id=1

    # Owner gone, family dissolved, member freed but still exists
    assert await session.get(User, owner) is None
    assert await session.get(Family, fam_id) is None
    assert await session.get(User, member) is not None
    assert await get_family(session, member) is None
    # No orphaned family_members rows
    leftover = await session.scalar(select(func.count(FamilyMember.id)))
    assert leftover == 0
    # Member's records survive
    recs = await session.scalar(
        select(func.count(Record.id)).where(Record.user_id == member)
    )
    assert recs == 1
