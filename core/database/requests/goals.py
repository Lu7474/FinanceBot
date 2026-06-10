"""Financial goals: CRUD, deposit/withdraw with account offset adjustment."""

from datetime import date as date_type
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MAX_GOAL_NAME_LENGTH
from core.database.models import (
    Account,
    FamilyMember,
    Goal,
    GoalDeposit,
    Record,
    User,
    moscow_now,
)
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

    Includes personal goals plus shared goals of the user's family. Active goals only
    by default. Smart sort makes most-relevant goals appear first.
    """
    family_id = await session.scalar(
        select(FamilyMember.family_id).where(FamilyMember.user_id == user_id)
    )
    if family_id is not None:
        cond = or_(Goal.user_id == user_id, Goal.family_id == family_id)
    else:
        cond = Goal.user_id == user_id
    q = select(Goal).where(cond)
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
    """Returns a goal the user may view/deposit/withdraw, or None.

    Access = own personal goal OR a shared goal of a family the user belongs to.
    For management (edit/complete/delete) use get_owned_goal instead.
    """
    goal = await session.scalar(select(Goal).where(Goal.id == goal_id))
    if goal is None:
        return None
    if goal.user_id == user_id:
        return goal
    if goal.family_id is not None:
        is_member = await session.scalar(
            select(FamilyMember.id).where(
                FamilyMember.family_id == goal.family_id,
                FamilyMember.user_id == user_id,
            )
        )
        if is_member:
            return goal
    return None


async def get_owned_goal(
    session: AsyncSession, goal_id: int, user_id: int
) -> Goal | None:
    """Returns a goal only if user is its owner/creator. Gate for management actions.

    For shared goals goal.user_id == family owner (only owner creates them), so this
    naturally restricts edit/complete/delete to the family owner.
    """
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
    """Updates goal fields (name/target/deadline). Owner-only. Returns True if updated."""
    goal = await get_owned_goal(session, goal_id, user_id)
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
    family_id: int | None = None,
) -> Goal:
    """Creates a new goal. family_id set → shared family goal (creator = owner)."""
    if not (0 < len(name) <= MAX_GOAL_NAME_LENGTH):
        raise ValueError(f"name must be 1..{MAX_GOAL_NAME_LENGTH} chars")
    goal = Goal(
        user_id=user_id,
        name=name,
        target_amount=target,
        deadline=deadline,
        family_id=family_id,
    )
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
        goal_id=goal_id,
        user_id=user_id,
        account_id=account_id,
        amount=amount,
        note=note,
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
        goal_id=goal_id,
        user_id=user_id,
        account_id=account_id,
        amount=-amount,
        note=note,
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
    """Marks goal as completed (owner-only). Restores account balance_offsets and
    creates an expense Record per account, attributed to that account's owner —
    so each member's contribution to a shared goal lands in their own history."""
    goal = await get_owned_goal(session, goal_id, user_id)
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
        # No user_id filter: shared-goal deposits may sit on other members' accounts.
        acc = await session.scalar(select(Account).where(Account.id == account_id))
        if not acc:
            continue
        acc.balance_offset = Decimal(str(acc.balance_offset)) + net
        if net > 0:
            session.add(
                Record(
                    user_id=acc.user_id,
                    operation="-",
                    amount=net,
                    category="Цели",
                    account_id=account_id,
                )
            )

    await session.flush()


async def delete_goal(session: AsyncSession, goal_id: int, user_id: int) -> None:
    """Deletes goal and all its deposits (owner-only). Restores balance_offset on
    each account that funded it, including other members' accounts (shared goals)."""
    goal = await get_owned_goal(session, goal_id, user_id)
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
        # No user_id filter: shared-goal deposits may sit on other members' accounts.
        acc = await session.scalar(select(Account).where(Account.id == account_id))
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


async def get_goal_contributions(
    session: AsyncSession, goal_id: int
) -> list[tuple[str, Decimal]]:
    """Net contribution per depositor as (name, net), biggest first.

    For shared-goal cards. Deposits with user_id NULL (legacy) are skipped via the
    inner join. Members with net 0 (deposited then fully withdrew) are kept.
    """
    rows = await session.execute(
        select(
            User.name,
            func.coalesce(func.sum(GoalDeposit.amount), 0).label("net"),
        )
        .join(User, User.id == GoalDeposit.user_id)
        .where(GoalDeposit.goal_id == goal_id)
        .group_by(User.id, User.name)
        .order_by(func.sum(GoalDeposit.amount).desc())
    )
    return [(name or "—", Decimal(str(net))) for name, net in rows.all()]


async def get_goal_monthly_pace(
    session: AsyncSession,
    goal_id: int,
    created_at: datetime,
    window_days: int = 90,
) -> tuple[float, int] | None:
    """Honest monthly saving pace over the recent window, or None if too little data.

    Темп = net (взносы − снятия) за окно / число месяцев окна. Окно ограничено
    возрастом цели: months = min(window_days, age) / 30. Возвращает None при недостатке
    истории (< 2 взносов / возраст < 14 дн / нет прошедшего времени) — лучше не показать
    прогноз, чем соврать. Знак net сохраняется (может быть ≤ 0 — обработка у вызывающего).
    """
    today = today_msk()
    age_days = (today - created_at.date()).days
    if age_days < 14:
        return None

    since = datetime.combine(today - timedelta(days=window_days), datetime.min.time())
    net = await session.scalar(
        select(func.coalesce(func.sum(GoalDeposit.amount), 0)).where(
            GoalDeposit.goal_id == goal_id, GoalDeposit.created_at >= since
        )
    )
    deposits_count = await session.scalar(
        select(func.count()).where(
            GoalDeposit.goal_id == goal_id,
            GoalDeposit.created_at >= since,
            GoalDeposit.amount > 0,
        )
    )
    if (deposits_count or 0) < 2:
        return None

    effective_days = min(window_days, age_days)
    months = effective_days / 30
    if months <= 0:
        return None

    rate_per_month = float(net or 0) / months
    return (rate_per_month, int(deposits_count or 0))
