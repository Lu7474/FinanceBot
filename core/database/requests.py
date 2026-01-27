"""
CRUD-операции с БД: работа с пользователями и записями.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import async_session, User, Record


# ==================== Пользователи ====================

# Получает пользователя по Telegram ID
async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> Optional[User]:
    """Находит пользователя по Telegram ID.

    Args:
        session: Асинхронная сессия БД
        tg_id: Telegram ID пользователя

    Returns:
        User или None если не найден
    """
    return await session.scalar(select(User).where(User.tg_id == tg_id))


# Создаёт или обновляет пользователя (возвращает User или None при ошибке)
async def set_user(
    session: AsyncSession, tg_id: int, name: str, phone: Optional[str] = None
) -> Optional[User]:
    """Создаёт нового или обновляет существующего пользователя.

    Args:
        session: Асинхронная сессия БД
        tg_id: Telegram ID пользователя
        name: Имя пользователя
        phone: Номер телефона (опционально)

    Returns:
        User объект или None при ошибке
    """
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
def _apply_period_filter(
    query,
    within: str,
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    now: Optional[datetime] = None,
):
    # Используем переданное время или создаём новое (для обратной совместимости)
    if now is None:
        now = datetime.now(ZoneInfo("Europe/Moscow"))

    if within == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "yesterday":
        yesterday = now - timedelta(days=1)
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        query = query.where(Record.created_at.between(start, end))
    elif within == "week":
        start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "month30":
        start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "prev_month":
        # Первый день прошлого месяца
        first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_prev_month = first_this_month - timedelta(days=1)
        start = last_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = last_prev_month.replace(hour=23, minute=59, second=59, microsecond=999999)
        query = query.where(Record.created_at.between(start, end))
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
    """Подсчитывает количество записей пользователя с фильтром по периоду.

    Args:
        session: Асинхронная сессия БД
        user_id: ID пользователя (внутренний, не tg_id)
        within: Период ("all", "day", "month", "year", "date", "range")
        date_from: Начальная дата (для "date" и "range")
        date_to: Конечная дата (для "range")

    Returns:
        Количество записей
    """
    try:
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        query = select(func.count(Record.id)).where(Record.user_id == user_id)
        query = _apply_period_filter(query, within, date_from, date_to, now=now)
        result = await session.execute(query)
        return result.scalar() or 0
    except Exception as e:
        logging.exception(f"Ошибка при подсчёте записей пользователя {user_id}: {e}")
        return 0


# Получает записи пользователя с фильтром по периоду и пагинацией
async def get_records(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Record]:
    """Получает записи пользователя с фильтром по периоду и пагинацией.

    Args:
        session: Асинхронная сессия БД
        user_id: ID пользователя (внутренний, не tg_id)
        within: Период ("all", "day", "month", "year", "date", "range")
        date_from: Начальная дата (для "date" и "range")
        date_to: Конечная дата (для "range")
        limit: Максимальное количество записей (для пагинации)
        offset: Смещение (для пагинации)

    Returns:
        Список записей Record, отсортированных по дате (новые первые)
    """
    try:
        now = datetime.now(ZoneInfo("Europe/Moscow"))
        query = select(Record).where(Record.user_id == user_id)
        query = _apply_period_filter(query, within, date_from, date_to, now=now)
        query = query.order_by(Record.created_at.asc())  # Хронологический порядок

        if limit is not None:
            query = query.limit(limit).offset(offset)

        result = await session.execute(query)
        return result.scalars().all()
    except Exception as e:
        logging.exception(f"Ошибка при получении записей пользователя {user_id}: {e}")
        return []


# Получает суммы доходов и расходов за период (одним запросом с CASE WHEN)
async def get_totals(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> tuple[Decimal, Decimal]:
    """Возвращает (сумма_доходов, сумма_расходов) одним запросом."""
    try:
        from sqlalchemy import case

        now = datetime.now(ZoneInfo("Europe/Moscow"))
        # Один запрос с условной агрегацией
        query = select(
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(Record.user_id == user_id)

        query = _apply_period_filter(query, within, date_from, date_to, now=now)
        result = await session.execute(query)
        row = result.one()

        return Decimal(str(row.income)), Decimal(str(row.expense))
    except Exception as e:
        logging.exception(f"Ошибка при получении сумм пользователя {user_id}: {e}")
        return Decimal("0"), Decimal("0")


# Допустимые значения для операции
VALID_OPERATIONS = ("+", "-")


# Добавляет новую запись дохода/расхода (принимает user_id напрямую)
async def add_record(
    session: AsyncSession,
    user_id: int,
    operation: str,
    amount: Decimal,
    category: str = "не указано",
) -> bool:
    """Добавляет новую запись дохода или расхода.

    Args:
        session: Асинхронная сессия БД
        user_id: ID пользователя (внутренний, не tg_id)
        operation: "+" для дохода, "-" для расхода
        amount: Сумма операции
        category: Категория (по умолчанию "не указано")

    Returns:
        True если запись добавлена, False при ошибке

    Raises:
        ValueError: Если operation не "+" или "-"
    """
    # Валидация операции
    if operation not in VALID_OPERATIONS:
        logging.error(f"Некорректная операция: {operation!r} (ожидается '+' или '-')")
        raise ValueError(f"operation must be '+' or '-', got {operation!r}")

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
async def delete_record(session: AsyncSession, user_id: int, record_id: int) -> bool:
    """Удаляет запись по ID, проверяя принадлежность пользователю.

    Args:
        session: Асинхронная сессия БД
        user_id: ID пользователя (внутренний, не tg_id)
        record_id: ID записи для удаления

    Returns:
        True если запись удалена, False если не найдена или ошибка
    """
    try:
        result = await session.execute(
            delete(Record).where(Record.id == record_id, Record.user_id == user_id)
        )
        await session.commit()
        return result.rowcount > 0
    except Exception as e:
        await session.rollback()
        logging.exception(
            f"Ошибка при удалении записи {record_id} пользователя {user_id}: {e}"
        )
        return False


# ==================== Оптимизированные запросы ====================

async def get_categories_summary(
    session: AsyncSession,
    user_id: int,
    operation: str,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Decimal]:
    """Получает суммы по категориям через SQL GROUP BY (оптимизированный запрос).

    Args:
        session: Асинхронная сессия БД
        user_id: ID пользователя (внутренний, не tg_id)
        operation: "+" для доходов, "-" для расходов
        date_from: Начальная дата периода
        date_to: Конечная дата периода

    Returns:
        Словарь {категория: сумма}
    """
    try:
        query = (
            select(
                Record.category,
                func.sum(Record.amount).label("total"),
            )
            .where(Record.user_id == user_id, Record.operation == operation)
            .group_by(Record.category)
        )

        if date_from and date_to:
            query = query.where(Record.created_at.between(date_from, date_to))

        result = await session.execute(query)
        rows = result.fetchall()

        return {
            (row.category or "Без категории"): Decimal(str(row.total))
            for row in rows
        }
    except Exception as e:
        logging.exception(f"Ошибка при получении сумм по категориям для user_id {user_id}: {e}")
        return {}


# Комбинированный запрос для истории (count + totals + records за один вызов)
async def get_history_data(
    session: AsyncSession,
    user_id: int,
    within: str = "all",
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> tuple[int, Decimal, Decimal, List[Record]]:
    """Получает все данные для истории одним вызовом.

    Returns:
        (total_count, income_sum, expense_sum, records)
    """
    try:
        from sqlalchemy import case

        now = datetime.now(ZoneInfo("Europe/Moscow"))

        # 1. COUNT + SUM одним запросом
        count_totals_query = select(
            func.count(Record.id).label("cnt"),
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(Record.user_id == user_id)
        count_totals_query = _apply_period_filter(count_totals_query, within, date_from, date_to, now=now)

        result = await session.execute(count_totals_query)
        row = result.one()
        total_count = row.cnt
        income_sum = Decimal(str(row.income))
        expense_sum = Decimal(str(row.expense))

        # 2. Записи с пагинацией
        records_query = select(Record).where(Record.user_id == user_id)
        records_query = _apply_period_filter(records_query, within, date_from, date_to, now=now)
        records_query = records_query.order_by(Record.created_at.asc())

        if limit is not None:
            records_query = records_query.limit(limit).offset(offset)

        records_result = await session.execute(records_query)
        records = records_result.scalars().all()

        return total_count, income_sum, expense_sum, records
    except Exception as e:
        logging.exception(f"Ошибка при получении данных истории для user_id {user_id}: {e}")
        return 0, Decimal("0"), Decimal("0"), []
