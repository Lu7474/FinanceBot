"""CRUD for Account + balance ops + inter-account transfer + get-or-create."""

import logging
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import Account, Record

from ._common import BALANCE_SET_CATEGORY, MAX_ACCOUNTS_PER_USER, TRANSFER_CATEGORY


async def create_account(
    session: AsyncSession, user_id: int, name: str
) -> Optional[Account]:
    """Create new account. Returns None if limit reached or name exists."""
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
        await session.flush()
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
        await session.flush()
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
        await session.flush()
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
        await session.flush()
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

        await session.execute(
            delete(Record).where(
                Record.account_id == account_id,
                Record.category == BALANCE_SET_CATEGORY,
            )
        )
        if desired_balance != Decimal("0"):
            session.add(
                Record(
                    user_id=user_id,
                    account_id=account_id,
                    operation="+",
                    amount=desired_balance,
                    category=BALANCE_SET_CATEGORY,
                )
            )

        await session.flush()
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


async def get_or_create_account(
    session: AsyncSession,
    user_id: int,
    name: str,
) -> Account | None:
    """Return existing account by name or create a new one. None if limit reached."""
    existing = await session.scalar(
        select(Account).where(Account.user_id == user_id, Account.name == name)
    )
    if existing:
        return existing

    count = await session.scalar(
        select(func.count(Account.id)).where(Account.user_id == user_id)
    )
    if (count or 0) >= MAX_ACCOUNTS_PER_USER:
        return None

    account = Account(user_id=user_id, name=name)
    session.add(account)
    await session.flush()
    return account


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
                    category=TRANSFER_CATEGORY,
                    account_id=from_account_id,
                ),
                Record(
                    user_id=user_id,
                    operation="+",
                    amount=amount,
                    category=TRANSFER_CATEGORY,
                    account_id=to_account_id,
                ),
            ]
        )
        await session.flush()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при создании перевода для user_id {user_id}: {e}")
        return False
