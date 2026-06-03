"""User CRUD: lookup and upsert."""

import logging
from datetime import date
from typing import Optional

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import Account, Record, User, moscow_now
from core.utils import clean_text

from .categories import seed_default_categories


async def get_notifiable_users(
    session: AsyncSession, flag: str | None = None
) -> list[User]:
    """Return non-banned users who have at least one record.

    If `flag` is given (a notify_* column name), filter to users with it enabled
    so the caller doesn't fetch-then-discard in Python.
    """
    q = (
        select(User)
        .where(User.is_banned == False)  # noqa: E712
        .where(exists().where(Record.user_id == User.id))
    )
    if flag is not None:
        q = q.where(getattr(User, flag).is_(True))
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_last_record_date(session: AsyncSession, user_id: int) -> Optional[date]:
    """Return the date of the user's most recent record, or None."""
    result = await session.scalar(
        select(func.max(Record.created_at)).where(Record.user_id == user_id)
    )
    return result.date() if result else None


async def update_last_reminded(session: AsyncSession, user_id: int) -> None:
    """Set last_reminded_at to current Moscow time."""
    user = await session.get(User, user_id)
    if user:
        user.last_reminded_at = moscow_now()
        await session.commit()


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    """Find user by Telegram ID. Returns User or None."""
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def set_user(
    session: AsyncSession,
    tg_id: int,
    name: str,
    phone: Optional[str] = None,
    default_account_name: Optional[str] = None,
) -> Optional[User]:
    """Create or update user atomically.

    First creation seeds default categories and (optionally) a default account
    in the same transaction. Concurrent /start from the same tg_id is handled:
    UNIQUE violation on tg_id → re-read and return existing user.
    """
    name = clean_text(name) if name else name
    try:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is None:
            user = User(tg_id=tg_id, name=name, phone=phone if phone else None)
            session.add(user)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                return await session.scalar(select(User).where(User.tg_id == tg_id))
            await seed_default_categories(session, user.id, commit=False)
            if default_account_name:
                session.add(Account(user_id=user.id, name=default_account_name))
        else:
            user.name = name
            if phone:
                user.phone = phone
        await session.commit()
        await session.refresh(user)
        return user
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при добавлении/обновлении пользователя {tg_id}: {e}")
        return None
