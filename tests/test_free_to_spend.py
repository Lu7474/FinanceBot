"""Tests for get_free_to_spend — «сколько могу потратить» (свободные деньги).

Главный риск — двойной счёт: привязанный депозит в цель уже уменьшает баланс
счёта, поэтому из earmark берётся только непривязанная часть (account_id IS NULL).
"""

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent))

from conftest import test_session

from core.database.models import Account, GoalDeposit, User
from core.database.requests import (
    add_record,
    complete_goal,
    create_goal,
    create_payment,
    deposit_goal,
    get_free_to_spend,
    withdraw_goal,
)
from core.utils import today_msk

# ==================== Helpers ====================


async def _make_user(tg_id: int) -> int:
    async with test_session() as s:
        user = User(tg_id=tg_id, name="FtsTest")
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user.id


async def _make_account(user_id: int, balance: Decimal, name: str = "Карта") -> int:
    """Creates an account and seeds it with `balance` via an income record."""
    async with test_session() as s:
        acc = Account(user_id=user_id, name=name)
        s.add(acc)
        await s.commit()
        await s.refresh(acc)
        acc_id = acc.id
    if balance != 0:
        async with test_session() as s:
            await add_record(
                s, user_id, "+", balance, category="Зарплата", account_id=acc_id
            )
            await s.commit()
    return acc_id


async def _make_goal(user_id: int, target: Decimal, family_id=None) -> int:
    async with test_session() as s:
        goal = await create_goal(s, user_id, "Цель", target, None, family_id=family_id)
        goal_id = goal.id
        await s.commit()
        return goal_id


async def _fts(user_id: int):
    async with test_session() as s:
        return await get_free_to_spend(s, user_id)


# ==================== Tests ====================


@pytest.mark.asyncio
async def test_no_goals_no_payments_free_equals_balance(session):
    user_id = await _make_user(7001)
    await _make_account(user_id, Decimal("85000"))

    fts = await _fts(user_id)

    assert fts.total_balance == Decimal("85000")
    assert fts.earmark == Decimal("0")
    assert fts.upcoming_payments == Decimal("0")
    assert fts.free == fts.total_balance == Decimal("85000")


