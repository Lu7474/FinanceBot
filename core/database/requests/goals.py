"""Financial goals: CRUD, deposit/withdraw with account offset adjustment."""

from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MAX_GOAL_NAME_LENGTH
from core.database.models import Account, Goal, GoalDeposit, Record, moscow_now
from core.exceptions import (
    GoalCompleted,
    GoalNotFound,
    GoalNotFoundOrCompleted,
    InsufficientFundsInGoal,
)
from core.utils import today_msk


async def get_goals(
    session: AsyncSession, user_id: int, include_completed: bool = False
) -> list[Goal]:
    """Returns user's goals with smart sort: overdue → nearest deadline → highest progress.

    Active goals only by default. Smart sort makes most-relevant goals appear first.
    """
    q = select(Goal).where(Goal.user_id == user_id)
    if not include_completed:
        q = q.where(Goal.is_completed == False)  # noqa: E712
    goals = list(await session.scalars(q))
    today = today_msk()

    def _sort_key(g: Goal) -> tuple:
        # 0=achieved-not-closed (very top, nudge to close), 1=overdue, 2=with deadline (by closeness),
        # 3=no deadline (by progress desc), 4=completed (bottom)
        if g.is_completed:
            return (4, g.created_at)
        if g.current_amount >= g.target_amount:
            return (0, g.created_at)
        if g.deadline and g.deadline < today:
            return (1, g.deadline)
        if g.deadline:
            return (2, g.deadline)
        pct = float(g.current_amount) / float(g.target_amount) if g.target_amount else 0
        return (3, -pct)

    goals.sort(key=_sort_key)
    return goals


async def get_goal(session: AsyncSession, goal_id: int, user_id: int) -> Goal | None:
    """Returns a single goal by id, validates ownership."""
    return await session.scalar(
        select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )


async def update_goal(
    session: AsyncSession,
    goal_id: int,
    user_id: int,
    name: str | None = None,
    target_amount: Decimal | None = None,
    deadline: date_type | None = None,
    clear_deadline: bool = False,
) -> bool:
    """Updates goal fields (name/target/deadline). Returns True if goal found and updated."""
    goal = await get_goal(session, goal_id, user_id)
    if not goal:
        return False
    if name is not None:
        if not (0 < len(name) <= MAX_GOAL_NAME_LENGTH):
            raise ValueError(f"name must be 1..{MAX_GOAL_NAME_LENGTH} chars")
        goal.name = name
    if target_amount is not None:
        goal.target_amount = target_amount
    if clear_deadline:
        goal.deadline = None
    elif deadline is not None:
        goal.deadline = deadline
    await session.flush()
    return True


async def create_goal(
    session: AsyncSession,
    user_id: int,
    name: str,
    target: Decimal,
    deadline,
) -> Goal:
    """Creates a new goal."""
    if not (0 < len(name) <= MAX_GOAL_NAME_LENGTH):
        raise ValueError(f"name must be 1..{MAX_GOAL_NAME_LENGTH} chars")
    goal = Goal(user_id=user_id, name=name, target_amount=target, deadline=deadline)
    session.add(goal)
    await session.flush()
    return goal


async def deposit_goal(
    session: AsyncSession,
    goal_id: int,
    user_id: int,
    amount: Decimal,
    note: str | None,
    account_id: int | None,
) -> GoalDeposit:
    """Adds deposit to goal — earmark в «конверт». Adjusts balance_offset, NO Record:
    реальный расход появляется только при complete_goal."""
    goal = await get_goal(session, goal_id, user_id)
    if not goal or goal.is_completed:
        raise GoalNotFoundOrCompleted()

    deposit = GoalDeposit(
        goal_id=goal_id, account_id=account_id, amount=amount, note=note
    )
    session.add(deposit)
    goal.current_amount += amount

    if account_id:
        acc = await session.scalar(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        if acc:
            acc.balance_offset = Decimal(str(acc.balance_offset)) - amount

    await session.flush()
    return deposit


async def withdraw_goal(
    session: AsyncSession,
    goal_id: int,
    user_id: int,
    amount: Decimal,
    note: str | None,
    account_id: int | None,
) -> GoalDeposit:
    """Withdraws from goal — снимает earmark. Adjusts balance_offset, no Record."""
    goal = await get_goal(session, goal_id, user_id)
    if not goal:
        raise GoalNotFound()
    if goal.is_completed:
        raise GoalCompleted()
    if amount > goal.current_amount:
        raise InsufficientFundsInGoal()

    deposit = GoalDeposit(
        goal_id=goal_id, account_id=account_id, amount=-amount, note=note
    )
    session.add(deposit)
    goal.current_amount -= amount

    if account_id:
        acc = await session.scalar(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        if acc:
            acc.balance_offset = Decimal(str(acc.balance_offset)) + amount

    await session.flush()
    return deposit


async def complete_goal(session: AsyncSession, goal_id: int, user_id: int) -> None:
    """Marks goal as completed. Restores account balance_offsets and creates expense Records."""
    goal = await get_goal(session, goal_id, user_id)
    if not goal:
        return
    goal.is_completed = True
    goal.completed_at = moscow_now()

    deposits = list(
        await session.scalars(
            select(GoalDeposit).where(
                GoalDeposit.goal_id == goal_id,
                GoalDeposit.account_id.is_not(None),
            )
        )
    )
    net_by_account: dict[int, Decimal] = {}
    for d in deposits:
        aid = d.account_id
        assert aid is not None  # query filters by .is_not(None)
        net_by_account[aid] = net_by_account.get(aid, Decimal("0")) + d.amount

    for account_id, net in net_by_account.items():
        if net == Decimal("0"):
            continue
        acc = await session.scalar(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        if not acc:
            continue
        acc.balance_offset = Decimal(str(acc.balance_offset)) + net
        if net > 0:
            session.add(
                Record(
                    user_id=user_id,
                    operation="-",
                    amount=net,
                    category="Цели",
                    account_id=account_id,
                )
            )

    await session.flush()


async def delete_goal(session: AsyncSession, goal_id: int, user_id: int) -> None:
    """Deletes goal and all its deposits. Restores account balance_offsets."""
    goal = await get_goal(session, goal_id, user_id)
    if not goal:
        return

    deposits = list(
        await session.scalars(
            select(GoalDeposit).where(
                GoalDeposit.goal_id == goal_id,
                GoalDeposit.account_id.is_not(None),
            )
        )
    )
    net_by_account: dict[int, Decimal] = {}
    for d in deposits:
        aid = d.account_id
        assert aid is not None  # query filters by .is_not(None)
        net_by_account[aid] = net_by_account.get(aid, Decimal("0")) + d.amount

    for account_id, net in net_by_account.items():
        if net == Decimal("0"):
            continue
        acc = await session.scalar(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        if acc:
            acc.balance_offset = Decimal(str(acc.balance_offset)) + net

    await session.execute(delete(GoalDeposit).where(GoalDeposit.goal_id == goal_id))
    await session.execute(
        delete(Goal).where(Goal.id == goal_id, Goal.user_id == user_id)
    )


async def get_goal_deposits(
    session: AsyncSession, goal_id: int, limit: int = 10
) -> list[GoalDeposit]:
    """Returns last N deposits for a goal (newest first)."""
    return list(
        await session.scalars(
            select(GoalDeposit)
            .where(GoalDeposit.goal_id == goal_id)
            .order_by(GoalDeposit.created_at.desc(), GoalDeposit.id.desc())
            .limit(limit)
        )
    )
