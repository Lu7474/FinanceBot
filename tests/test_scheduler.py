"""Tests for scheduler senders, retry logic, reminder formatters and setup.

Covers the async sender side of core/scheduler.py (delivery, skip rules,
flood-control retry, last_reminded_at side effects) plus the debt/payment
reminder formatters and setup_scheduler job wiring. Formatter tests for the
weekly/monthly/daily summaries live in test_notifications.py.
"""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage
from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Debt, Payment, Record, User, moscow_now
from core.database.requests.users import set_user
from core.scheduler import (
    _format_debt_reminder,
    _format_payment_reminder,
    _send_with_retry,
    send_daily_summary,
    send_debt_reminders,
    send_monthly_report,
    send_payment_reminders,
    send_reminders,
    send_weekly_report,
    setup_scheduler,
)
from core.utils import today_msk
from tests.conftest import test_session

# ==================== Helpers ====================


class FakeBot:
    """Records send_message calls; optionally raises a per-call exception."""

    def __init__(self, side_effects=None):
        # side_effects: list of exceptions (or None) consumed per send_message call.
        self.sent: list[tuple[int, str, dict]] = []
        self._side_effects = list(side_effects) if side_effects else None

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        if self._side_effects:
            exc = self._side_effects.pop(0)
            if exc is not None:
                raise exc


def _send_msg() -> SendMessage:
    return SendMessage(chat_id=1, text="x")


def _forbidden() -> TelegramForbiddenError:
    return TelegramForbiddenError(method=_send_msg(), message="blocked")


def _bad_request() -> TelegramBadRequest:
    return TelegramBadRequest(method=_send_msg(), message="bad")


def _retry_after(seconds: int = 1) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=_send_msg(), message="flood", retry_after=seconds)


async def _make_user(tg_id: int, **flags) -> int:
    """Create a notifiable user (flags like notify_weekly=True) in its own session."""
    async with test_session() as s:
        user = await set_user(s, tg_id, name="Test")
        for k, v in flags.items():
            setattr(user, k, v)
        uid = user.id  # capture before commit expires the instance
        await s.commit()
        return uid


