"""
CRUD-операции с БД: работа с пользователями, записями и счетами.
"""

import csv
import io
import logging
import re
from datetime import date as date_type
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import TIMEZONE
from core.database.models import (
    Account,
    CategoryKeyword,
    Record,
    SavingsItem,
    SavingsSnapshot,
    User,
    UserCategory,
    WealthItem,
)

MAX_ACCOUNTS_PER_USER = 10
TRANSFER_CATEGORY = "Перевод"
BALANCE_SET_CATEGORY = "Установка баланса"
SYSTEM_CATEGORIES = (TRANSFER_CATEGORY, BALANCE_SET_CATEGORY)


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
        now = datetime.now(ZoneInfo(TIMEZONE))

    if within == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "yesterday":
        yesterday = now - timedelta(days=1)
        start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        query = query.where(Record.created_at.between(start, end))
    elif within == "week":
        start = (now - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        query = query.where(Record.created_at >= start)
    elif within == "month30":
        start = (now - timedelta(days=30)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        query = query.where(Record.created_at >= start)
    elif within == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "prev_month":
        # Первый день прошлого месяца
        first_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_prev_month = first_this_month - timedelta(days=1)
        start = last_prev_month.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        end = last_prev_month.replace(hour=23, minute=59, second=59, microsecond=999999)
        query = query.where(Record.created_at.between(start, end))
    elif within == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        query = query.where(Record.created_at >= start)
    elif within == "date" and date_from:
        start = date_from.replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=ZoneInfo(TIMEZONE)
        )
        end = date_from.replace(
            hour=23, minute=59, second=59, microsecond=999999, tzinfo=ZoneInfo(TIMEZONE)
        )
        query = query.where(Record.created_at.between(start, end))
    elif within == "range" and date_from and date_to:
        if date_from.tzinfo is None:
            date_from = date_from.replace(tzinfo=ZoneInfo(TIMEZONE))
        if date_to.tzinfo is None:
            date_to = date_to.replace(tzinfo=ZoneInfo(TIMEZONE))
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
        now = datetime.now(ZoneInfo(TIMEZONE))
        query = select(func.count(Record.id)).where(
            Record.user_id == user_id,
            Record.category.not_in(SYSTEM_CATEGORIES),
        )
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
    account_id: Optional[int] = None,
    include_transfers: bool = False,
) -> List[Record]:
    """Получает записи пользователя с фильтром по периоду и пагинацией."""
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        conditions = [Record.user_id == user_id]
        if not include_transfers:
            conditions.append(Record.category.not_in(SYSTEM_CATEGORIES))
        if account_id is not None:
            conditions.append(Record.account_id == account_id)
        query = select(Record).where(*conditions)
        query = _apply_period_filter(query, within, date_from, date_to, now=now)
        query = query.order_by(Record.created_at.asc())

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
        now = datetime.now(ZoneInfo(TIMEZONE))
        # Один запрос с условной агрегацией
        query = select(
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(Record.user_id == user_id, Record.category.not_in(SYSTEM_CATEGORIES))

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
    created_at: Optional[datetime] = None,
    account_id: Optional[int] = None,
) -> bool:
    """Добавляет новую запись дохода или расхода.

    Args:
        session: Асинхронная сессия БД
        user_id: ID пользователя (внутренний, не tg_id)
        operation: "+" для дохода, "-" для расхода
        amount: Сумма операции
        category: Категория (по умолчанию "не указано")
        created_at: Дата записи (опционально, по умолчанию текущая)
        account_id: ID счёта (опционально)

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
            account_id=account_id,
        )
        if created_at is not None:
            record.created_at = created_at
        session.add(record)
        return True
    except Exception as e:
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
            .where(
                Record.user_id == user_id,
                Record.operation == operation,
                Record.category.not_in(SYSTEM_CATEGORIES),
            )
            .group_by(Record.category)
        )

        if date_from and date_to:
            query = query.where(Record.created_at.between(date_from, date_to))

        result = await session.execute(query)
        rows = result.fetchall()

        return {
            (row.category or "Без категории"): Decimal(str(row.total)) for row in rows
        }
    except Exception as e:
        logging.exception(
            f"Ошибка при получении сумм по категориям для user_id {user_id}: {e}"
        )
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
    account_id: Optional[int] = None,
    include_transfers: bool = False,
) -> tuple[int, Decimal, Decimal, List[Record]]:
    """Получает все данные для истории одним вызовом.

    Returns:
        (total_count, income_sum, expense_sum, records)
    """
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))

        base_conditions = [Record.user_id == user_id]
        if not include_transfers:
            base_conditions.append(Record.category.not_in(SYSTEM_CATEGORIES))
        if account_id is not None:
            base_conditions.append(Record.account_id == account_id)

        # 1. COUNT + SUM одним запросом
        count_totals_query = select(
            func.count(Record.id).label("cnt"),
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(*base_conditions)
        count_totals_query = _apply_period_filter(
            count_totals_query, within, date_from, date_to, now=now
        )

        result = await session.execute(count_totals_query)
        row = result.one()
        total_count = row.cnt
        income_sum = Decimal(str(row.income))
        expense_sum = Decimal(str(row.expense))

        # 2. Записи с пагинацией
        records_query = select(Record).where(*base_conditions)
        records_query = _apply_period_filter(
            records_query, within, date_from, date_to, now=now
        )
        records_query = records_query.order_by(Record.created_at.asc())

        if limit is not None:
            records_query = records_query.limit(limit).offset(offset)

        records_result = await session.execute(records_query)
        records = records_result.scalars().all()

        return total_count, income_sum, expense_sum, records
    except Exception as e:
        logging.exception(
            f"Ошибка при получении данных истории для user_id {user_id}: {e}"
        )
        return 0, Decimal("0"), Decimal("0"), []


async def get_monthly_totals(
    session: AsyncSession,
    user_id: int,
    operation: str,
    months_back: int = 12,
) -> list[tuple[int, int, Decimal]]:
    """Получает суммы по месяцам за последние N месяцев (для графика тренда).

    Args:
        session: Асинхронная сессия БД
        user_id: ID пользователя (внутренний, не tg_id)
        operation: "+" для доходов, "-" для расходов
        months_back: Сколько месяцев назад смотреть (по умолчанию 12)

    Returns:
        Список кортежей [(год, месяц, сумма), ...] отсортированный по дате
    """
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        start_date = now - timedelta(days=months_back * 30)

        query = (
            select(
                func.extract("year", Record.created_at).label("year"),
                func.extract("month", Record.created_at).label("month"),
                func.sum(Record.amount).label("total"),
            )
            .where(
                Record.user_id == user_id,
                Record.operation == operation,
                Record.category.not_in(SYSTEM_CATEGORIES),
                Record.created_at >= start_date,
            )
            .group_by(
                func.extract("year", Record.created_at),
                func.extract("month", Record.created_at),
            )
            .order_by(
                func.extract("year", Record.created_at),
                func.extract("month", Record.created_at),
            )
        )

        result = await session.execute(query)
        rows = result.fetchall()

        return [
            (int(row.year), int(row.month), Decimal(str(row.total))) for row in rows
        ]
    except Exception as e:
        logging.exception(
            f"Ошибка при получении месячных сумм для user_id {user_id}: {e}"
        )
        return []


# ==================== Счета ====================


async def create_account(
    session: AsyncSession, user_id: int, name: str
) -> Optional[Account]:
    """Creates a new account for the user.

    Returns None if limit reached or name already exists.
    """
    try:
        count = await session.scalar(
            select(func.count(Account.id)).where(Account.user_id == user_id)
        )
        if (count or 0) >= MAX_ACCOUNTS_PER_USER:
            return None

        existing = await session.scalar(
            select(Account).where(Account.user_id == user_id, Account.name == name)
        )
        if existing:
            return None

        account = Account(user_id=user_id, name=name)
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return account
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при создании счёта для user_id {user_id}: {e}")
        return None


async def get_accounts(session: AsyncSession, user_id: int) -> List[Account]:
    """Returns all accounts for the user ordered by creation date."""
    try:
        result = await session.execute(
            select(Account)
            .where(Account.user_id == user_id)
            .order_by(Account.created_at)
        )
        return list(result.scalars().all())
    except Exception as e:
        logging.exception(f"Ошибка при получении счетов для user_id {user_id}: {e}")
        return []


async def rename_account(
    session: AsyncSession, account_id: int, user_id: int, new_name: str
) -> bool:
    """Renames account. Returns False if not found or name already taken."""
    try:
        account = await session.scalar(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        if not account:
            return False

        duplicate = await session.scalar(
            select(Account).where(
                Account.user_id == user_id,
                Account.name == new_name,
                Account.id != account_id,
            )
        )
        if duplicate:
            return False

        account.name = new_name
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при переименовании счёта {account_id}: {e}")
        return False


async def delete_account(session: AsyncSession, account_id: int, user_id: int) -> bool:
    """Sets account_id=NULL in linked records, then deletes the account."""
    try:
        account = await session.scalar(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        if not account:
            return False

        await session.execute(
            update(Record)
            .where(Record.account_id == account_id)
            .values(account_id=None)
        )
        await session.delete(account)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при удалении счёта {account_id}: {e}")
        return False


async def move_and_delete_account(
    session: AsyncSession, account_id: int, user_id: int, target_account_id: int
) -> bool:
    """Moves all records to target account, then deletes the source account."""
    try:
        account = await session.scalar(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        if not account:
            return False
        await session.execute(
            update(Record)
            .where(Record.account_id == account_id)
            .values(account_id=target_account_id)
        )
        await session.delete(account)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при переносе и удалении счёта {account_id}: {e}")
        return False


async def get_account_balances(session: AsyncSession, user_id: int) -> List[tuple]:
    """Returns [(Account, balance), ...] with a single aggregation query."""
    try:
        accounts = await get_accounts(session, user_id)
        if not accounts:
            return []

        account_ids = [a.id for a in accounts]
        result = await session.execute(
            select(
                Record.account_id,
                func.coalesce(
                    func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
                ).label("income"),
                func.coalesce(
                    func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
                ).label("expense"),
            )
            .where(
                Record.account_id.in_(account_ids),
                Record.category != BALANCE_SET_CATEGORY,
            )
            .group_by(Record.account_id)
        )
        balance_map: dict[int, Decimal] = {
            row.account_id: Decimal(str(row.income)) - Decimal(str(row.expense))
            for row in result.fetchall()
        }
        return [
            (
                acc,
                balance_map.get(acc.id, Decimal("0"))
                + Decimal(str(acc.balance_offset)),
            )
            for acc in accounts
        ]
    except Exception as e:
        logging.exception(f"Ошибка при получении балансов для user_id {user_id}: {e}")
        return []


async def get_account_balance(
    session: AsyncSession, account_id: int, user_id: int | None = None
) -> Decimal:
    """Returns balance for a single account (transactions + offset)."""
    conditions = [Account.id == account_id]
    if user_id is not None:
        conditions.append(Account.user_id == user_id)
    acc_result = await session.execute(select(Account).where(*conditions))
    acc = acc_result.scalar_one_or_none()
    if acc is None:
        return Decimal("0")
    result = await session.execute(
        select(
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense"),
        ).where(
            Record.account_id == account_id, Record.category != BALANCE_SET_CATEGORY
        )
    )
    row = result.one()
    tx_balance = Decimal(str(row.income)) - Decimal(str(row.expense))
    return tx_balance + Decimal(str(acc.balance_offset))


async def set_account_balance(
    session: AsyncSession, account_id: int, desired_balance: Decimal, user_id: int
) -> bool:
    """Sets account balance via balance_offset and creates a history record."""
    try:
        acc_result = await session.execute(
            select(Account).where(Account.id == account_id, Account.user_id == user_id)
        )
        acc = acc_result.scalar_one_or_none()
        if acc is None:
            return False
        tx_result = await session.execute(
            select(
                func.coalesce(
                    func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
                ).label("income"),
                func.coalesce(
                    func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
                ).label("expense"),
            ).where(
                Record.account_id == account_id, Record.category != BALANCE_SET_CATEGORY
            )
        )
        row = tx_result.one()
        tx_balance = Decimal(str(row.income)) - Decimal(str(row.expense))
        acc.balance_offset = desired_balance - tx_balance

        # Replace old balance-set record with new one
        await session.execute(
            delete(Record).where(
                Record.account_id == account_id,
                Record.category == BALANCE_SET_CATEGORY,
            )
        )
        session.add(
            Record(
                user_id=user_id,
                account_id=account_id,
                operation="+",
                amount=desired_balance,
                category=BALANCE_SET_CATEGORY,
            )
        )

        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(
            f"Ошибка при установке баланса для account_id {account_id}: {e}"
        )
        return False


async def get_account_record_count(session: AsyncSession, account_id: int) -> int:
    """Returns the number of records linked to the given account."""
    try:
        return (
            await session.scalar(
                select(func.count(Record.id)).where(Record.account_id == account_id)
            )
            or 0
        )
    except Exception as e:
        logging.exception(f"Ошибка при подсчёте записей счёта {account_id}: {e}")
        return 0


# ==================== Администрирование ====================


async def get_all_users(
    session: AsyncSession,
    offset: int = 0,
    limit: int = 10,
    filter_mode: str = "all",
    sort_by: str = "date",
) -> List[User]:
    last_act = (
        select(Record.user_id, func.max(Record.created_at).label("last_at"))
        .group_by(Record.user_id)
        .subquery()
    )
    query = select(User).outerjoin(last_act, User.id == last_act.c.user_id)
    if filter_mode == "active":
        query = query.where(User.is_banned == False)  # noqa: E712
    elif filter_mode == "banned":
        query = query.where(User.is_banned == True)  # noqa: E712
    if sort_by == "activity":
        query = query.order_by(desc(last_act.c.last_at).nulls_last())
    elif sort_by == "name":
        query = query.order_by(User.name.asc().nulls_last())
    else:
        query = query.order_by(User.created_at.desc())
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


async def count_users(session: AsyncSession, filter_mode: str = "all") -> int:
    query = select(func.count(User.id))
    if filter_mode == "active":
        query = query.where(User.is_banned == False)  # noqa: E712
    elif filter_mode == "banned":
        query = query.where(User.is_banned == True)  # noqa: E712
    return await session.scalar(query) or 0


async def get_user_stats(session: AsyncSession, user_id: int) -> dict:
    """Returns record counts, totals and last activity for a user in one query."""
    result = await session.execute(
        select(
            func.count(Record.id).label("total"),
            func.coalesce(
                func.sum(case((Record.operation == "+", 1), else_=0)), 0
            ).label("income_count"),
            func.coalesce(
                func.sum(case((Record.operation == "+", Record.amount), else_=0)), 0
            ).label("income_sum"),
            func.coalesce(
                func.sum(case((Record.operation == "-", Record.amount), else_=0)), 0
            ).label("expense_sum"),
            func.max(Record.created_at).label("last_activity"),
        ).where(
            Record.user_id == user_id,
            Record.category.not_in(SYSTEM_CATEGORIES),
        )
    )
    row = result.one()
    total = row.total or 0
    income_count = row.income_count or 0
    return {
        "total_records": total,
        "income_count": income_count,
        "expense_count": total - income_count,
        "income_sum": Decimal(str(row.income_sum)),
        "expense_sum": Decimal(str(row.expense_sum)),
        "last_activity": row.last_activity,
    }


async def ban_user(session: AsyncSession, tg_id: int, is_banned: bool) -> bool:
    try:
        result = await session.execute(
            update(User).where(User.tg_id == tg_id).values(is_banned=is_banned)
        )
        await session.commit()
        return result.rowcount > 0
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при изменении бана пользователя {tg_id}: {e}")
        return False


async def delete_user_cascade(session: AsyncSession, tg_id: int) -> bool:
    try:
        user = await session.scalar(select(User).where(User.tg_id == tg_id))
        if not user:
            return False
        await session.execute(delete(Record).where(Record.user_id == user.id))
        await session.execute(delete(Account).where(Account.user_id == user.id))
        await session.execute(delete(CategoryKeyword).where(CategoryKeyword.user_id == user.id))
        await session.execute(delete(UserCategory).where(UserCategory.user_id == user.id))
        await session.delete(user)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при удалении пользователя {tg_id}: {e}")
        return False


async def get_bot_stats(session: AsyncSession) -> dict:
    now = datetime.now(ZoneInfo(TIMEZONE))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = (now - timedelta(days=7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    total_users = await session.scalar(select(func.count(User.id))) or 0
    banned_users = (
        await session.scalar(
            select(func.count(User.id)).where(User.is_banned == True)  # noqa: E712
        )
        or 0
    )
    total_accounts = await session.scalar(select(func.count(Account.id))) or 0
    total_records = (
        await session.scalar(
            select(func.count(Record.id)).where(
                Record.category.not_in(SYSTEM_CATEGORIES)
            )
        )
        or 0
    )
    new_today = (
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
        or 0
    )
    new_week = (
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= week_start)
        )
        or 0
    )
    active_week = (
        await session.scalar(
            select(func.count(func.distinct(Record.user_id))).where(
                Record.created_at >= week_start
            )
        )
        or 0
    )

    return {
        "total_users": total_users,
        "banned_users": banned_users,
        "total_accounts": total_accounts,
        "total_records": total_records,
        "new_today": new_today,
        "new_week": new_week,
        "active_week": active_week,
    }


async def get_all_tg_ids(session: AsyncSession, skip_banned: bool = True) -> List[int]:
    query = select(User.tg_id)
    if skip_banned:
        query = query.where(User.is_banned == False)  # noqa: E712
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_top_users(session: AsyncSession, limit: int = 5) -> list:
    """Returns [(User, record_count), ...] sorted by activity."""
    result = await session.execute(
        select(User, func.count(Record.id).label("cnt"))
        .join(Record, Record.user_id == User.id)
        .where(Record.category.not_in(SYSTEM_CATEGORIES))
        .group_by(User.id)
        .order_by(func.count(Record.id).desc())
        .limit(limit)
    )
    return [(row.User, row.cnt) for row in result.fetchall()]


async def find_users_by_name(session: AsyncSession, query_str: str) -> List[User]:
    result = await session.execute(
        select(User).where(User.name.ilike(f"%{query_str}%")).limit(10)
    )
    return list(result.scalars().all())


async def get_user_last_activity(
    session: AsyncSession, user_id: int
) -> Optional[datetime]:
    return await session.scalar(
        select(func.max(Record.created_at)).where(Record.user_id == user_id)
    )


async def get_active_user_tg_ids(session: AsyncSession, days: int = 7) -> List[int]:
    since = datetime.now(ZoneInfo(TIMEZONE)) - timedelta(days=days)
    result = await session.execute(
        select(User.tg_id)
        .join(Record, Record.user_id == User.id)
        .where(Record.created_at >= since, User.is_banned == False)  # noqa: E712
        .distinct()
    )
    return list(result.scalars().all())


async def get_power_user_tg_ids(
    session: AsyncSession, min_records: int = 10
) -> List[int]:
    result = await session.execute(
        select(User.tg_id)
        .join(Record, Record.user_id == User.id)
        .where(User.is_banned == False)  # noqa: E712
        .group_by(User.id)
        .having(func.count(Record.id) >= min_records)
    )
    return list(result.scalars().all())


async def get_user_records_csv(session: AsyncSession, user_id: int) -> bytes:
    result = await session.execute(
        select(Record)
        .options(selectinload(Record.account))
        .where(Record.user_id == user_id)
        .order_by(Record.created_at)
    )
    records = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Дата", "Тип", "Сумма", "Категория", "Счёт"])
    for r in records:
        writer.writerow(
            [
                r.created_at.strftime("%d.%m.%Y %H:%M"),
                "Доход" if r.operation == "+" else "Расход",
                float(r.amount),
                r.category,
                r.account.name if r.account else "—",
            ]
        )
    return output.getvalue().encode("utf-8-sig")  # BOM для корректного открытия в Excel


# ==================== Накопления ====================


async def get_snapshots_dates(session: AsyncSession, user_id: int) -> list[date_type]:
    """Returns all dates with savings snapshots for user, sorted ascending."""
    result = await session.execute(
        select(SavingsSnapshot.date)
        .where(SavingsSnapshot.user_id == user_id)
        .order_by(SavingsSnapshot.date.asc())
    )
    return list(result.scalars().all())


async def get_snapshot(
    session: AsyncSession, user_id: int, snapshot_date: date_type
) -> SavingsSnapshot | None:
    """Returns snapshot with eagerly loaded items for the given date."""
    result = await session.execute(
        select(SavingsSnapshot)
        .options(selectinload(SavingsSnapshot.items))
        .where(
            SavingsSnapshot.user_id == user_id, SavingsSnapshot.date == snapshot_date
        )
    )
    return result.scalar_one_or_none()


async def get_snapshot_by_id(
    session: AsyncSession, snapshot_id: int, user_id: int
) -> SavingsSnapshot | None:
    """Returns snapshot by id with items (validates ownership)."""
    result = await session.execute(
        select(SavingsSnapshot)
        .options(selectinload(SavingsSnapshot.items))
        .where(SavingsSnapshot.id == snapshot_id, SavingsSnapshot.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_latest_snapshot(
    session: AsyncSession, user_id: int
) -> SavingsSnapshot | None:
    """Returns the most recent snapshot with items."""
    result = await session.execute(
        select(SavingsSnapshot)
        .options(selectinload(SavingsSnapshot.items))
        .where(SavingsSnapshot.user_id == user_id)
        .order_by(SavingsSnapshot.date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def upsert_snapshot(
    session: AsyncSession,
    user_id: int,
    snapshot_date: date_type,
    items: list[tuple[str, Decimal]],
) -> SavingsSnapshot | None:
    """Creates or replaces snapshot for the given date. items = [(name, amount), ...]."""
    try:
        result = await session.execute(
            select(SavingsSnapshot).where(
                SavingsSnapshot.user_id == user_id,
                SavingsSnapshot.date == snapshot_date,
            )
        )
        snapshot = result.scalar_one_or_none()
        if snapshot:
            await session.execute(
                delete(SavingsItem).where(SavingsItem.snapshot_id == snapshot.id)
            )
        else:
            snapshot = SavingsSnapshot(user_id=user_id, date=snapshot_date)
            session.add(snapshot)
            await session.flush()

        for name, amount in items:
            session.add(SavingsItem(snapshot_id=snapshot.id, name=name, amount=amount))
        await session.commit()
        await session.refresh(snapshot)
        return snapshot
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in upsert_snapshot for user_id {user_id}: {e}")
        return None


async def add_snapshot_item(
    session: AsyncSession,
    snapshot_id: int,
    user_id: int,
    name: str,
    amount: Decimal,
) -> SavingsItem | None:
    """Adds a new item to an existing snapshot (validates ownership)."""
    try:
        snap = await session.get(SavingsSnapshot, snapshot_id)
        if not snap or snap.user_id != user_id:
            return None
        item = SavingsItem(snapshot_id=snapshot_id, name=name, amount=amount)
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in add_snapshot_item: {e}")
        return None


async def update_snapshot_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
    amount: Decimal,
) -> bool:
    """Updates amount for a savings item (validates ownership via snapshot join)."""
    try:
        result = await session.execute(
            select(SavingsItem)
            .join(SavingsSnapshot)
            .where(SavingsItem.id == item_id, SavingsSnapshot.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return False
        item.amount = amount
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in update_snapshot_item: {e}")
        return False


async def delete_snapshot_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
) -> date_type | None:
    """Deletes a savings item. Returns snapshot date on success, None on failure."""
    try:
        result = await session.execute(
            select(SavingsItem)
            .join(SavingsSnapshot)
            .where(SavingsItem.id == item_id, SavingsSnapshot.user_id == user_id)
        )
        item = result.scalar_one_or_none()
        if not item:
            return None
        snap = await session.get(SavingsSnapshot, item.snapshot_id)
        snap_date = snap.date
        await session.delete(item)
        await session.commit()
        return snap_date
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in delete_snapshot_item: {e}")
        return None


async def delete_snapshot(
    session: AsyncSession,
    snapshot_id: int,
    user_id: int,
) -> bool:
    """Deletes entire snapshot and all its items (cascade)."""
    try:
        snap = await session.get(SavingsSnapshot, snapshot_id)
        if not snap or snap.user_id != user_id:
            return False
        await session.delete(snap)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in delete_snapshot: {e}")
        return False


# ==================== Активы / Пассивы ====================


async def get_wealth_items(session: AsyncSession, user_id: int) -> list[WealthItem]:
    """Returns all wealth items for user ordered by type then name."""
    result = await session.execute(
        select(WealthItem)
        .where(WealthItem.user_id == user_id)
        .order_by(WealthItem.type.asc(), WealthItem.name.asc())
    )
    return list(result.scalars().all())


async def add_wealth_item(
    session: AsyncSession,
    user_id: int,
    type_: str,
    name: str,
    amount: Decimal,
    note: str | None = None,
) -> WealthItem | None:
    """Creates a new wealth item."""
    try:
        item = WealthItem(
            user_id=user_id, type=type_, name=name, amount=amount, note=note
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in add_wealth_item: {e}")
        return None


async def update_wealth_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
    **fields,
) -> bool:
    """Updates fields of a wealth item (validates ownership)."""
    try:
        item = await session.get(WealthItem, item_id)
        if not item or item.user_id != user_id:
            return False
        for key, value in fields.items():
            setattr(item, key, value)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in update_wealth_item: {e}")
        return False


async def delete_wealth_item(
    session: AsyncSession,
    item_id: int,
    user_id: int,
) -> bool:
    """Deletes a wealth item (validates ownership)."""
    try:
        item = await session.get(WealthItem, item_id)
        if not item or item.user_id != user_id:
            return False
        await session.delete(item)
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in delete_wealth_item: {e}")
        return False


_ALLOWED_EDIT_FIELDS = frozenset({"amount", "category", "created_at", "account_id"})


async def get_record_by_id(
    session: AsyncSession, record_id: int, user_id: int
) -> Record | None:
    """Return user's record by id or None if not found / belongs to another user."""
    return await session.scalar(
        select(Record)
        .options(selectinload(Record.account))
        .where(Record.id == record_id, Record.user_id == user_id)
    )


