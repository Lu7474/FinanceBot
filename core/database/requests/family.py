"""Family budget repository: membership, invites, shared aggregates.

Per project convention these functions DO NOT commit — the calling handler does.
"""

import secrets
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import Family, FamilyMember, Record, User

from ._common import SYSTEM_CATEGORIES, apply_period_filter

# Max members per family (owner included).
MAX_FAMILY_MEMBERS = 5

# Invite-code alphabet without look-alikes (0 O 1 I L excluded).
INVITE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 8


async def _generate_invite_code(session: AsyncSession) -> str:
    """Returns a unique 8-char invite code, retrying on the rare collision."""
    while True:
        code = "".join(
            secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH)
        )
        exists = await session.scalar(
            select(Family.id).where(Family.invite_code == code)
        )
        if not exists:
            return code


async def create_family(
    session: AsyncSession, owner_user_id: int, name: str
) -> Family:
    """Creates a Family and its owner FamilyMember row. Caller commits."""
    code = await _generate_invite_code(session)
    family = Family(name=name, owner_id=owner_user_id, invite_code=code)
    session.add(family)
    await session.flush()  # need family.id for the member row
    session.add(
        FamilyMember(family_id=family.id, user_id=owner_user_id, role="owner")
    )
    await session.flush()
    return family


async def join_family(
    session: AsyncSession, user_id: int, invite_code: str
) -> Optional[Family]:
    """Joins a family by code. Returns None if code invalid / full / already in a family."""
    family = await session.scalar(
        select(Family).where(Family.invite_code == invite_code)
    )
    if family is None:
        return None

    # One family per user
    if await get_family(session, user_id) is not None:
        return None

    member_count = (
        await session.scalar(
            select(func.count(FamilyMember.id)).where(
                FamilyMember.family_id == family.id
            )
        )
        or 0
    )
    if member_count >= MAX_FAMILY_MEMBERS:
        return None

    session.add(FamilyMember(family_id=family.id, user_id=user_id, role="member"))
    await session.flush()
    return family


async def leave_family(session: AsyncSession, user_id: int) -> bool:
    """Removes a member from their family. Owner cannot leave (only dissolve)."""
    member = await session.scalar(
        select(FamilyMember).where(FamilyMember.user_id == user_id)
    )
    if member is None or member.role == "owner":
        return False
    await session.delete(member)
    await session.flush()
    return True


async def dissolve_family(
    session: AsyncSession, family_id: int, owner_id: int
) -> bool:
    """Deletes a family (cascade removes all members). Owner-only."""
    family = await session.scalar(select(Family).where(Family.id == family_id))
    if family is None or family.owner_id != owner_id:
        return False
    await session.delete(family)
    await session.flush()
    return True


async def get_family(session: AsyncSession, user_id: int) -> Optional[Family]:
    """Returns the family the user belongs to, or None."""
    return await session.scalar(
        select(Family)
        .join(FamilyMember, FamilyMember.family_id == Family.id)
        .where(FamilyMember.user_id == user_id)
    )


async def get_family_members(session: AsyncSession, family_id: int) -> list[User]:
    """Returns member User rows ordered by join time (owner first → stable palette)."""
    result = await session.execute(
        select(User)
        .join(FamilyMember, FamilyMember.user_id == User.id)
        .where(FamilyMember.family_id == family_id)
        .order_by(FamilyMember.joined_at, FamilyMember.id)
    )
    return list(result.scalars().all())


async def get_family_member_ids(session: AsyncSession, family_id: int) -> list[int]:
    """Returns member user_ids ordered by join time (for report scope)."""
    result = await session.execute(
        select(FamilyMember.user_id)
        .where(FamilyMember.family_id == family_id)
        .order_by(FamilyMember.joined_at, FamilyMember.id)
    )
    return list(result.scalars().all())


async def get_family_summary(
    session: AsyncSession,
    family_id: int,
    within: str = "month",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[int, dict]:
    """Per-member income/expense totals for the period.

    Returns {user_id: {"income": Decimal, "expense": Decimal}} for every member
    (members with no records get zeros). System categories excluded.
    """
    member_ids = await get_family_member_ids(session, family_id)
    summary: dict[int, dict] = {
        uid: {"income": Decimal("0"), "expense": Decimal("0")} for uid in member_ids
    }
    if not member_ids:
        return summary

    query = (
        select(
            Record.user_id,
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        )
        .where(
            Record.user_id.in_(member_ids),
            Record.category.not_in(SYSTEM_CATEGORIES),
        )
        .group_by(Record.user_id)
    )
    query = apply_period_filter(query, within, date_from, date_to)

    result = await session.execute(query)
    for row in result.fetchall():
        summary[row.user_id] = {
            "income": Decimal(str(row.income)),
            "expense": Decimal(str(row.expense)),
        }
    return summary


async def get_family_category_breakdown(
    session: AsyncSession,
    family_id: int,
    operation: str,
    within: str = "month",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[dict]:
    """Sums grouped by (category, user_id) for the family report stacked chart.

    Returns [{"category": str, "user_id": int, "total": Decimal}, ...].
    System categories excluded.
    """
    member_ids = await get_family_member_ids(session, family_id)
    if not member_ids:
        return []

    query = (
        select(
            Record.category,
            Record.user_id,
            func.sum(Record.amount).label("total"),
        )
        .where(
            Record.user_id.in_(member_ids),
            Record.operation == operation,
            Record.category.not_in(SYSTEM_CATEGORIES),
        )
        .group_by(Record.category, Record.user_id)
    )
    query = apply_period_filter(query, within, date_from, date_to)

    result = await session.execute(query)
    return [
        {
            "category": row.category or "Без категории",
            "user_id": row.user_id,
            "total": Decimal(str(row.total)),
        }
        for row in result.fetchall()
    ]


async def kick_member(
    session: AsyncSession, family_id: int, owner_id: int, target_user_id: int
) -> bool:
    """Removes a member. Owner-only; the owner cannot kick themselves."""
    family = await session.scalar(select(Family).where(Family.id == family_id))
    if family is None or family.owner_id != owner_id:
        return False
    if target_user_id == owner_id:
        return False
    member = await session.scalar(
        select(FamilyMember).where(
            FamilyMember.family_id == family_id,
            FamilyMember.user_id == target_user_id,
        )
    )
    if member is None:
        return False
    await session.delete(member)
    await session.flush()
    return True


async def regenerate_invite_code(
    session: AsyncSession, family_id: int, owner_id: int
) -> Optional[str]:
    """Generates a new unique invite code. Owner-only. Returns the new code or None."""
    family = await session.scalar(select(Family).where(Family.id == family_id))
    if family is None or family.owner_id != owner_id:
        return None
    family.invite_code = await _generate_invite_code(session)
    await session.flush()
    return family.invite_code


async def rename_family(
    session: AsyncSession, family_id: int, owner_id: int, name: str
) -> bool:
    """Renames a family. Owner-only."""
    family = await session.scalar(select(Family).where(Family.id == family_id))
    if family is None or family.owner_id != owner_id:
        return False
    family.name = name
    await session.flush()
    return True
