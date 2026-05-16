"""Shared constants and helpers used across the requests package."""

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from config import TIMEZONE
from core.database.models import Record

MAX_ACCOUNTS_PER_USER = 10
TRANSFER_CATEGORY = "Перевод"
BALANCE_SET_CATEGORY = "Установка баланса"
SYSTEM_CATEGORIES = (TRANSFER_CATEGORY, BALANCE_SET_CATEGORY)

VALID_OPERATIONS = ("+", "-")


def apply_period_filter(
    query,
    within: str,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    now: Optional[datetime] = None,
):
    """Apply a period-based WHERE clause to a Record query."""
    if now is None:
        now = datetime.now(ZoneInfo(TIMEZONE))

    if within == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "yesterday":
        yesterday = now - timedelta(days=1)
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        query = query.where(Record.created_at.between(start, end))
    elif within == "week":
        start = (now - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        query = query.where(Record.created_at >= start)
    elif within == "month30":
        start = (now - timedelta(days=30)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        query = query.where(Record.created_at >= start)
    elif within == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "prev_month":
        first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_prev_month = first_this_month - timedelta(days=1)
        start = last_prev_month.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end = last_prev_month.replace(hour=23, minute=59, second=59, microsecond=999999)
        query = query.where(Record.created_at.between(start, end))
    elif within == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "date" and date_from:
        start = date_from.replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo(TIMEZONE)
        )
        end = date_from.replace(
            hour=23, minute=59, second=59, microsecond=999999, tzinfo=ZoneInfo(TIMEZONE)
        )
        query = query.where(Record.created_at.between(start, end))
    elif within == "range" and date_from and date_to:
        if date_from.tzinfo is None:
            date_from = date_from.replace(tzinfo=ZoneInfo(TIMEZONE))
        if date_to.tzinfo is None:
            date_to = date_to.replace(tzinfo=ZoneInfo(TIMEZONE))
        query = query.where(Record.created_at.between(date_from, date_to))

    return query