async def update_record(
    session: AsyncSession, record_id: int, user_id: int, **fields
) -> Record | None:
    """Update only allowed fields on a record and return the updated object.

    Validates ownership and, if account_id is being changed, checks that the
    new account also belongs to user_id. Returns None if record not found or
    validation fails.
    """
    unknown = set(fields) - _ALLOWED_EDIT_FIELDS
    if unknown:
        logging.error(f"update_record: disallowed fields {unknown}")
        return None

    try:
        record = await session.scalar(
            select(Record).where(Record.id == record_id, Record.user_id == user_id)
        )
        if not record:
            return None

        if "account_id" in fields and fields["account_id"] is not None:
            acc = await session.scalar(
                select(Account).where(
                    Account.id == fields["account_id"],
                    Account.user_id == user_id,
                )
            )
            if not acc:
                return None

        for key, value in fields.items():
            setattr(record, key, value)

        await session.commit()
        await session.refresh(record)

        # Re-load account relationship so callers can read record.account.name
        result = await session.execute(
            select(Record)
            .options(selectinload(Record.account))
            .where(Record.id == record_id)
        )
        return result.scalar_one_or_none()

    except Exception as e:
        await session.rollback()
        logging.exception(f"Error in update_record for record_id {record_id}: {e}")
        return None


