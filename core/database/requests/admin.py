"""Admin queries: user listing, stats, bans, cascading delete, broadcast targets."""

import csv
import io
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

from ._common import SYSTEM_CATEGORIES


async def get_all_users(
    session: AsyncSession,
    offset: int = 0,
    limit: int = 10,
    filter_mode: str = "all",
    sort_by: str = "date",
) -> List[User]:
    last_act = (
        select(Record.user_id, func.max(Record.created_at).label("last_at"))
        .group_by(Record.user_id)
        .subquery()
    )
    query = select(User).outerjoin(last_act, User.id == last_act.c.user_id)
    if filter_mode == "active":
        query = query.where(User.is_banned == False)  # noqa: E712
    elif filter_mode == "banned":
        query = query.where(User.is_banned == True)  # noqa: E712
    if sort_by == "activity":
        query = query.order_by(desc(last_act.c.last_at).nulls_last())
    elif sort_by == "name":
        query = query.order_by(User.name.asc().nulls_last())
    else:
        query = query.order_by(User.created_at.desc())
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


async def count_users(session: AsyncSession, filter_mode: str = "all") -> int:
    query = select(func.count(User.id))
    if filter_mode == "active":
        query = query.where(User.is_banned == False)  # noqa: E712
    elif filter_mode == "banned":
        query = query.where(User.is_banned == True)  # noqa: E712
    return await session.scalar(query) or 0


async def get_user_stats(session: AsyncSession, user_id: int) -> dict:
    """Returns record counts, totals and last activity for a user in one query."""
    result = await session.execute(
        select(
            func.count(Record.id).label("total"),
            func.coalesce(
                func.sum(case((Record.operation == "+", 1), else_=0)), 0
            ).label("income_count"),
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income_sum"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense_sum"),
            func.max(Record.created_at).label("last_activity"),
        ).where(
            Record.user_id == user_id,
            Record.category.not_in(SYSTEM_CATEGORIES),
        )
    )
    row = result.one()
    total = row.total or 0
    income_count = row.income_count or 0
    return {
        "total_records": total,
        "income_count": income_count,
        "expense_count": total - income_count,
        "income_sum": Decimal(str(row.income_sum)),
        "expense_sum": Decimal(str(row.expense_sum)),
        "last_activity": row.last_activity,
    }


async def ban_user(session: AsyncSession, tg_id: int, is_banned: bool) -> bool:
    try:
        result = await session.execute(
            update(User).where(User.tg_id == tg_id).values(is_banned=is_banned)
        )
        await session.flush()
        return result.rowcount > 0
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при изменении бана пользователя {tg_id}: {e}")
        return False


async def delete_user_cascade(session: AsyncSession, tg_id: int) -> bool:
    """Explicit delete of user and all related rows.

    Bulk session.execute(delete(...)) emits raw SQL and bypasses ORM relationship
    cascade. Without PRAGMA foreign_keys=ON SQLite won't enforce ondelete=CASCADE
    either — so child tables (GoalDeposit, SavingsItem) must be deleted explicitly
    before their parents.
    """
    try:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return False
        uid = user.id

        # GoalDeposit -> Goal
        await session.execute(
            delete(GoalDeposit).where(
                GoalDeposit.goal_id.in_(select(Goal.id).where(Goal.user_id == uid))
            )
        )
        await session.execute(delete(Goal).where(Goal.user_id == uid))

        # SavingsItem -> SavingsSnapshot
        await session.execute(
            delete(SavingsItem).where(
                SavingsItem.snapshot_id.in_(
                    select(SavingsSnapshot.id).where(SavingsSnapshot.user_id == uid)
                )
            )
        )
        await session.execute(delete(SavingsSnapshot).where(SavingsSnapshot.user_id == uid))

        # CategoryKeyword -> UserCategory
        await session.execute(delete(CategoryKeyword).where(CategoryKeyword.user_id == uid))
        await session.execute(delete(UserCategory).where(UserCategory.user_id == uid))

        # Independent of user-children
        await session.execute(delete(Budget).where(Budget.user_id == uid))
        await session.execute(delete(WealthItem).where(WealthItem.user_id == uid))
        await session.execute(delete(Record).where(Record.user_id == uid))
        await session.execute(delete(Account).where(Account.user_id == uid))

        await session.delete(user)
        await session.flush()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при удалении пользователя {tg_id}: {e}")
        return False


