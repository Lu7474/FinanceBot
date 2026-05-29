"""Debts CRUD: create, payment, deletion, reminders. Isolated from balance/reports."""

from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import MAX_DEBT_AMOUNT, MAX_DEBT_PERSON_NAME
from core.database.models import Debt, DebtPayment, User, moscow_now
from core.exceptions import (
    DebtAlreadyClosed,
    DebtNotFound,
    PaymentExceedsRemaining,
)
from core.utils import today_msk


def _validate_person_name(name: str) -> None:
    if not (0 < len(name) <= MAX_DEBT_PERSON_NAME):
        raise ValueError(f"person_name must be 1..{MAX_DEBT_PERSON_NAME} chars")


def _validate_amount(amount: Decimal) -> None:
    if amount <= 0 or amount > Decimal(str(MAX_DEBT_AMOUNT)):
        raise ValueError(f"amount must be in (0, {MAX_DEBT_AMOUNT}]")


async def get_active_debts(session: AsyncSession, user_id: int) -> list[Debt]:
    """Returns user's active debts. Sort: overdue → nearest due → no due."""
    rows = list(
        await session.scalars(
            select(Debt).where(
                Debt.user_id == user_id,
                Debt.is_closed == False,  # noqa: E712
            )
        )
    )
    today = today_msk()

    def _key(d: Debt) -> tuple:
        if d.due_date and d.due_date < today:
            return (0, d.due_date)
        if d.due_date:
            return (1, d.due_date)
        return (2, d.created_at)

    rows.sort(key=_key)
    return rows