# ==================== Категории пользователя ====================

_STOP_WORDS = frozenset({
    "в", "на", "и", "за", "по", "из", "с", "для", "от", "до", "не",
    "же", "бы", "что", "как", "это", "та", "тот", "или", "при", "под",
})
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
        logging.exception(f"Ошибка при подсчёте записей категории '{category_name}': {e}")
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
                UserCategory(user_id=user_id, name=name, cat_type=cat_type, sort_order=i)
            )
        await session.commit()
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при создании дефолтных категорий user_id {user_id}: {e}")


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
    from core.utils import SYSTEM_KEYWORDS

    words = [
        w.lower()
        for w in re.split(r"\W+", text)
        if len(w) >= 3 and not w.isdigit()
    ]

    type_filter = (
        [UserCategory.cat_type.in_([op_type, "*"])]
        if op_type
        else []
    )

    for word in words:
        # User-defined keyword rules (highest priority)
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

        # System keywords fallback
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


async def create_transfer(
    session: AsyncSession,
    user_id: int,
    from_account_id: int,
    to_account_id: int,
    amount: Decimal,
) -> bool:
    """Creates two records (expense + income) for a transfer between accounts."""
    try:
        session.add_all(
            [
                Record(
                    user_id=user_id,
                    operation="-",
                    amount=amount,
                    category="Перевод",
                    account_id=from_account_id,
                ),
                Record(
                    user_id=user_id,
                    operation="+",
                    amount=amount,
                    category="Перевод",
                    account_id=to_account_id,
                ),
            ]
        )
        await session.commit()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при создании перевода для user_id {user_id}: {e}")
        return False
