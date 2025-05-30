from core.database.models import async_session
from core.database.models import User, Record
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime


async def set_user(tg_id, name, phone=None):
    async with async_session() as session:
        user = await session.scalar(select(User).filter(User.tg_id == tg_id))

        if not user:
            user = User(tg_id=tg_id, name=name, phone=phone if phone else None)
            session.add(user)
            await session.commit()


async def get_records(session, tg_id: int, within: str = "all"):
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one()
    query = select(Record).where(Record.user_id == user.id)

    now = datetime.utcnow()
    if within == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)

    query = query.order_by(Record.created_at)
    result = await session.execute(query)
    return result.scalars().all()


async def add_record(session, tg_id: int, operation: str, amount: float):
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one()
    record = Record(user_id=user.id, operation=operation, amount=amount)
    session.add(record)
    await session.commit()
