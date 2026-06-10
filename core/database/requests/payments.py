"""Payment reminders CRUD: create, edit, mark paid (with recurrence), reminders.

Isolated from balance/reports — paying a reminder never creates a Record.
"""

from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MAX_PAYMENT_AMOUNT, MAX_PAYMENT_TITLE
from core.database.models import Payment, User, moscow_now
from core.exceptions import PaymentNotFound
from core.utils import next_due_date

VALID_PERIODS = ("none", "month", "year")


def _validate_title(title: str) -> None:
    if not (0 < len(title) <= MAX_PAYMENT_TITLE):
        raise ValueError(f"title must be 1..{MAX_PAYMENT_TITLE} chars")


def _validate_amount(amount: Decimal | None) -> None:
    if amount is None:
        return
    if amount <= 0 or amount > Decimal(str(MAX_PAYMENT_AMOUNT)):
        raise ValueError(f"amount must be in (0, {MAX_PAYMENT_AMOUNT}]")


async def get_active_payments(session: AsyncSession, user_id: int) -> list[Payment]:
    """Returns user's active payments. Sort: overdue → nearest due → later."""
    rows = list(
        await session.scalars(
            select(Payment).where(
                Payment.user_id == user_id,
                Payment.is_active == True,  # noqa: E712
            )
        )
    )
    rows.sort(key=lambda p: (p.due_date, p.id))
    return rows


async def get_payment(
    session: AsyncSession, payment_id: int, user_id: int
) -> Payment:
    """Returns payment by id with ownership check. Raises PaymentNotFound."""
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id, Payment.user_id == user_id)
    )
    if not payment:
        raise PaymentNotFound()
    return payment


async def create_payment(
    session: AsyncSession,
    user_id: int,
    title: str,
    amount: Decimal | None,
    due_date: date_type,
    period: str,
) -> Payment:
    """Creates a new payment reminder."""
    _validate_title(title)
    _validate_amount(amount)
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of {VALID_PERIODS}")

    payment = Payment(
        user_id=user_id,
        title=title,
        amount=amount,
        due_date=due_date,
        period=period,
    )
    session.add(payment)
    await session.flush()
    return payment


async def update_payment(
    session: AsyncSession,
    payment_id: int,
    user_id: int,
    *,
    title: str | None = None,
    amount: Decimal | None = None,
    clear_amount: bool = False,
    due_date: date_type | None = None,
    period: str | None = None,
) -> Payment:
    """Updates given fields of a payment. `clear_amount` sets amount to NULL
    (floating sum). Raises PaymentNotFound."""
    payment = await get_payment(session, payment_id, user_id)
    if title is not None:
        _validate_title(title)
        payment.title = title
    if clear_amount:
        payment.amount = None
    elif amount is not None:
        _validate_amount(amount)
        payment.amount = amount
    if due_date is not None:
        payment.due_date = due_date
    if period is not None:
        if period not in VALID_PERIODS:
            raise ValueError(f"period must be one of {VALID_PERIODS}")
        payment.period = period
    await session.flush()
    return payment


async def mark_paid(
    session: AsyncSession, payment_id: int, user_id: int
) -> tuple[Payment, date_type | None]:
    """Marks a payment as paid.

    Recurring → due_date rolls to the next cycle, reminder counter reset,
    returns (payment, next_due). One-time → is_active=False, returns (payment, None).
    Raises PaymentNotFound.
    """
    payment = await get_payment(session, payment_id, user_id)
    now = moscow_now()
    payment.last_paid_at = now

    if payment.period == "none":
        payment.is_active = False
        next_due = None
    else:
        next_due = next_due_date(payment.due_date, payment.period)
        payment.due_date = next_due
        payment.last_reminded_at = None  # re-arm reminder for the next cycle

    await session.flush()
    return payment, next_due


async def delete_payment(
    session: AsyncSession, payment_id: int, user_id: int
) -> None:
    """Hard delete of a payment. Raises PaymentNotFound."""
    payment = await get_payment(session, payment_id, user_id)
    await session.delete(payment)
    await session.flush()


async def get_payments_to_remind(
    session: AsyncSession, today: date_type
) -> list[tuple[Payment, User]]:
    """Returns (payment, user) pairs that need a reminder today.

    Rules mirror debt reminders:
      - tomorrow / due today: remind once.
      - overdue: remind every 7 days based on last_reminded_at.
      - dedup: skip if already reminded today.
      - only active payments, users with notify_payments=True, not banned.
    """
    tomorrow = today + timedelta(days=1)
    week_ago = today - timedelta(days=7)

    overdue_window_ok = or_(
        Payment.last_reminded_at.is_(None),
        func.date(Payment.last_reminded_at) <= week_ago,
    )
    not_reminded_today = or_(
        Payment.last_reminded_at.is_(None),
        func.date(Payment.last_reminded_at) != today,
    )

    q = (
        select(Payment, User)
        .join(User, User.id == Payment.user_id)
        .where(
            Payment.is_active == True,  # noqa: E712
            User.notify_payments == True,  # noqa: E712
            User.is_banned == False,  # noqa: E712
            not_reminded_today,
            or_(
                Payment.due_date == today,
                Payment.due_date == tomorrow,
                (Payment.due_date < today) & overdue_window_ok,
            ),
        )
    )
    rows = await session.execute(q)
    return [(p, u) for p, u in rows.all()]
