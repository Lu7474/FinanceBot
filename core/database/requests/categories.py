"""User-defined categories: CRUD, defaults seeding, keyword learning, suggestion."""

import logging
import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import CategoryKeyword, Record, UserCategory
from core.utils import SYSTEM_KEYWORDS

_STOP_WORDS = frozenset(
    {
        "в",
        "на",
        "и",
        "за",
        "по",
        "из",
        "с",
        "для",
        "от",
        "до",
        "не",
        "же",
        "бы",
        "что",
        "как",
        "это",
        "та",
        "тот",
        "или",
        "при",
        "под",
    }
)
_MAX_KEYWORDS_PER_DESCRIPTION = 3
_MAX_CATEGORIES_PER_USER = 30


async def get_user_categories(
    session: AsyncSession,
    user_id: int,
    cat_type: str | None = None,
) -> list[UserCategory]:
    """Returns active user categories filtered by type, ordered by sort_order.

    cat_type=None returns all; "+" income; "-" expense; "*" both-typed only.
    Filter logic: cat_type matches OR stored cat_type=="*" (except when cat_type=None).
    """
    query = select(UserCategory).where(
        UserCategory.user_id == user_id,
        UserCategory.is_active == True,  # noqa: E712
    )
    if cat_type is not None:
        query = query.where(
            (UserCategory.cat_type == cat_type) | (UserCategory.cat_type == "*")
        )
    query = query.order_by(UserCategory.sort_order.asc(), UserCategory.id.asc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def add_user_category(
    session: AsyncSession,
    user_id: int,
    name: str,
    cat_type: str,
) -> UserCategory | None:
    """Creates a new category. sort_order = MAX(sort_order) + 1.

    Returns None if limit (30) exceeded or name already exists.
    """
    try:
        count = await session.scalar(
            select(func.count(UserCategory.id)).where(UserCategory.user_id == user_id)
        )
        if (count or 0) >= _MAX_CATEGORIES_PER_USER:
            return None

        existing = await session.scalar(
            select(UserCategory).where(
                UserCategory.user_id == user_id,
                UserCategory.name == name,
            )
        )
        if existing:
            return None

        max_order = await session.scalar(
            select(func.max(UserCategory.sort_order)).where(
                UserCategory.user_id == user_id
            )
        )
        cat = UserCategory(
            user_id=user_id,
            name=name,
            cat_type=cat_type,
            sort_order=(max_order or 0) + 1,
        )
        session.add(cat)
        await session.commit()
        await session.refresh(cat)
        return cat
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при создании категории для user_id {user_id}: {e}")
        return None


async def rename_user_category(
    session: AsyncSession,
    cat_id: int,
    user_id: int,
    new_name: str,
) -> bool:
    """Renames category and updates all records with old name in one transaction.

    Returns False if new_name already exists for this user.
    """
    try:
        cat = await session.scalar(
            select(UserCategory).where(
                UserCategory.id == cat_id, UserCategory.user_id == user_id
            )
        )
        if not cat:
            return False

        dup = await session.scalar(
            select(UserCategory).where(
                UserCategory.user_id == user_id,
                UserCategory.name == new_name,
                UserCategory.id != cat_id,
            )
        )
        if dup:
            return False

        old_name = cat.name
        await session.execute(
            update(Record)
            .where(Record.user_id == user_id, Record.category == old_name)
            .values(category=new_name)
        )
        cat.name = new_name
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при переименовании категории {cat_id}: {e}")
        return False


async def delete_user_category(
    session: AsyncSession,
    cat_id: int,
    user_id: int,
) -> bool:
    """Deletes category. Records are NOT modified. CategoryKeyword deleted explicitly."""
    try:
        cat = await session.scalar(
            select(UserCategory).where(
                UserCategory.id == cat_id, UserCategory.user_id == user_id
            )
        )
        if not cat:
            return False

        await session.execute(
            delete(CategoryKeyword).where(CategoryKeyword.category_id == cat_id)
        )
        await session.delete(cat)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при удалении категории {cat_id}: {e}")
        return False


async def count_records_with_category(
    session: AsyncSession,
    user_id: int,
    category_name: str,
) -> int:
    """Returns count of records using this category name."""
    try:
        return (
            await session.scalar(
                select(func.count(Record.id)).where(
                    Record.user_id == user_id, Record.category == category_name
                )
            )
            or 0
        )
    except Exception as e:
        logging.exception(
            f"Ошибка при подсчёте записей категории '{category_name}': {e}"
        )
        return 0


async def seed_default_categories(
    session: AsyncSession,
    user_id: int,
) -> None:
    """Creates default categories for a new user. No-op if user already has categories."""
    try:
        count = await session.scalar(
            select(func.count(UserCategory.id)).where(UserCategory.user_id == user_id)
        )
        if (count or 0) > 0:
            return

        defaults = [
            ("Еда", "-"),
            ("Транспорт", "-"),
            ("Кафе", "-"),
            ("Развлечения", "-"),
            ("Здоровье", "-"),
            ("Связь", "-"),
            ("Зарплата", "+"),
            ("Фриланс", "+"),
        ]
        for i, (name, cat_type) in enumerate(defaults, 1):
            session.add(
                UserCategory(
                    user_id=user_id, name=name, cat_type=cat_type, sort_order=i
                )
            )
        await session.commit()
    except Exception as e:
        await session.rollback()
        logging.exception(
            f"Ошибка при создании дефолтных категорий user_id {user_id}: {e}"
        )


async def suggest_category(
    session: AsyncSession,
    user_id: int,
    text: str,
    op_type: str | None = None,
) -> str | None:
    """Suggests a category based on keywords in text.

    Checks user CategoryKeyword rules first, then SYSTEM_KEYWORDS.
    op_type: "+" or "-" — filters to categories matching that type or "*".
    Returns category name if found and active for this user, else None.
    """
    words = [
        w.lower() for w in re.split(r"\W+", text) if len(w) >= 3 and not w.isdigit()
    ]

    type_filter = [UserCategory.cat_type.in_([op_type, "*"])] if op_type else []

    for word in words:
        cat_name = await session.scalar(
            select(UserCategory.name)
            .join(CategoryKeyword, CategoryKeyword.category_id == UserCategory.id)
            .where(
                CategoryKeyword.user_id == user_id,
                CategoryKeyword.keyword == word,
                UserCategory.is_active == True,  # noqa: E712
                UserCategory.user_id == user_id,
                *type_filter,
            )
        )
        if cat_name:
            return cat_name

        system_cat = SYSTEM_KEYWORDS.get(word)
        if system_cat:
            exists = await session.scalar(
                select(UserCategory.name).where(
                    UserCategory.user_id == user_id,
                    UserCategory.name == system_cat,
                    UserCategory.is_active == True,  # noqa: E712
                    *type_filter,
                )
            )
            if exists:
                return exists

    return None


async def learn_keyword(
    session: AsyncSession,
    user_id: int,
    description: str,
    category_id: int,
) -> None:
    """Saves useful tokens from description as keyword→category_id mappings (UPSERT).

    Skips words shorter than 3 chars, numeric tokens, and stop-words.
    """
    try:
        words = [
            w.lower()
            for w in re.split(r"\W+", description)
            if len(w) >= 3 and not w.isdigit() and w.lower() not in _STOP_WORDS
        ]
        words = words[:_MAX_KEYWORDS_PER_DESCRIPTION]

        for word in words:
            existing = await session.scalar(
                select(CategoryKeyword).where(
                    CategoryKeyword.user_id == user_id,
                    CategoryKeyword.keyword == word,
                )
            )
            if existing:
                existing.category_id = category_id
            else:
                session.add(
                    CategoryKeyword(
                        user_id=user_id, category_id=category_id, keyword=word
                    )
                )
        await session.commit()
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка в learn_keyword для user_id {user_id}: {e}")
