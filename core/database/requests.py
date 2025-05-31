from datetime import datetime, timedelta

from sqlalchemy import select

from core.database.models import async_session, User, Record


async def set_user(tg_id, name, phone=None):
    async with async_session() as session:
        user = await session.scalar(select(User).filter(User.tg_id == tg_id))

        if not user:
            user = User(tg_id=tg_id, name=name, phone=phone if phone else None)
            session.add(user)
            await session.commit()


async def get_records(
    session,
    tg_id: int,
    within: str = "all",
    date_from: datetime = None,
    date_to: datetime = None,
):
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
    elif within == "date" and date_from:
        start = date_from - timedelta(hours=3)
        end = date_from.replace(
            hour=23, minute=59, second=59, microsecond=999999
        ) - timedelta(hours=3)
        query = query.where(Record.created_at.between(start, end))

    elif within == "range" and date_from and date_to:
        query = query.where(Record.created_at.between(date_from, date_to))

    query = query.order_by(Record.created_at)
    result = await session.execute(query)
    return result.scalars().all()


async def add_record(
    session, tg_id: int, operation: str, amount: float, category: str = "не указано"
):
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    user = result.scalar_one()
    record = Record(
        user_id=user.id, operation=operation, amount=amount, category=category
    )
    session.add(record)
    await session.commit()


async def delete_record(session, tg_id: int, record_id: int):
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one()
    record = (
        await session.execute(
            select(Record).where(Record.id == record_id, Record.user_id == user.id)
        )
    ).scalar_one_or_none()

    if record:
        await session.delete(record)
        await session.commit()
        return True
    return False
