"""Notification scheduler: weekly, monthly, daily summaries and reminders."""

import asyncio
import html
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import TIMEZONE
from core.database.models import Debt, moscow_now
from core.database.requests.debts import get_debts_to_remind
from core.database.requests.notifications import (
    get_daily_summary_data,
    get_monthly_summary_data,
    get_weekly_summary_data,
)
from core.database.requests.users import (
    get_last_record_date,
    get_notifiable_users,
    update_last_reminded,
)
from core.keyboards import debt_reminder_open_keyboard
from core.utils import RU_MONTHS, RU_MONTHS_GEN, format_money, today_msk

_RU_MONTHS_SHORT = {
    1: "Янв",
    2: "Фев",
    3: "Мар",
    4: "Апр",
    5: "Май",
    6: "Июн",
    7: "Июл",
    8: "Авг",
    9: "Сен",
    10: "Окт",
    11: "Ноя",
    12: "Дек",
}


# ==================== Formatters ====================


def _n(v: Decimal) -> str:
    """Format Decimal as plain integer with space thousands separator."""
    return f"{round(v):,}".replace(",", " ")


def _d(v: Decimal) -> str:
    """Format delta with leading + for positive values."""
    sign = "+" if v >= 0 else ""
    return f"{sign}{round(v):,}".replace(",", " ")


def format_weekly_summary(data: dict) -> str:
    """Format weekly summary. Returns empty string if no data."""
    if not data:
        return ""

    week_start: date = data["week_start"]
    week_end: date = data["week_end"]
    income: Decimal = data["income"]
    expense: Decimal = data["expense"]
    top_categories: list = data["top_categories"]
    prev_expense: Decimal = data["prev_expense"]

    balance = income - expense
    if balance >= 0:
        balance_fmt = f"+{format_money(float(balance))}"
    else:
        balance_fmt = f"−{format_money(float(abs(balance)))}"

    if week_start.month == week_end.month:
        period = f"{week_start.day}–{week_end.day} {RU_MONTHS_GEN[week_start.month]}"
    else:
        period = (
            f"{week_start.day} {RU_MONTHS_GEN[week_start.month]}–"
            f"{week_end.day} {RU_MONTHS_GEN[week_end.month]}"
        )

    lines = [
        f"📊 Неделя: {period}\n",
        f"Расходы: {format_money(float(expense))}",
        f"Доходы: {format_money(float(income))}",
        f"Баланс: {balance_fmt}",
    ]

    if top_categories:
        lines.append("\nТоп расходов:")
        for cat, amount in top_categories:
            lines.append(f"{html.escape(cat)} — {format_money(float(amount))}")

    delta = expense - prev_expense
    if delta > 0:
        lines.append(f"\nК прошлой неделе: +{format_money(float(delta))} расходов")
    elif delta < 0:
        lines.append(f"\nК прошлой неделе: −{format_money(float(abs(delta)))} расходов")
    else:
        lines.append("\nК прошлой неделе: без изменений")

    return "\n".join(lines)


def format_monthly_summary(
    curr_data: dict, prev_data: dict, month: int, year: int
) -> str:
    """Format monthly summary as HTML (uses <pre> table when comparison is available).

    Falls back to simplified plain format when prev_data is empty.
    """
    if not curr_data:
        return ""

    curr_income: Decimal = curr_data["income"]
    curr_expense: Decimal = curr_data["expense"]
    top_categories: list = curr_data.get("top_categories", [])
    month_name = RU_MONTHS[month]
    header = f"📅 {month_name} {year}"

    if prev_data:
        prev_income: Decimal = prev_data["income"]
        prev_expense: Decimal = prev_data["expense"]
        curr_bal = curr_income - curr_expense
        prev_bal = prev_income - prev_expense
        d_inc = curr_income - prev_income
        d_exp = curr_expense - prev_expense
        d_bal = curr_bal - prev_bal

        m_inc = "✅" if d_inc >= 0 else "⚠️"
        m_exp = "✅" if d_exp <= 0 else "⚠️"
        m_bal = "✅" if d_bal >= 0 else "⚠️"

        prev_month = month - 1 if month > 1 else 12
        prev_sn = _RU_MONTHS_SHORT[prev_month]
        curr_sn = _RU_MONTHS_SHORT[month]

        # Dynamic column widths
        nums = [
            _n(curr_income),
            _n(curr_expense),
            _n(abs(curr_bal)),
            _n(prev_income),
            _n(prev_expense),
            _n(abs(prev_bal)),
        ]
        col_w = max(len(v) for v in nums + [prev_sn, curr_sn])
        deltas = [_d(d_inc), _d(d_exp), _d(d_bal)]
        d_w = max(len(v) for v in deltas + ["Δ"])
        L = 7  # label column width (Расходы = 7 chars)

        hdr = f"{'':>{L}}  {prev_sn:>{col_w}}  {curr_sn:>{col_w}}  {'Δ':>{d_w}}"
        r_inc = (
            f"{'Доходы':<{L}}  {_n(prev_income):>{col_w}}  "
            f"{_n(curr_income):>{col_w}}  {_d(d_inc):>{d_w}} {m_inc}"
        )
        r_exp = (
            f"{'Расходы':<{L}}  {_n(prev_expense):>{col_w}}  "
            f"{_n(curr_expense):>{col_w}}  {_d(d_exp):>{d_w}} {m_exp}"
        )
        r_bal = (
            f"{'Остаток':<{L}}  {_n(abs(prev_bal)):>{col_w}}  "
            f"{_n(abs(curr_bal)):>{col_w}}  {_d(d_bal):>{d_w}} {m_bal}"
        )
        if top_categories:
            top_lines = ["Топ расходов:"]
            for cat, amount in top_categories:
                pct = int(amount / curr_expense * 100) if curr_expense > 0 else 0
                top_lines.append(
                    f"  {html.escape(cat):<12}  {format_money(float(amount))}  ({pct}%)"
                )
            top_block = "\n".join(top_lines)
            table = f"<pre>\n{hdr}\n{r_inc}\n{r_exp}\n{r_bal}\n\n{top_block}\n</pre>"
        else:
            table = f"<pre>\n{hdr}\n{r_inc}\n{r_exp}\n{r_bal}\n</pre>"
        text = f"{header}\n\n{table}"
    else:
        bal = curr_income - curr_expense
        bal_sign = "+" if bal >= 0 else ""
        text = (
            f"{header}\n\n"
            f"Доходы:   {format_money(float(curr_income))}\n"
            f"Расходы:  {format_money(float(curr_expense))}\n"
            f"Остаток: {bal_sign}{format_money(float(bal))}"
        )
        if top_categories:
            top_lines = ["\n\nТоп расходов:"]
            for cat, amount in top_categories:
                pct = int(amount / curr_expense * 100) if curr_expense > 0 else 0
                top_lines.append(
                    f"  {html.escape(cat)} — {format_money(float(amount))} ({pct}%)"
                )
            text += "\n".join(top_lines)

    return text


