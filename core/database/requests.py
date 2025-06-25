import logging
from datetime import datetime
from sqlalchemy import select
from decimal import Decimal
from core.database.models import async_session, User, Record
from zoneinfo import ZoneInfo


async def get_user_by_tg_id(session, tg_id: int):
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def set_user(tg_id, name, phone=None):
    async with async_session() as session:
        try:
            user = await session.scalar(select(User).where(User.tg_id == tg_id))
            if not user:
                user = User(tg_id=tg_id, name=name, phone=phone if phone else None)
                session.add(user)
            else:
                user.name = name
                if phone:
                    user.phone = phone
            await session.commit()
        except Exception as e:
            await session.rollback()
            logging.exception(
                f"Ошибка при добавлении/обновлении пользователя {tg_id}: {e}"
            )


async def get_records(
    session,
    user_id: int,
    within: str = "all",
    date_from: datetime = None,
    date_to: datetime = None,
):
    try:
        query = select(Record).where(Record.user_id == user_id)

        now = datetime.now(ZoneInfo("Europe/Moscow"))  # Московское время
        if within == "day":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            query = query.where(Record.created_at >= start)
        elif within == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            query = query.where(Record.created_at >= start)
        elif within == "year":
            start = now.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
            query = query.where(Record.created_at >= start)
        elif within == "date" and date_from:
            start = date_from.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
                tzinfo=ZoneInfo("Europe/Moscow"),
            )
            end = date_from.replace(
                hour=23,
                minute=59,
                second=59,
                microsecond=999999,
                tzinfo=ZoneInfo("Europe/Moscow"),
            )
            query = query.where(Record.created_at.between(start, end))
        elif within == "range" and date_from and date_to:
            # Приводим к московскому времени, если не указано
            if date_from.tzinfo is None:
                date_from = date_from.replace(tzinfo=ZoneInfo("Europe/Moscow"))
            else:
                date_from = date_from.astimezone(ZoneInfo("Europe/Moscow"))
            if date_to.tzinfo is None:
                date_to = date_to.replace(tzinfo=ZoneInfo("Europe/Moscow"))
            else:
                date_to = date_to.astimezone(ZoneInfo("Europe/Moscow"))
            query = query.where(Record.created_at.between(date_from, date_to))

        query = query.order_by(Record.created_at)
        result = await session.execute(query)
        return result.scalars().all()
    except Exception as e:
        logging.exception(f"Ошибка при получении записей пользователя {user_id}: {e}")
        return []


async def add_record(
    session, tg_id: int, operation: str, amount: float, category: str = "не указано"
):
    try:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            return False

        record = Record(
            user_id=user.id,
            operation=operation,
            amount=Decimal(str(amount)),
            category=category,
        )
        session.add(record)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при добавлении записи для пользователя {tg_id}: {e}")
        return False


async def delete_record(session, tg_id: int, record_id: int):
    try:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            return False

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
    except Exception as e:
        await session.rollback()
        logging.exception(
            f"Ошибка при удалении записи {record_id} пользователя {tg_id}: {e}"
        )
        return False
