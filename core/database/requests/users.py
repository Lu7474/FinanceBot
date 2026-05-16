"""User CRUD: lookup and upsert."""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import User

from .categories import seed_default_categories


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    """Find user by Telegram ID. Returns User or None."""
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def set_user(
    session: AsyncSession, tg_id: int, name: str, phone: Optional[str] = None
) -> Optional[User]:
    """Create or update user. Seeds default categories on first creation."""
    try:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        is_new = user is None
        if not user:
            user = User(tg_id=tg_id, name=name, phone=phone if phone else None)
            session.add(user)
        else:
            user.name = name
            if phone:
                user.phone = phone
        await session.commit()
        await session.refresh(user)
        if is_new:
            await seed_default_categories(session, user.id)
            await session.refresh(user)
        return user
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при добавлении/обновлении пользователя {tg_id}: {e}")
        return None