def format_daily_summary(data: dict) -> str:
    """Format daily summary. Returns empty string if no records today."""
    if not data:
        return ""

    target_date: date = data["date"]
    expense_by_cat: list = data["expense_by_cat"]
    total_income: Decimal = data["total_income"]
    total_expense: Decimal = data["total_expense"]
    month_total_expense: Decimal = data["month_total_expense"]

    balance = total_income - total_expense
    balance_sign = "+" if balance >= 0 else "−"

    lines = [
        f"🌙 Итоги сегодня, {target_date.day} {RU_MONTHS_GEN[target_date.month]}\n"
    ]

    if expense_by_cat:
        lines.append(f"Расходы: {format_money(float(total_expense))}")
        for cat, amount in expense_by_cat:
            lines.append(f"  {html.escape(cat)}:  {format_money(float(amount))}")

    if total_income > 0:
        lines.append(f"\nДоходы: {format_money(float(total_income))}")

    lines.append(f"Баланс дня: {balance_sign}{format_money(float(abs(balance)))}")
    lines.append(
        f"За {RU_MONTHS_GEN[target_date.month]} потрачено: "
        f"{format_money(float(month_total_expense))}"
    )

    return "\n".join(lines)


# ==================== Senders ====================


async def _send_with_retry(bot: Bot, chat_id: int, text: str, **kwargs) -> None:
    """send_message with one retry on flood-control (429).

    On a second failure the exception propagates to the caller's existing
    except blocks. Side effects must stay AFTER the call so they run only on
    successful delivery.
    """
    try:
        await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await bot.send_message(chat_id, text, **kwargs)