@pytest.mark.asyncio
async def test_linked_deposit_no_double_count(session):
    """Привязанный депозит уже уменьшил баланс → earmark его НЕ учитывает."""
    user_id = await _make_user(7002)
    acc_id = await _make_account(user_id, Decimal("85000"))
    goal_id = await _make_goal(user_id, Decimal("200000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("20000"), None, acc_id)
        await s.commit()

    fts = await _fts(user_id)

    # Баланс упал на 20000, earmark непривязанный = 0 → free == total_balance.
    assert fts.total_balance == Decimal("65000")
    assert fts.earmark == Decimal("0")
    assert fts.free == fts.total_balance == Decimal("65000")


@pytest.mark.asyncio
async def test_unlinked_deposit_reduces_free(session):
    """Непривязанный депозит не трогает баланс, но вычитается как earmark."""
    user_id = await _make_user(7003)
    await _make_account(user_id, Decimal("85000"))
    goal_id = await _make_goal(user_id, Decimal("200000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("20000"), None, None)
        await s.commit()

    fts = await _fts(user_id)

    assert fts.total_balance == Decimal("85000")
    assert fts.earmark == Decimal("20000")
    assert fts.free == Decimal("65000")


@pytest.mark.asyncio
async def test_payment_this_month_subtracted_next_month_not(session):
    user_id = await _make_user(7004)
    await _make_account(user_id, Decimal("85000"))
    today = today_msk()

    async with test_session() as s:
        await create_payment(s, user_id, "Этот месяц", Decimal("5000"), today, "none")
        await s.commit()

    fts = await _fts(user_id)
    assert fts.upcoming_payments == Decimal("5000")
    assert fts.free == Decimal("80000")

    # Платёж в следующем месяце не должен вычитаться.
    user2 = await _make_user(7005)
    await _make_account(user2, Decimal("85000"))
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=15)
    else:
        next_month = today.replace(month=today.month + 1, day=15)

    async with test_session() as s:
        await create_payment(
            s, user2, "След. месяц", Decimal("5000"), next_month, "none"
        )
        await s.commit()

    fts2 = await _fts(user2)
    assert fts2.upcoming_payments == Decimal("0")
    assert fts2.free == Decimal("85000")


@pytest.mark.asyncio
async def test_overdue_active_payment_subtracted(session):
    user_id = await _make_user(7006)
    await _make_account(user_id, Decimal("85000"))
    overdue = today_msk() - timedelta(days=10)

    async with test_session() as s:
        await create_payment(s, user_id, "Просрочка", Decimal("3000"), overdue, "none")
        await s.commit()

    fts = await _fts(user_id)
    assert fts.upcoming_payments == Decimal("3000")
    assert fts.free == Decimal("82000")


@pytest.mark.asyncio
async def test_payment_no_amount_counted_not_summed(session):
    user_id = await _make_user(7007)
    await _make_account(user_id, Decimal("85000"))
    today = today_msk()

    async with test_session() as s:
        await create_payment(s, user_id, "Коммуналка", None, today, "none")
        await create_payment(s, user_id, "Точный", Decimal("4000"), today, "none")
        await s.commit()

    fts = await _fts(user_id)
    assert fts.upcoming_payments == Decimal("4000")
    assert fts.payments_no_amount == 1
    assert fts.free == Decimal("81000")


@pytest.mark.asyncio
async def test_completed_goal_earmark_ignored(session):
    """Завершённая цель: её непривязанный earmark не вычитается из free."""
    user_id = await _make_user(7008)
    await _make_account(user_id, Decimal("85000"))
    goal_id = await _make_goal(user_id, Decimal("20000"))

    async with test_session() as s:
        await deposit_goal(s, goal_id, user_id, Decimal("20000"), None, None)
        await s.commit()
    async with test_session() as s:
        await complete_goal(s, goal_id, user_id)
        await s.commit()

    fts = await _fts(user_id)
    assert fts.earmark == Decimal("0")
    assert fts.free == fts.total_balance == Decimal("85000")


@pytest.mark.asyncio
async def test_family_goal_other_member_deposit_ignored(session):
    """Чужой непривязанный взнос в семейную цель не уменьшает мой free."""
    user_id = await _make_user(7009)
    other_id = await _make_user(7010)
    await _make_account(user_id, Decimal("85000"))
    goal_id = await _make_goal(user_id, Decimal("200000"))

    # Непривязанный взнос ДРУГОГО юзера — пишем строку напрямую.
    async with test_session() as s:
        s.add(
            GoalDeposit(
                goal_id=goal_id,
                user_id=other_id,
                account_id=None,
                amount=Decimal("30000"),
            )
        )
        await s.commit()

    fts = await _fts(user_id)
    assert fts.earmark == Decimal("0")
    assert fts.free == fts.total_balance == Decimal("85000")


@pytest.mark.asyncio
async def test_negative_earmark_clamped_to_zero(session):
    """Снято непривязанно больше, чем внесено непривязанно → earmark = 0, не < 0."""
    user_id = await _make_user(7011)
    acc_id = await _make_account(user_id, Decimal("85000"))
    goal_id = await _make_goal(user_id, Decimal("200000"))

    async with test_session() as s:
        # Привязанный депозит даёт current_amount, баланс падает на 1000.
        await deposit_goal(s, goal_id, user_id, Decimal("1000"), None, acc_id)
        await s.commit()
    async with test_session() as s:
        # Непривязанное снятие 1000 → net непривязанного = -1000.
        await withdraw_goal(s, goal_id, user_id, Decimal("1000"), None, None)
        await s.commit()

    fts = await _fts(user_id)
    assert fts.total_balance == Decimal("84000")
    assert fts.earmark == Decimal("0")  # клампнут, не -1000
    assert fts.free == fts.total_balance == Decimal("84000")