async def get_bot_stats(session: AsyncSession) -> dict:
    now = datetime.now(ZoneInfo(TIMEZONE))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = (now - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    total_users = await session.scalar(select(func.count(User.id))) or 0
    banned_users = (
        await session.scalar(
            select(func.count(User.id)).where(User.is_banned == True)  # noqa: E712
        )
        or 0
    )
    total_accounts = await session.scalar(select(func.count(Account.id))) or 0
    total_records = (
        await session.scalar(
            select(func.count(Record.id)).where(
                Record.category.not_in(SYSTEM_CATEGORIES)
            )
        )
        or 0
    )
    new_today = (
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
        or 0
    )
    new_week = (
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= week_start)
        )
        or 0
    )
    active_week = (
        await session.scalar(
            select(func.count(func.distinct(Record.user_id))).where(
                Record.created_at >= week_start
            )
        )
        or 0
    )

    return {
        "total_users": total_users,
        "banned_users": banned_users,
        "total_accounts": total_accounts,
        "total_records": total_records,
        "new_today": new_today,
        "new_week": new_week,
        "active_week": active_week,
    }


async def get_all_tg_ids(session: AsyncSession, skip_banned: bool = True) -> List[int]:
    query = select(User.tg_id)
    if skip_banned:
        query = query.where(User.is_banned == False)  # noqa: E712
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_top_users(session: AsyncSession, limit: int = 5) -> list:
    """Returns [(User, record_count), ...] sorted by activity."""
    result = await session.execute(
        select(User, func.count(Record.id).label("cnt"))
        .join(Record, Record.user_id == User.id)
        .where(Record.category.not_in(SYSTEM_CATEGORIES))
        .group_by(User.id)
        .order_by(func.count(Record.id).desc())
        .limit(limit)
    )
    return [(row.User, row.cnt) for row in result.fetchall()]


async def find_users_by_name(session: AsyncSession, query_str: str) -> List[User]:
    result = await session.execute(
        select(User).where(User.name.ilike(f"%{query_str}%")).limit(10)
    )
    return list(result.scalars().all())


async def get_user_last_activity(
    session: AsyncSession, user_id: int
) -> Optional[datetime]:
    return await session.scalar(
        select(func.max(Record.created_at)).where(Record.user_id == user_id)
    )


async def get_active_user_tg_ids(session: AsyncSession, days: int = 7) -> List[int]:
    since = datetime.now(ZoneInfo(TIMEZONE)) - timedelta(days=days)
    result = await session.execute(
        select(User.tg_id)
        .join(Record, Record.user_id == User.id)
        .where(Record.created_at >= since, User.is_banned == False)  # noqa: E712
        .distinct()
    )
    return list(result.scalars().all())


async def get_power_user_tg_ids(
    session: AsyncSession, min_records: int = 10
) -> List[int]:
    result = await session.execute(
        select(User.tg_id)
        .join(Record, Record.user_id == User.id)
        .where(User.is_banned == False)  # noqa: E712
        .group_by(User.id)
        .having(func.count(Record.id) >= min_records)
    )
    return list(result.scalars().all())


async def get_user_records_csv(session: AsyncSession, user_id: int) -> bytes:
    result = await session.execute(
        select(Record)
        .options(selectinload(Record.account))
        .where(Record.user_id == user_id)
        .order_by(Record.created_at)
    )
    records = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Дата", "Тип", "Сумма", "Категория", "Счёт"])
    for r in records:
        writer.writerow(
            [
                r.created_at.strftime("%d.%m.%Y %H:%M"),
                "Доход" if r.operation == "+" else "Расход",
                float(r.amount),
                r.category,
                r.account.name if r.account else "—",
            ]
        )
    return output.getvalue().encode("utf-8-sig")  # BOM для корректного открытия в Excel