async def send_weekly_report(bot: Bot, async_session) -> None:
    """Send weekly summary every Sunday at 20:00."""
    now = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    # Today is Sunday (weekday=6); week runs Mon–Sun
    week_start = (now - timedelta(days=6)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    async with async_session() as session:
        users = await get_notifiable_users(session)

    for user in users:
        if not user.notify_weekly:
            continue
        try:
            async with async_session() as session:
                data = await get_weekly_summary_data(
                    session, user.id, week_start, week_end
                )
            text = format_weekly_summary(data)
            if text:
                await _send_with_retry(bot, user.tg_id, text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logging.warning(f"Cannot send weekly report to {user.tg_id}: {e}")
        except Exception as e:
            logging.exception(
                f"Unexpected error sending weekly report to {user.tg_id}: {e}"
            )
        await asyncio.sleep(0.05)


async def send_monthly_report(bot: Bot, async_session) -> None:
    """Send monthly summary on the last day of the month at 20:00."""
    now = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    month, year = now.month, now.year
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1

    async with async_session() as session:
        users = await get_notifiable_users(session)

    for user in users:
        if not user.notify_monthly:
            continue
        try:
            async with async_session() as session:
                curr_data = await get_monthly_summary_data(
                    session, user.id, month, year
                )
                prev_data = await get_monthly_summary_data(
                    session, user.id, prev_month, prev_year
                )
            text = format_monthly_summary(curr_data, prev_data, month, year)
            if text:
                await _send_with_retry(bot, user.tg_id, text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logging.warning(f"Cannot send monthly report to {user.tg_id}: {e}")
        except Exception as e:
            logging.exception(
                f"Unexpected error sending monthly report to {user.tg_id}: {e}"
            )
        await asyncio.sleep(0.05)


async def send_daily_summary(bot: Bot, async_session) -> None:
    """Send daily summary every day at 21:00 (only when user has records today)."""
    now = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    today = now.date()

    async with async_session() as session:
        users = await get_notifiable_users(session)

    for user in users:
        if not user.notify_daily:
            continue
        try:
            async with async_session() as session:
                data = await get_daily_summary_data(session, user.id, today)
            text = format_daily_summary(data)
            if text:
                await _send_with_retry(bot, user.tg_id, text, parse_mode="HTML")
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logging.warning(f"Cannot send daily summary to {user.tg_id}: {e}")
        except Exception as e:
            logging.exception(
                f"Unexpected error sending daily summary to {user.tg_id}: {e}"
            )
        await asyncio.sleep(0.05)


async def send_reminders(bot: Bot, async_session) -> None:
    """Send reminder to users with no records for 2+ calendar days."""
    now = datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    today = now.date()
    yesterday = today - timedelta(days=1)

    async with async_session() as session:
        users = await get_notifiable_users(session)

    for user in users:
        if not user.notify_reminder:
            continue
        try:
            async with async_session() as session:
                last_record = await get_last_record_date(session, user.id)

            # Has a record today or yesterday → skip
            if last_record is not None and last_record >= yesterday:
                continue

            # Already reminded within the last 2 days → skip
            if user.last_reminded_at is not None:
                if user.last_reminded_at.date() >= yesterday:
                    continue

            await _send_with_retry(
                bot,
                user.tg_id,
                "👋 Привет! Вы не добавляли записи уже 2 дня.\n"
                "Не забудьте записать доходы и расходы 📝",
            )
            async with async_session() as session:
                await update_last_reminded(session, user.id)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logging.warning(f"Cannot send reminder to {user.tg_id}: {e}")
        except Exception as e:
            logging.exception(f"Unexpected error sending reminder to {user.tg_id}: {e}")
        await asyncio.sleep(0.05)


def _format_debt_reminder(debt: Debt, today: date) -> str:
    """Compose the reminder message body for a single debt."""
    person = html.escape(debt.person_name)
    amount = format_money(float(debt.remaining))
    due = debt.due_date
    assert due is not None  # get_debts_to_remind filters out None
    delta_days = (due - today).days

    if delta_days == 1:
        due_str = f"{due.day} {RU_MONTHS_GEN[due.month]} {due.year} (завтра)"
    elif delta_days == 0:
        due_str = f"{due.day} {RU_MONTHS_GEN[due.month]} {due.year} (сегодня)"
    else:
        # overdue
        overdue_days = -delta_days
        due_str = (
            f"Срок истёк {overdue_days} {'день' if overdue_days == 1 else 'дней'} назад"
        )

    if debt.direction == "I":
        head = f"{person} должен тебе {amount}"
    else:
        head = f"Ты должен {person} {amount}"

    return f"⏰ <b>Напоминание о долге</b>\n\n{head}\nСрок возврата: {due_str}"


async def send_debt_reminders(bot: Bot, async_session) -> None:
    """Send debt reminders at 10:00 MSK based on due_date rules."""
    today = today_msk()

    async with async_session() as session:
        pairs = await get_debts_to_remind(session, today)

    for debt, user in pairs:
        try:
            text = _format_debt_reminder(debt, today)
            await _send_with_retry(
                bot,
                user.tg_id,
                text,
                parse_mode="HTML",
                reply_markup=debt_reminder_open_keyboard(),
            )
            async with async_session() as session:
                stored = await session.get(Debt, debt.id)
                if stored:
                    stored.last_reminded_at = moscow_now()
                    await session.commit()
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logging.warning(f"Cannot send debt reminder to {user.tg_id}: {e}")
        except Exception as e:
            logging.exception(
                f"Unexpected error sending debt reminder to {user.tg_id}: {e}"
            )
        await asyncio.sleep(0.05)


# ==================== Setup ====================


def setup_scheduler(bot: Bot, async_session) -> AsyncIOScheduler:
    """Create, configure, and start the APScheduler instance."""
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        send_weekly_report,
        "cron",
        day_of_week="sun",
        hour=20,
        minute=0,
        args=[bot, async_session],
        id="weekly_report",
    )
    scheduler.add_job(
        send_monthly_report,
        "cron",
        day="last",
        hour=20,
        minute=0,
        args=[bot, async_session],
        id="monthly_report",
    )
    scheduler.add_job(
        send_daily_summary,
        "cron",
        hour=21,
        minute=0,
        args=[bot, async_session],
        id="daily_summary",
    )
    scheduler.add_job(
        send_reminders,
        "cron",
        hour=20,
        minute=0,
        args=[bot, async_session],
        id="reminders",
    )
    scheduler.add_job(
        send_debt_reminders,
        "cron",
        hour=10,
        minute=0,
        args=[bot, async_session],
        id="debt_reminders",
    )
    scheduler.start()
    return scheduler
