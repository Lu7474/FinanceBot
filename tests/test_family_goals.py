"""Tests for shared family goals: access, deposits, completion, withdraw, dissolve."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from core.database.models import Account, Goal, Record, User
from core.database.requests import (
    complete_goal,
    create_family,
    create_goal,
    delete_goal,
    deposit_goal,
    dissolve_family,
    get_family_members,
    get_goal,
    get_goal_contributions,
    get_goals,
    get_owned_goal,
    join_family,
    update_goal,
    withdraw_goal,
)

# ==================== Helpers ====================


async def _mk_user(session, tg_id: int, name: str) -> int:
    user = User(tg_id=tg_id, name=name)
    session.add(user)
    await session.flush()
    return user.id


async def _mk_account(session, user_id: int, name: str = "Карта") -> int:
    acc = Account(user_id=user_id, name=name)
    session.add(acc)
    await session.flush()
    return acc.id


async def _family_with_member(session):
    """Owner + member in one family. Returns (owner_id, member_id, family_id)."""
    owner = await _mk_user(session, 1, "Иван")
    family = await create_family(session, owner, "Семья")
    member = await _mk_user(session, 2, "Мария")
    await join_family(session, member, family.invite_code)
    return owner, member, family.id


async def _mk_goal(session, owner_id: int, target: str, family_id=None) -> int:
    """Creates a goal, returns id (read before commit, flush already set it)."""
    goal = await create_goal(
        session, owner_id, "Отпуск", Decimal(target), None, family_id=family_id
    )
    goal_id = goal.id
    await session.commit()
    return goal_id


async def _offset(session, account_id: int) -> Decimal:
    acc = await session.get(Account, account_id)
    await session.refresh(acc)
    return Decimal(str(acc.balance_offset))


async def _current_amount(session, goal_id: int) -> Decimal:
    goal = await session.get(Goal, goal_id)
    await session.refresh(goal)
    return Decimal(str(goal.current_amount))


# ==================== Access ====================


@pytest.mark.asyncio
async def test_shared_goal_visible_to_member(session):
    owner, member, fam_id = await _family_with_member(session)
    goal_id = await _mk_goal(session, owner, "100000", family_id=fam_id)

    member_goals = await get_goals(session, member)
    assert goal_id in {g.id for g in member_goals}
    assert await get_goal(session, goal_id, member) is not None


@pytest.mark.asyncio
async def test_personal_goal_not_visible_to_family(session):
    owner, member, fam_id = await _family_with_member(session)
    goal_id = await _mk_goal(session, owner, "5000")  # personal (family_id=None)

    member_goals = await get_goals(session, member)
    assert goal_id not in {g.id for g in member_goals}
    assert await get_goal(session, goal_id, member) is None


@pytest.mark.asyncio
async def test_outsider_has_no_access(session):
    owner, member, fam_id = await _family_with_member(session)
    outsider = await _mk_user(session, 9, "Чужой")
    goal_id = await _mk_goal(session, owner, "100000", family_id=fam_id)

    assert await get_goal(session, goal_id, outsider) is None
    assert goal_id not in {g.id for g in await get_goals(session, outsider)}


# ==================== Deposit / attribution ====================


@pytest.mark.asyncio
async def test_member_deposit_offsets_own_account(session):
    owner, member, fam_id = await _family_with_member(session)
    member_acc = await _mk_account(session, member)
    goal_id = await _mk_goal(session, owner, "100000", family_id=fam_id)

    await deposit_goal(session, goal_id, member, Decimal("5000"), None, member_acc)
    await session.commit()

    assert await _offset(session, member_acc) == Decimal("-5000")
    assert await _current_amount(session, goal_id) == Decimal("5000")

    contribs = await get_goal_contributions(session, goal_id)
    assert ("Мария", Decimal("5000")) in contribs


# ==================== Management gate ====================


@pytest.mark.asyncio
async def test_member_cannot_manage(session):
    owner, member, fam_id = await _family_with_member(session)
    goal_id = await _mk_goal(session, owner, "100000", family_id=fam_id)

    assert await get_owned_goal(session, goal_id, member) is None
    assert await get_owned_goal(session, goal_id, owner) is not None

    # update is a no-op for a member
    assert await update_goal(session, goal_id, member, name="Взлом") is False
    goal = await session.get(Goal, goal_id)
    await session.refresh(goal)
    assert goal.name == "Отпуск"

    # complete/delete no-ops for a member
    await complete_goal(session, goal_id, member)
    await session.refresh(goal)
    assert goal.is_completed is False

    await delete_goal(session, goal_id, member)
    assert await session.get(Goal, goal_id) is not None


# ==================== Completion: per-member records ====================


@pytest.mark.asyncio
async def test_complete_records_expense_per_member(session):
    owner, member, fam_id = await _family_with_member(session)
    owner_acc = await _mk_account(session, owner, "Нал")
    member_acc = await _mk_account(session, member, "Карта")
    goal_id = await _mk_goal(session, owner, "15000", family_id=fam_id)

    await deposit_goal(session, goal_id, owner, Decimal("10000"), None, owner_acc)
    await deposit_goal(session, goal_id, member, Decimal("5000"), None, member_acc)
    await session.commit()

    await complete_goal(session, goal_id, owner)
    await session.commit()

    goal = await session.get(Goal, goal_id)
    await session.refresh(goal)
    assert goal.is_completed is True
    assert await _offset(session, owner_acc) == Decimal("0")
    assert await _offset(session, member_acc) == Decimal("0")

    # Expense Record attributed to each account's owner, not the completer
    owner_exp = await session.scalar(
        select(func.coalesce(func.sum(Record.amount), 0)).where(
            Record.user_id == owner, Record.category == "Цели"
        )
    )
    member_exp = await session.scalar(
        select(func.coalesce(func.sum(Record.amount), 0)).where(
            Record.user_id == member, Record.category == "Цели"
        )
    )
    assert Decimal(str(owner_exp)) == Decimal("10000")
    assert Decimal(str(member_exp)) == Decimal("5000")


# ==================== Withdraw: any member, any amount ====================


@pytest.mark.asyncio
async def test_member_withdraws_more_than_own_contribution(session):
    owner, member, fam_id = await _family_with_member(session)
    owner_acc = await _mk_account(session, owner, "Нал")
    member_acc = await _mk_account(session, member, "Карта")
    goal_id = await _mk_goal(session, owner, "100000", family_id=fam_id)

    await deposit_goal(session, goal_id, owner, Decimal("10000"), None, owner_acc)
    await deposit_goal(session, goal_id, member, Decimal("5000"), None, member_acc)
    await session.commit()

    # Member pulls the whole 15k onto their own account (more than they put in)
    await withdraw_goal(session, goal_id, member, Decimal("15000"), None, member_acc)
    await session.commit()

    assert await _current_amount(session, goal_id) == Decimal("0")
    # member_acc: -5000 (deposit) + 15000 (withdraw) = +10000
    assert await _offset(session, member_acc) == Decimal("10000")
    assert await _offset(session, owner_acc) == Decimal("-10000")


# ==================== Delete restores all members' offsets ====================


@pytest.mark.asyncio
async def test_delete_restores_all_offsets(session):
    owner, member, fam_id = await _family_with_member(session)
    owner_acc = await _mk_account(session, owner, "Нал")
    member_acc = await _mk_account(session, member, "Карта")
    goal_id = await _mk_goal(session, owner, "100000", family_id=fam_id)

    await deposit_goal(session, goal_id, owner, Decimal("10000"), None, owner_acc)
    await deposit_goal(session, goal_id, member, Decimal("5000"), None, member_acc)
    await session.commit()

    await delete_goal(session, goal_id, owner)
    await session.commit()

    assert await session.get(Goal, goal_id) is None
    assert await _offset(session, owner_acc) == Decimal("0")
    assert await _offset(session, member_acc) == Decimal("0")
    cnt = await session.scalar(
        select(func.count(Record.id)).where(Record.category == "Цели")
    )
    assert cnt == 0


# ==================== Dissolve: shared goal → personal, balances intact ====================


@pytest.mark.asyncio
async def test_dissolve_converts_shared_goal_to_personal(session):
    owner, member, fam_id = await _family_with_member(session)
    member_acc = await _mk_account(session, member)
    goal_id = await _mk_goal(session, owner, "100000", family_id=fam_id)
    await deposit_goal(session, goal_id, member, Decimal("5000"), None, member_acc)
    await session.commit()

    assert await dissolve_family(session, fam_id, owner) is True
    await session.commit()

    # SET NULL: goal survives, becomes personal of ex-owner, money intact
    revived = await session.get(Goal, goal_id)
    assert revived is not None
    await session.refresh(revived)
    assert revived.family_id is None
    assert revived.user_id == owner
    assert revived.current_amount == Decimal("5000")
    assert await _offset(session, member_acc) == Decimal("-5000")


# ==================== Notify other members on deposit/withdraw ====================


@pytest.mark.asyncio
async def test_notify_excludes_actor_and_formats_withdraw(session):
    from unittest.mock import AsyncMock

    from core.handlers.goals import _notify_family_goal_move

    owner, member, fam_id = await _family_with_member(session)
    await session.commit()
    members = await get_family_members(session, fam_id)
    member_user = next(m for m in members if m.id == member)

    bot = AsyncMock()
    await _notify_family_goal_move(
        bot, members, owner, "Иван", "Отпуск", Decimal("5000"), "withdraw"
    )

    # Only the member is notified — the actor (owner) is skipped
    assert bot.send_message.await_count == 1
    args, kwargs = bot.send_message.await_args
    assert args[0] == member_user.tg_id
    assert "снял" in args[1] and "5 000₽" in args[1] and "Отпуск" in args[1]
    assert kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_notify_deposit_text_and_block_resilient(session):
    from unittest.mock import AsyncMock

    from core.handlers.goals import _notify_family_goal_move

    owner, member, fam_id = await _family_with_member(session)
    await session.commit()
    members = await get_family_members(session, fam_id)

    bot = AsyncMock()
    bot.send_message.side_effect = Exception("bot blocked by user")
    # A member who blocked the bot must not break the call
    await _notify_family_goal_move(
        bot, members, member, "Мария", "Отпуск", Decimal("3000"), "deposit"
    )

    # Actor (member) excluded → only the owner is targeted
    assert bot.send_message.await_count == 1
    args, _ = bot.send_message.await_args
    assert "внёс" in args[1] and "3 000₽" in args[1]
