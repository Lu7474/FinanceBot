"""User CRUD: lookup and upsert."""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import Account, User

from .categories import seed_default_categories


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
    try:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if user is None:
            user = User(tg_id=tg_id, name=name, phone=phone if phone else None)
            session.add(user)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                return await session.scalar(
                    select(User).where(User.tg_id == tg_id)
                )
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
        logging.exception(
            f"Ошибка при добавлении/обновлении пользователя {tg_id}: {e}"
        )
        return None
