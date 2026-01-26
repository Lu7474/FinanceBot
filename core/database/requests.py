"""
CRUD-операции с БД: работа с пользователями и записями.
"""
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import async_session, User, Record


# ==================== Пользователи ====================

# Получает пользователя по Telegram ID
async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


# Создаёт или обновляет пользователя (возвращает User или None при ошибке)
async def set_user(
    session: AsyncSession, tg_id: int, name: str, phone: Optional[str] = None
) -> Optional[User]:
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
        await session.refresh(user)  # Обновляем для получения id
        return user
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при добавлении/обновлении пользователя {tg_id}: {e}")
        return None


# ==================== Записи ====================

# Вспомогательная функция: применяет фильтр периода к запросу
def _apply_period_filter(query, within: str, date_from: Optional[datetime], date_to: Optional[datetime]):
    now = datetime.now(ZoneInfo("Europe/Moscow"))

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
        start = date_from.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo("Europe/Moscow"))
        end = date_from.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=ZoneInfo("Europe/Moscow"))
        query = query.where(Record.created_at.between(start, end))
    elif within == "range" and date_from and date_to:
        if date_from.tzinfo is None:
            date_from = date_from.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        if date_to.tzinfo is None:
            date_to = date_to.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        query = query.where(Record.created_at.between(date_from, date_to))

    return query


# Подсчёт записей с фильтром (для пагинации)
async def count_records(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> int:
    try:
        query = select(func.count(Record.id)).where(Record.user_id == user_id)
        query = _apply_period_filter(query, within, date_from, date_to)
        result = await session.execute(query)
        return result.scalar() or 0
    except Exception as e:
        logging.exception(f"Ошибка при подсчёте записей пользователя {user_id}: {e}")
        return 0


# Получает записи пользователя с фильтром по периоду и пагинацией
# within: "all", "day", "month", "year", "date", "range"
async def get_records(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Record]:
    try:
        query = select(Record).where(Record.user_id == user_id)
        query = _apply_period_filter(query, within, date_from, date_to)
        query = query.order_by(Record.created_at.desc())  # Сначала новые

        if limit is not None:
            query = query.limit(limit).offset(offset)

        result = await session.execute(query)
        return result.scalars().all()
    except Exception as e:
        logging.exception(f"Ошибка при получении записей пользователя {user_id}: {e}")
        return []


# Получает суммы доходов и расходов за период (одним запросом)
async def get_totals(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> tuple[Decimal, Decimal]:
    """Возвращает (сумма_доходов, сумма_расходов)."""
    try:
        # Сумма доходов
        query_income = select(func.coalesce(func.sum(Record.amount), 0)).where(
            Record.user_id == user_id, Record.operation == "+"
        )
        query_income = _apply_period_filter(query_income, within, date_from, date_to)

        # Сумма расходов
        query_expense = select(func.coalesce(func.sum(Record.amount), 0)).where(
            Record.user_id == user_id, Record.operation == "-"
        )
        query_expense = _apply_period_filter(query_expense, within, date_from, date_to)

        income = await session.execute(query_income)
        expense = await session.execute(query_expense)

        return Decimal(str(income.scalar() or 0)), Decimal(str(expense.scalar() or 0))
    except Exception as e:
        logging.exception(f"Ошибка при получении сумм пользователя {user_id}: {e}")
        return Decimal("0"), Decimal("0")


# Добавляет новую запись дохода/расхода (принимает user_id напрямую)
async def add_record(
    session: AsyncSession,
    user_id: int,
    operation: str,           # "+" или "-"
    amount: Decimal,
    category: str = "не указано",
) -> bool:
    try:
        record = Record(
            user_id=user_id,
            operation=operation,
            amount=amount,
            category=category,
        )
        session.add(record)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при добавлении записи для user_id {user_id}: {e}")
        return False


# Удаляет запись по ID (проверяет принадлежность пользователю)
async def delete_record(session: AsyncSession, tg_id: int, record_id: int) -> bool:
    try:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            return False

        result = await session.execute(
            delete(Record).where(Record.id == record_id, Record.user_id == user.id)
        )
        await session.commit()
        return result.rowcount > 0
    except Exception as e:
        await session.rollback()
        logging.exception(
            f"Ошибка при удалении записи {record_id} пользователя {tg_id}: {e}"
        )
        return False


# ==================== Отчёты ====================

# Формирует текстовый отчёт по доходам за период
async def get_income_report(
    session: AsyncSession,
    user_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> str:
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


# Формирует текстовый отчёт по расходам за период
async def get_expense_report(
    session: AsyncSession,
    user_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> str:
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