def _rec(user_id: int, op: str, amount, cat: str, dt: datetime) -> Record:
    return Record(
        user_id=user_id,
        operation=op,
        amount=Decimal(str(amount)),
        category=cat,
        created_at=dt,
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make asyncio.sleep a no-op inside the scheduler (retry backoff + pacing)."""

    async def _noop(*_a, **_k):
        return None

    import core.scheduler as sched

    monkeypatch.setattr(sched.asyncio, "sleep", _noop)


# ==================== _send_with_retry ====================


@pytest.mark.asyncio
async def test_send_with_retry_success_first_try():
    bot = FakeBot()
    await _send_with_retry(bot, 100, "hi", parse_mode="HTML")
    assert bot.sent == [(100, "hi", {"parse_mode": "HTML"})]


@pytest.mark.asyncio
async def test_send_with_retry_retries_once_on_flood():
    bot = FakeBot(side_effects=[_retry_after(2), None])
    await _send_with_retry(bot, 100, "hi")
    # Two attempts, second succeeds.
    assert len(bot.sent) == 2


@pytest.mark.asyncio
async def test_send_with_retry_propagates_on_second_failure():
    bot = FakeBot(side_effects=[_retry_after(1), _forbidden()])
    with pytest.raises(TelegramForbiddenError):
        await _send_with_retry(bot, 100, "hi")
    assert len(bot.sent) == 2


# ==================== send_weekly_report ====================


@pytest.mark.asyncio
async def test_send_weekly_report_delivers_to_user_with_data():
    uid = await _make_user(1, notify_weekly=True)
    async with test_session() as s:
        s.add(_rec(uid, "-", 500, "Еда", moscow_now()))
        s.add(_rec(uid, "+", 1000, "Зарплата", moscow_now()))
        await s.commit()

    bot = FakeBot()
    await send_weekly_report(bot, test_session)

    assert len(bot.sent) == 1
    chat_id, text, kwargs = bot.sent[0]
    assert kwargs.get("parse_mode") == "HTML"
    assert "Неделя" in text


@pytest.mark.asyncio
async def test_send_weekly_report_skips_user_without_data():
    # User opted in but has no records this week → empty text → no send.
    await _make_user(1, notify_weekly=True)
    bot = FakeBot()
    await send_weekly_report(bot, test_session)
    assert bot.sent == []


@pytest.mark.asyncio
async def test_send_weekly_report_swallows_forbidden():
    uid = await _make_user(1, notify_weekly=True)
    async with test_session() as s:
        s.add(_rec(uid, "-", 500, "Еда", moscow_now()))
        await s.commit()

    bot = FakeBot(side_effects=[_forbidden()])
    # Blocked user must not crash the whole run.
    await send_weekly_report(bot, test_session)
    assert len(bot.sent) == 1


# ==================== send_monthly_report ====================


@pytest.mark.asyncio
async def test_send_monthly_report_delivers():
    uid = await _make_user(1, notify_monthly=True)
    async with test_session() as s:
        s.add(_rec(uid, "-", 700, "Еда", moscow_now()))
        s.add(_rec(uid, "+", 2000, "Зарплата", moscow_now()))
        await s.commit()

    bot = FakeBot()
    await send_monthly_report(bot, test_session)
    assert len(bot.sent) == 1
    assert bot.sent[0][2].get("parse_mode") == "HTML"


# ==================== send_daily_summary ====================


@pytest.mark.asyncio
async def test_send_daily_summary_delivers():
    uid = await _make_user(1, notify_daily=True)
    async with test_session() as s:
        s.add(_rec(uid, "-", 300, "Кафе", moscow_now()))
        await s.commit()

    bot = FakeBot()
    await send_daily_summary(bot, test_session)
    assert len(bot.sent) == 1
    assert "Итоги сегодня" in bot.sent[0][1]


@pytest.mark.asyncio
async def test_send_daily_summary_skips_user_without_records_today():
    uid = await _make_user(1, notify_daily=True)
    async with test_session() as s:
        # Record from 3 days ago counts user as notifiable but not for *today*.
        s.add(_rec(uid, "-", 300, "Кафе", moscow_now() - timedelta(days=3)))
        await s.commit()

    bot = FakeBot()
    await send_daily_summary(bot, test_session)
    assert bot.sent == []


# ==================== send_reminders ====================


@pytest.mark.asyncio
async def test_send_reminders_delivers_and_sets_last_reminded():
    uid = await _make_user(1, notify_reminder=True)
    async with test_session() as s:
        # Last record 5 days ago → eligible for reminder.
        s.add(_rec(uid, "-", 100, "Еда", moscow_now() - timedelta(days=5)))
        await s.commit()

    bot = FakeBot()
    await send_reminders(bot, test_session)

    assert len(bot.sent) == 1
    assert "не добавляли записи" in bot.sent[0][1]
    async with test_session() as s:
        user = await s.get(User, uid)
        assert user.last_reminded_at is not None


@pytest.mark.asyncio
async def test_send_reminders_skips_recent_record():
    uid = await _make_user(1, notify_reminder=True)
    async with test_session() as s:
        # Recorded today → user is active, no reminder.
        s.add(_rec(uid, "-", 100, "Еда", moscow_now()))
        await s.commit()

    bot = FakeBot()
    await send_reminders(bot, test_session)
    assert bot.sent == []


@pytest.mark.asyncio
async def test_send_reminders_skips_recently_reminded():
    uid = await _make_user(1, notify_reminder=True)
    async with test_session() as s:
        s.add(_rec(uid, "-", 100, "Еда", moscow_now() - timedelta(days=5)))
        user = await s.get(User, uid)
        user.last_reminded_at = moscow_now()  # reminded today already
        await s.commit()

    bot = FakeBot()
    await send_reminders(bot, test_session)
    assert bot.sent == []


# ==================== _format_debt_reminder ====================


def _debt(direction="O", person="Иван", remaining="5000", due=None) -> Debt:
    return Debt(
        user_id=1,
        direction=direction,
        person_name=person,
        amount=Decimal(remaining),
        remaining=Decimal(remaining),
        due_date=due,
    )


def test_format_debt_reminder_tomorrow():
    today = date(2025, 5, 14)
    text = _format_debt_reminder(_debt(due=today + timedelta(days=1)), today)
    assert "завтра" in text
    assert "Ты должен Иван" in text


def test_format_debt_reminder_today():
    today = date(2025, 5, 14)
    text = _format_debt_reminder(_debt(due=today), today)
    assert "сегодня" in text


def test_format_debt_reminder_overdue():
    today = date(2025, 5, 14)
    text = _format_debt_reminder(_debt(due=today - timedelta(days=3)), today)
    assert "истёк" in text
    assert "3 дней" in text


def test_format_debt_reminder_direction_in():
    today = date(2025, 5, 14)
    text = _format_debt_reminder(_debt(direction="I", due=today), today)
    assert "должен тебе" in text


def test_format_debt_reminder_escapes_html():
    today = date(2025, 5, 14)
    text = _format_debt_reminder(_debt(person="<b>x</b>", due=today), today)
    assert "<b>x</b>" not in text.replace("<b>Напоминание", "")
    assert "&lt;b&gt;x&lt;/b&gt;" in text


# ==================== _format_payment_reminder ====================


def _payment(title="Налог", amount="1000", period="month", due=None) -> Payment:
    return Payment(
        user_id=1,
        title=title,
        amount=Decimal(amount) if amount is not None else None,
        due_date=due,
        period=period,
    )


def test_format_payment_reminder_with_amount_and_period():
    today = date(2025, 5, 14)
    text = _format_payment_reminder(_payment(due=today + timedelta(days=1)), today)
    assert "завтра" in text
    assert "1 000" in text
    assert "Периодичность" in text


def test_format_payment_reminder_floating_amount():
    today = date(2025, 5, 14)
    text = _format_payment_reminder(_payment(amount=None, due=today), today)
    # No amount line, just the title.
    assert "Налог" in text
    assert "сегодня" in text


def test_format_payment_reminder_overdue():
    today = date(2025, 5, 14)
    text = _format_payment_reminder(_payment(due=today - timedelta(days=2)), today)
    assert "истёк" in text


def test_format_payment_reminder_escapes_html():
    today = date(2025, 5, 14)
    text = _format_payment_reminder(_payment(title="<i>t</i>", due=today), today)
    assert "&lt;i&gt;t&lt;/i&gt;" in text


# ==================== send_debt_reminders ====================


@pytest.mark.asyncio
async def test_send_debt_reminders_delivers_and_stamps():
    uid = await _make_user(1, notify_debts=True)
    async with test_session() as s:
        s.add(
            Debt(
                user_id=uid,
                direction="O",
                person_name="Иван",
                amount=Decimal("5000"),
                remaining=Decimal("5000"),
                due_date=today_msk(),
            )
        )
        await s.commit()

    bot = FakeBot()
    await send_debt_reminders(bot, test_session)

    assert len(bot.sent) == 1
    assert "долге" in bot.sent[0][1]
    async with test_session() as s:
        debt = (await s.execute(select(Debt))).scalars().first()
        assert debt.last_reminded_at is not None


@pytest.mark.asyncio
async def test_send_debt_reminders_skips_when_no_due():
    uid = await _make_user(1, notify_debts=True)
    async with test_session() as s:
        s.add(
            Debt(
                user_id=uid,
                direction="O",
                person_name="Иван",
                amount=Decimal("5000"),
                remaining=Decimal("5000"),
                due_date=None,  # no due date → never reminded
            )
        )
        await s.commit()

    bot = FakeBot()
    await send_debt_reminders(bot, test_session)
    assert bot.sent == []


# ==================== send_payment_reminders ====================


@pytest.mark.asyncio
async def test_send_payment_reminders_delivers_and_stamps():
    uid = await _make_user(1, notify_payments=True)
    async with test_session() as s:
        s.add(
            Payment(
                user_id=uid,
                title="Налог",
                amount=Decimal("1000"),
                due_date=today_msk(),
                period="month",
            )
        )
        await s.commit()

    bot = FakeBot()
    await send_payment_reminders(bot, test_session)

    assert len(bot.sent) == 1
    assert "платеже" in bot.sent[0][1]
    async with test_session() as s:
        pay = (await s.execute(select(Payment))).scalars().first()
        assert pay.last_reminded_at is not None


# ==================== setup_scheduler ====================


@pytest.mark.asyncio
async def test_setup_scheduler_registers_all_jobs():
    bot = FakeBot()
    scheduler = setup_scheduler(bot, test_session)
    try:
        ids = {job.id for job in scheduler.get_jobs()}
        assert ids == {
            "weekly_report",
            "monthly_report",
            "daily_summary",
            "reminders",
            "debt_reminders",
            "payment_reminders",
        }
    finally:
        scheduler.shutdown(wait=False)
