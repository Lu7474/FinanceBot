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
    result = await session.execute(
        select(Account).where(Account.user_id == user_id).order_by(Account.created_at)
    )
    return list(result.scalars().all())


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
            balance_map.get(acc.id, Decimal("0")) + Decimal(str(acc.balance_offset)),
        )
        for acc in accounts
    ]


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
    return (
        await session.scalar(
            select(func.count(Record.id)).where(Record.account_id == account_id)
        )
        or 0
    )


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
    """Creates two linked records (expense + income) for a transfer.

    Both rows share a transfer_id (= expense row id) so the pair can be listed
    and cancelled atomically.
    """
    try:
        expense = Record(
            user_id=user_id,
            operation="-",
            amount=amount,
            category=TRANSFER_CATEGORY,
            account_id=from_account_id,
        )
        income = Record(
            user_id=user_id,
            operation="+",
            amount=amount,
            category=TRANSFER_CATEGORY,
            account_id=to_account_id,
        )
        session.add_all([expense, income])
        await session.flush()  # assigns PKs
        expense.transfer_id = expense.id
        income.transfer_id = expense.id
        await session.flush()
        return True
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при создании перевода для user_id {user_id}: {e}")
        return False


async def count_transfers(session: AsyncSession, user_id: int) -> int:
    """Returns the number of transfer pairs (distinct transfer_id) for the user."""
    return (
        await session.scalar(
            select(func.count(func.distinct(Record.transfer_id))).where(
                Record.user_id == user_id,
                Record.category == TRANSFER_CATEGORY,
                Record.transfer_id.isnot(None),
            )
        )
        or 0
    )


async def get_transfers(
    session: AsyncSession, user_id: int, limit: int, offset: int
) -> List[dict]:
    """Returns a page of transfer pairs, newest first.

    Each dict: {transfer_id, amount, date (datetime), from_name, to_name}.
    Account names fall back to «(удалён)» when the linked account is gone.
    """
    # Newest transfer_ids for this page (group by pair, order by recency).
    id_rows = await session.execute(
        select(Record.transfer_id, func.max(Record.created_at).label("ts"))
        .where(
            Record.user_id == user_id,
            Record.category == TRANSFER_CATEGORY,
            Record.transfer_id.isnot(None),
        )
        .group_by(Record.transfer_id)
        .order_by(func.max(Record.created_at).desc())
        .limit(limit)
        .offset(offset)
    )
    transfer_ids = [row.transfer_id for row in id_rows.fetchall()]
    if not transfer_ids:
        return []

    # All records of those pairs, joined to account names.
    rows = await session.execute(
        select(Record, Account.name)
        .outerjoin(Account, Record.account_id == Account.id)
        .where(
            Record.user_id == user_id,
            Record.transfer_id.in_(transfer_ids),
        )
    )
    pairs: dict[int, dict] = {}
    for record, acc_name in rows.fetchall():
        entry = pairs.setdefault(
            record.transfer_id,
            {
                "transfer_id": record.transfer_id,
                "amount": record.amount,
                "date": record.created_at,
                "from_name": None,
                "to_name": None,
            },
        )
        name = acc_name or "(удалён)"
        if record.operation == "-":
            entry["from_name"] = name
        else:
            entry["to_name"] = name

    # Preserve the page ordering (newest first).
    return [pairs[tid] for tid in transfer_ids if tid in pairs]


async def get_transfer(
    session: AsyncSession, user_id: int, transfer_id: int
) -> Optional[dict]:
    """Returns a single transfer pair as a dict, or None if not found/not owned."""
    rows = await session.execute(
        select(Record, Account.name)
        .outerjoin(Account, Record.account_id == Account.id)
        .where(
            Record.user_id == user_id,
            Record.transfer_id == transfer_id,
        )
    )
    result: Optional[dict] = None
    for record, acc_name in rows.fetchall():
        if result is None:
            result = {
                "transfer_id": transfer_id,
                "amount": record.amount,
                "date": record.created_at,
                "from_name": None,
                "to_name": None,
            }
        name = acc_name or "(удалён)"
        if record.operation == "-":
            result["from_name"] = name
        else:
            result["to_name"] = name
    return result


async def cancel_transfer(
    session: AsyncSession, user_id: int, transfer_id: int
) -> bool:
    """Deletes both records of a transfer atomically. False if nothing was removed.

    user_id is enforced in the WHERE clause to prevent cancelling someone
    else's transfer (IDOR). Tolerates a half-deleted pair.
    """
    try:
        result = await session.execute(
            delete(Record).where(
                Record.user_id == user_id,
                Record.transfer_id == transfer_id,
                Record.category == TRANSFER_CATEGORY,
            )
        )
        await session.flush()
        return (result.rowcount or 0) > 0
    except Exception as e:
        await session.rollback()
        logging.exception(f"Ошибка при отмене перевода {transfer_id}: {e}")
        return False
