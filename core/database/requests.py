import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import async_session, User, Record


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def set_user(
    session: AsyncSession, tg_id: int, name: str, phone: Optional[str] = None
) -> None:
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
        logging.exception(f"Ошибка при добавлении/обновлении пользователя {tg_id}: {e}")


async def get_records(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> List[Record]:
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
    session: AsyncSession,
    tg_id: int,
    operation: str,
    amount: Decimal,
    category: str = "не указано",
) -> bool:
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


async def delete_record(session: AsyncSession, tg_id: int, record_id: int) -> bool:
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


async def get_income_report(session, user_id, date_from=None, date_to=None):
    query = select(Record).where(Record.user_id == user_id, Record.operation == "+")
    if date_from and date_to:
        query = query.where(Record.created_at.between(date_from, date_to))
    incomes = await session.execute(query)
    incomes = incomes.scalars().all()
    if not incomes:
        return "Доходов не найдено."
    report = "Ваши доходы:\n"
    for inc in incomes:
        report += f"{inc.created_at:%d.%m.%y} — {round(inc.amount, 0):,.0f}₽ {inc.category}\n".replace(
            ",", "."
        )
    return report


async def get_expense_report(session, user_id, date_from=None, date_to=None):
    query = select(Record).where(Record.user_id == user_id, Record.operation == "-")
    if date_from and date_to:
        query = query.where(Record.created_at.between(date_from, date_to))
    expenses = await session.execute(query)
    expenses = expenses.scalars().all()
    if not expenses:
        return "Расходов не найдено."
    report = "Ваши расходы:\n"
    for exp in expenses:
        report += f"{exp.created_at:%d.%m.%y} — {round(exp.amount, 0):,.0f}₽ {exp.category}\n".replace(
            ",", "."
        )
    return report
