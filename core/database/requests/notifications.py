"""DB queries for notification summaries: weekly, monthly, daily."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import Record
from core.database.requests._common import SYSTEM_CATEGORIES


async def get_weekly_summary_data(
    session: AsyncSession,
    user_id: int,
    week_start: datetime,
    week_end: datetime,
) -> dict:
    """Return income, expense, top categories, prev-week expense for a week range."""
    result = await session.execute(
        select(
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(
            Record.user_id == user_id,
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= week_start,
            Record.created_at <= week_end,
        )
    )
    row = result.one()
    income = Decimal(str(row.income))
    expense = Decimal(str(row.expense))

    if income == 0 and expense == 0:
        return {}

    top_result = await session.execute(
        select(Record.category, func.sum(Record.amount).label("total"))
        .where(
            Record.user_id == user_id,
            Record.operation == "-",
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= week_start,
            Record.created_at <= week_end,
        )
        .group_by(Record.category)
        .order_by(func.sum(Record.amount).desc())
        .limit(5)
    )
    top_categories = [
        (r.category, Decimal(str(r.total))) for r in top_result.fetchall()
    ]

    prev_start = week_start - timedelta(days=7)
    prev_end = week_end - timedelta(days=7)
    prev_expense = Decimal(
        str(
            await session.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            case((Record.operation == "-", Record.amount), else_=0)
                        ),
                        0,
                    )
                ).where(
                    Record.user_id == user_id,
                    Record.category.not_in(SYSTEM_CATEGORIES),
                    Record.created_at >= prev_start,
                    Record.created_at <= prev_end,
                )
            )
        )
    )

    return {
        "week_start": week_start.date(),
        "week_end": week_end.date(),
        "income": income,
        "expense": expense,
        "top_categories": top_categories,
        "prev_expense": prev_expense,
    }


async def get_monthly_summary_data(
    session: AsyncSession,
    user_id: int,
    month: int,
    year: int,
) -> dict:
    """Return income, expense, top categories for a calendar month. Empty dict if no data."""
    month_start = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        month_end = datetime(year + 1, 1, 1, 0, 0, 0) - timedelta(microseconds=1)
    else:
        month_end = datetime(year, month + 1, 1, 0, 0, 0) - timedelta(microseconds=1)

    result = await session.execute(
        select(
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(
            Record.user_id == user_id,
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= month_start,
            Record.created_at <= month_end,
        )
    )
    row = result.one()
    income = Decimal(str(row.income))
    expense = Decimal(str(row.expense))

    if income == 0 and expense == 0:
        return {}

    top_result = await session.execute(
        select(Record.category, func.sum(Record.amount).label("total"))
        .where(
            Record.user_id == user_id,
            Record.operation == "-",
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= month_start,
            Record.created_at <= month_end,
        )
        .group_by(Record.category)
        .order_by(func.sum(Record.amount).desc())
        .limit(5)
    )
    top_categories = [
        (r.category, Decimal(str(r.total))) for r in top_result.fetchall()
    ]

    return {
        "income": income,
        "expense": expense,
        "top_categories": top_categories,
    }


async def get_daily_summary_data(
    session: AsyncSession,
    user_id: int,
    target_date: date,
) -> dict:
    """Return expense-by-category, totals, and month total for a given date."""
    day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    day_end = datetime(
        target_date.year, target_date.month, target_date.day, 23, 59, 59, 999999
    )

    cat_result = await session.execute(
        select(Record.category, func.sum(Record.amount).label("total"))
        .where(
            Record.user_id == user_id,
            Record.operation == "-",
            Record.category.not_in(SYSTEM_CATEGORIES),
            Record.created_at >= day_start,
            Record.created_at <= day_end,
        )
        .group_by(Record.category)
        .order_by(func.sum(Record.amount).desc())
    )
    expense_by_cat = [
        (r.category, Decimal(str(r.total))) for r in cat_result.fetchall()
    ]

    total_income = Decimal(
        str(
            await session.scalar(
                select(func.coalesce(func.sum(Record.amount), 0)).where(
                    Record.user_id == user_id,
                    Record.operation == "+",
                    Record.category.not_in(SYSTEM_CATEGORIES),
                    Record.created_at >= day_start,
                    Record.created_at <= day_end,
                )
            )
        )
    )
    total_expense = sum(amt for _, amt in expense_by_cat)

    if not expense_by_cat and total_income == 0:
        return {}

    month_start = datetime(target_date.year, target_date.month, 1, 0, 0, 0)
    month_total_expense = Decimal(
        str(
            await session.scalar(
                select(func.coalesce(func.sum(Record.amount), 0)).where(
                    Record.user_id == user_id,
                    Record.operation == "-",
                    Record.category.not_in(SYSTEM_CATEGORIES),
                    Record.created_at >= month_start,
                    Record.created_at <= day_end,
                )
            )
        )
    )

    return {
        "date": target_date,
        "expense_by_cat": expense_by_cat,
        "total_income": total_income,
        "total_expense": total_expense,
        "month_total_expense": month_total_expense,
    }
