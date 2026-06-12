"""Budgets: CRUD, status calc, alert flag reset, threshold alerts."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import Budget, Record, moscow_now
from core.utils import format_money


async def get_budgets(session: AsyncSession, user_id: int) -> list[Budget]:
    """Returns all active budgets for user."""
    result = await session.execute(
        select(Budget).where(Budget.user_id == user_id, Budget.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())


async def set_budget(
    session: AsyncSession, user_id: int, category: str, amount: Decimal
) -> None:
    """Upserts budget; resets alert flags on insert or update."""
    budget = await session.scalar(
        select(Budget).where(Budget.user_id == user_id, Budget.category == category)
    )
    if budget:
        budget.amount = amount
        budget.alerted_80 = False
        budget.alerted_100 = False
    else:
        session.add(
            Budget(
                user_id=user_id,
                category=category,
                amount=amount,
                is_active=True,
                alerted_80=False,
                alerted_100=False,
            )
        )
    await session.flush()


async def delete_budget(session: AsyncSession, budget_id: int, user_id: int) -> bool:
    """Deletes budget by id, validates ownership."""
    result = await session.execute(
        delete(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
    )
    await session.flush()
    return result.rowcount > 0


async def get_budget_status(
    session: AsyncSession, user_id: int, month: int, year: int
) -> list[dict]:
    """Returns list of {id, category, limit, spent, pct} for all active budgets."""
    budgets = await get_budgets(session, user_id)
    if not budgets:
        return []

    date_from = datetime(year, month, 1)
    if month == 12:
        date_to = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        date_to = datetime(year, month + 1, 1) - timedelta(seconds=1)

    categories = [b.category for b in budgets]
    rows = await session.execute(
        select(Record.category, func.sum(Record.amount).label("total"))
        .where(
            Record.user_id == user_id,
            Record.operation == "-",
            Record.category.in_(categories),
            Record.created_at.between(date_from, date_to),
        )
        .group_by(Record.category)
    )
    spent_map: dict[str, Decimal] = {
        row.category: Decimal(str(row.total)) for row in rows
    }

    result = []
    for budget in budgets:
        spent = spent_map.get(budget.category, Decimal("0"))
        pct = int((spent / budget.amount) * 100) if budget.amount > 0 else 0
        result.append(
            {
                "id": budget.id,
                "category": budget.category,
                "limit": budget.amount,
                "spent": spent,
                "pct": pct,
            }
        )
    return result


async def get_budget_trend(
    session: AsyncSession, user_id: int, months_back: int = 6
) -> dict:
    """Per-month fact vs current limit for active budgets over last `months_back` months.

    Returns {"months": [(year, month), ...] ascending,
             "rows": [{"category", "limit", "spent": [Decimal per month], "over_count"}]}.
    Limit is the current budget.amount (no historical limits stored).
    """
    budgets = await get_budgets(session, user_id)
    if not budgets:
        return {"months": [], "rows": []}

    # Build month list ending at current month (ascending).
    now = moscow_now()
    months: list[tuple[int, int]] = []
    y, m = now.year, now.month
    for _ in range(months_back):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months.reverse()

    date_from = datetime(months[0][0], months[0][1], 1)
    categories = [b.category for b in budgets]

    year_col = func.extract("year", Record.created_at)
    month_col = func.extract("month", Record.created_at)
    rows = await session.execute(
        select(
            Record.category,
            year_col.label("y"),
            month_col.label("m"),
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.user_id == user_id,
            Record.operation == "-",
            Record.category.in_(categories),
            Record.created_at >= date_from,
        )
        .group_by(Record.category, year_col, month_col)
    )
    spent_map: dict[tuple[str, int, int], Decimal] = {
        (row.category, int(row.y), int(row.m)): Decimal(str(row.total)) for row in rows
    }

    result_rows = []
    for budget in budgets:
        spent = [
            spent_map.get((budget.category, yr, mo), Decimal("0")) for yr, mo in months
        ]
        over_count = sum(1 for s in spent if s > budget.amount)
        result_rows.append(
            {
                "category": budget.category,
                "limit": budget.amount,
                "spent": spent,
                "over_count": over_count,
            }
        )
    return {"months": months, "rows": result_rows}


async def reset_budget_alerts_if_new_month(
    session: AsyncSession, budget: Budget
) -> bool:
    """Resets alert flags if current month differs from last_reset_month. Returns True if reset occurred."""
    now = moscow_now()
    current_yyyymm = now.year * 100 + now.month
    if budget.last_reset_month != current_yyyymm:
        budget.alerted_80 = False
        budget.alerted_100 = False
        budget.last_reset_month = current_yyyymm
        return True
    return False


async def check_and_alert_budget(
    session: AsyncSession, user_id: int, category: str, amount_added: Decimal
) -> list[str]:
    """Returns list of alert strings (does not send anything). Commits flag changes."""
    budget = await session.scalar(
        select(Budget).where(
            Budget.user_id == user_id,
            Budget.category == category,
            Budget.is_active == True,  # noqa: E712
        )
    )
    if not budget:
        return []

    reset = await reset_budget_alerts_if_new_month(session, budget)

    now = moscow_now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    spent = await session.scalar(
        select(func.coalesce(func.sum(Record.amount), 0)).where(
            Record.user_id == user_id,
            Record.operation == "-",
            Record.category == category,
            Record.created_at >= month_start,
        )
    )
    spent = Decimal(str(spent))
    limit = budget.amount

    alerts = []
    if not budget.alerted_100 and spent >= limit:
        alerts.append(
            f"🚨 {category}: бюджет превышен! ({format_money(float(spent))} из {format_money(float(limit))})"
        )
        budget.alerted_100 = True
        budget.alerted_80 = True
    elif not budget.alerted_80 and limit > 0 and spent >= limit * Decimal("0.8"):
        alerts.append(
            f"⚠️ {category}: потрачено 80% бюджета ({format_money(float(spent))} из {format_money(float(limit))})"
        )
        budget.alerted_80 = True

    if alerts or reset:
        await session.flush()

    return alerts