async def get_closed_debts(
    session: AsyncSession, user_id: int, limit: int, offset: int
) -> list[Debt]:
    """Returns closed debts page, sorted by closed_at DESC."""
    return list(
        await session.scalars(
            select(Debt)
            .where(
                Debt.user_id == user_id,
                Debt.is_closed == True,  # noqa: E712
            )
            .order_by(Debt.closed_at.desc(), Debt.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


async def count_closed_debts(session: AsyncSession, user_id: int) -> int:
    """Total number of closed debts (for archive pagination)."""
    res = await session.scalar(
        select(func.count(Debt.id)).where(
            Debt.user_id == user_id,
            Debt.is_closed == True,  # noqa: E712
        )
    )
    return int(res or 0)


async def get_debt(session: AsyncSession, debt_id: int, user_id: int) -> Debt:
    """Returns debt by id with ownership check. Raises DebtNotFound."""
    debt = await session.scalar(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    if not debt:
        raise DebtNotFound()
    return debt


async def get_debt_payments(
    session: AsyncSession, debt_id: int
) -> list[DebtPayment]:
    """Returns all payments for a debt, oldest first (chronological)."""
    return list(
        await session.scalars(
            select(DebtPayment)
            .where(DebtPayment.debt_id == debt_id)
            .order_by(DebtPayment.paid_at.asc(), DebtPayment.id.asc())
        )
    )


async def create_debt(
    session: AsyncSession,
    user_id: int,
    direction: str,
    person_name: str,
    amount: Decimal,
    description: str | None,
    due_date: date_type | None,
) -> Debt:
    """Creates a new debt. remaining initialised to amount."""
    if direction not in ("I", "O"):
        raise ValueError("direction must be 'I' or 'O'")
    _validate_person_name(person_name)
    _validate_amount(amount)
    if description is not None and len(description) > 200:
        raise ValueError("description must be ≤ 200 chars")

    debt = Debt(
        user_id=user_id,
        direction=direction,
        person_name=person_name,
        amount=amount,
        remaining=amount,
        description=description,
        due_date=due_date,
    )
    session.add(debt)
    await session.flush()
    return debt


async def add_payment(
    session: AsyncSession,
    debt_id: int,
    user_id: int,
    amount: Decimal,
    note: str | None,
) -> tuple[Debt, bool]:
    """Records a partial payment. Atomic UPDATE remaining (race-safe).

    Returns (updated_debt, just_closed). Raises DebtNotFound, DebtAlreadyClosed,
    PaymentExceedsRemaining.
    """
    debt = await get_debt(session, debt_id, user_id)
    if debt.is_closed:
        raise DebtAlreadyClosed()
    if amount <= 0 or amount > debt.remaining:
        raise PaymentExceedsRemaining()
    if note is not None and len(note) > 200:
        raise ValueError("note must be ≤ 200 chars")

    # Atomic UPDATE with predicate: protects against concurrent FSM payments.
    # If two FSMs race and both pass the pre-check above, only one UPDATE will
    # match (the other sees remaining already decremented below `amount`).
    result = await session.execute(
        update(Debt)
        .where(
            Debt.id == debt_id,
            Debt.user_id == user_id,
            Debt.is_closed == False,  # noqa: E712
            Debt.remaining >= amount,
        )
        .values(remaining=Debt.remaining - amount)
    )
    if result.rowcount == 0:
        # Race lost: debt was closed or its remaining dropped under amount.
        # Re-read to distinguish the two cases for a precise error.
        fresh = await session.scalar(
            select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
        )
        if fresh and fresh.is_closed:
            raise DebtAlreadyClosed()
        raise PaymentExceedsRemaining()

    # UPDATE succeeded — now safe to record the payment.
    payment = DebtPayment(debt_id=debt_id, amount=amount, note=note)
    session.add(payment)
    await session.flush()
    await session.refresh(debt)

    just_closed = False
    if debt.remaining <= 0:
        now = moscow_now()
        await session.execute(
            update(Debt)
            .where(Debt.id == debt_id, Debt.user_id == user_id)
            .values(is_closed=True, closed_at=now)
        )
        await session.flush()
        await session.refresh(debt)
        just_closed = True

    return debt, just_closed


async def delete_debt(session: AsyncSession, debt_id: int, user_id: int) -> None:
    """Hard delete of debt + cascade of payments. Raises DebtNotFound."""
    debt = await session.scalar(
        select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )
    if not debt:
        raise DebtNotFound()
    # explicit payment delete in case cascade is not honoured by SQLite session
    await session.execute(delete(DebtPayment).where(DebtPayment.debt_id == debt_id))
    await session.execute(
        delete(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    )


async def get_debts_to_remind(
    session: AsyncSession, today: date_type
) -> list[tuple[Debt, User]]:
    """Returns (debt, user) pairs that need a reminder today.

    Rules:
      - tomorrow (due_date == today + 1d): remind once.
      - due today (due_date == today): remind once.
      - overdue (due_date < today): remind every 7 days based on last_reminded_at.
      - dedup: skip if already reminded today (last_reminded_at::date == today).
      - only users with notify_debts=True, not banned, debt not closed.
    """
    tomorrow = today + timedelta(days=1)
    # ТЗ: "раз в 7 дней". last_reminded_at::date <= today-7 → 7 полных суток прошло.
    week_ago = today - timedelta(days=7)

    overdue_window_ok = or_(
        Debt.last_reminded_at.is_(None),
        func.date(Debt.last_reminded_at) <= week_ago,
    )
    not_reminded_today = or_(
        Debt.last_reminded_at.is_(None),
        func.date(Debt.last_reminded_at) != today,
    )

    q = (
        select(Debt, User)
        .join(User, User.id == Debt.user_id)
        .where(
            Debt.is_closed == False,  # noqa: E712
            Debt.due_date.is_not(None),
            User.notify_debts == True,  # noqa: E712
            User.is_banned == False,  # noqa: E712
            not_reminded_today,
            or_(
                Debt.due_date == today,
                Debt.due_date == tomorrow,
                (Debt.due_date < today) & overdue_window_ok,
            ),
        )
    )
    rows = await session.execute(q)
    return [(d, u) for d, u in rows.all()]
