"""Tests for export/import functionality."""

import sys
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import User
from core.database.requests import (
    bulk_insert_records,
    check_duplicate_record,
    get_or_create_account,
)
from core.export import (
    _build_template_sync,
    parse_import_file,
    validate_import_row,
)


# ==================== Helpers ====================


def _make_xlsx(rows: list[dict]) -> bytes:
    """Build a minimal xlsx bytes from a list of row dicts."""
    buf = BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


async def _create_user(session, tg_id: int = 999) -> User:
    user = User(tg_id=tg_id, name="Test")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


# ==================== validate_import_row ====================


def test_validate_import_row_date_formats():
    """Both date formats parse correctly."""
    row_dmy = {"Дата": "15.03.2024", "Тип": "Расход", "Сумма": "100", "Категория": "Еда"}
    row_iso = {"Дата": "2024-03-15", "Тип": "Расход", "Сумма": "100", "Категория": "Еда"}

    parsed_dmy, err = validate_import_row(row_dmy, 2)
    assert err is None
    assert parsed_dmy["date"] == date(2024, 3, 15)

    parsed_iso, err = validate_import_row(row_iso, 3)
    assert err is None
    assert parsed_iso["date"] == date(2024, 3, 15)


def test_validate_import_row_bad_date():
    row = {"Дата": "31/13/2024", "Тип": "Расход", "Сумма": "100", "Категория": "Еда"}
    parsed, err = validate_import_row(row, 2)
    assert parsed is None
    assert "дат" in err.lower()


def test_validate_import_row_operation_variants():
    """All accepted operation aliases map correctly."""
    for text, expected in [("Расход", "-"), ("расход", "-"), ("-", "-"),
                            ("Доход", "+"), ("доход", "+"), ("+", "+")]:
        row = {"Дата": "01.01.2025", "Тип": text, "Сумма": "50", "Категория": "Еда"}
        parsed, err = validate_import_row(row, 2)
        assert err is None, f"Unexpected error for type={text!r}: {err}"
        assert parsed["operation"] == expected


def test_validate_import_row_bad_operation():
    row = {"Дата": "01.01.2025", "Тип": "Трата", "Сумма": "100", "Категория": "Еда"}
    parsed, err = validate_import_row(row, 2)
    assert parsed is None
    assert err is not None


def test_validate_import_row_amount_bounds():
    """Amount must be >0 and <=MAX_AMOUNT."""
    ok_row = {"Дата": "01.01.2025", "Тип": "Расход", "Сумма": "100", "Категория": "Еда"}
    zero_row = {**ok_row, "Сумма": "0"}
    neg_row = {**ok_row, "Сумма": "-50"}
    big_row = {**ok_row, "Сумма": "9999999"}

    p, err = validate_import_row(ok_row, 2)
    assert err is None

    _, err = validate_import_row(zero_row, 3)
    assert err is not None

    _, err = validate_import_row(neg_row, 4)
    assert err is not None

    _, err = validate_import_row(big_row, 5)
    assert err is not None


def test_validate_import_row_category_capitalized():
    row = {"Дата": "01.01.2025", "Тип": "Расход", "Сумма": "50", "Категория": "еда на день"}
    parsed, err = validate_import_row(row, 2)
    assert err is None
    assert parsed["category"] == "Еда на день"


def test_validate_import_row_account_optional():
    row_with = {"Дата": "01.01.2025", "Тип": "Расход", "Сумма": "50", "Категория": "Еда", "Счёт": "Карта"}
    row_without = {"Дата": "01.01.2025", "Тип": "Расход", "Сумма": "50", "Категория": "Еда"}

    p1, _ = validate_import_row(row_with, 2)
    p2, _ = validate_import_row(row_without, 3)

    assert p1["account_name"] == "Карта"
    assert p2["account_name"] is None


# ==================== parse_import_file ====================


def test_parse_import_file_valid():
    """Valid xlsx is parsed without errors."""
    rows = [
        {"Дата": "01.01.2025", "Тип": "Расход", "Сумма": 100.0, "Категория": "Еда", "Счёт": "Карта"},
        {"Дата": "02.01.2025", "Тип": "Доход", "Сумма": 5000.0, "Категория": "Зарплата", "Счёт": None},
    ]
    xlsx = _make_xlsx(rows)
    valid, errors, dups = parse_import_file(xlsx)
    assert len(valid) == 2
    assert errors == []
    assert dups == 0


def test_parse_import_file_errors():
    """Rows with invalid data go to errors, valid ones to valid_rows."""
    rows = [
        {"Дата": "01.01.2025", "Тип": "Расход", "Сумма": 100.0, "Категория": "Еда"},
        {"Дата": "BAD_DATE", "Тип": "Расход", "Сумма": 100.0, "Категория": "Еда"},
        {"Дата": "03.01.2025", "Тип": "UNKNOWN", "Сумма": 100.0, "Категория": "Еда"},
        {"Дата": "04.01.2025", "Тип": "Расход", "Сумма": -10.0, "Категория": "Еда"},
    ]
    xlsx = _make_xlsx(rows)
    valid, errors, _ = parse_import_file(xlsx)
    assert len(valid) == 1
    assert len(errors) == 3


def test_parse_import_file_max_rows():
    """File with >1000 rows returns error."""
    rows = [
        {"Дата": "01.01.2025", "Тип": "Расход", "Сумма": 100.0, "Категория": "Еда"}
        for _ in range(1001)
    ]
    xlsx = _make_xlsx(rows)
    valid, errors, _ = parse_import_file(xlsx, max_rows=1000)
    assert valid == []
    assert len(errors) == 1
    assert "1000" in errors[0]


def test_parse_import_file_missing_columns():
    """Missing required columns produce error."""
    rows = [{"Дата": "01.01.2025", "Тип": "Расход"}]  # no Сумма, Категория
    xlsx = _make_xlsx(rows)
    valid, errors, _ = parse_import_file(xlsx)
    assert valid == []
    assert errors


def test_parse_import_file_empty_file():
    """Empty xlsx returns empty results without crash."""
    xlsx = _make_xlsx([{"Дата": None, "Тип": None, "Сумма": None, "Категория": None}])
    # The one row has all None — will fail validation, counted as error
    valid, errors, _ = parse_import_file(xlsx)
    assert isinstance(valid, list)
    assert isinstance(errors, list)


# ==================== _build_template_sync ====================


def test_build_template_sync():
    """Template xlsx is valid and has expected columns."""
    buf = _build_template_sync()
    df = pd.read_excel(buf, sheet_name=0)
    assert "Дата" in df.columns
    assert "Тип" in df.columns
    assert "Сумма" in df.columns
    assert "Категория" in df.columns
    assert len(df) == 1  # one example row


# ==================== DB: check_duplicate_record ====================


@pytest.mark.asyncio
async def test_check_duplicate_record(session):
    """Duplicate is detected by date (no time), operation, amount, category."""
    from zoneinfo import ZoneInfo
    from core.database.models import Record

    user = await _create_user(session, tg_id=1001)
    user_id = user.id  # capture before next commit expires the object

    created_at = datetime(2025, 1, 15, 10, 30, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    rec = Record(
        user_id=user_id,
        operation="-",
        amount=Decimal("100.00"),
        category="Еда",
        created_at=created_at,
    )
    session.add(rec)
    await session.commit()

    # Same date (different time) → duplicate
    is_dup = await check_duplicate_record(session, user_id, date(2025, 1, 15), "-", Decimal("100.00"), "Еда")
    assert is_dup is True

    # Different date → not duplicate
    not_dup = await check_duplicate_record(session, user_id, date(2025, 1, 16), "-", Decimal("100.00"), "Еда")
    assert not_dup is False

    # Different amount → not duplicate
    not_dup2 = await check_duplicate_record(session, user_id, date(2025, 1, 15), "-", Decimal("200.00"), "Еда")
    assert not_dup2 is False


# ==================== DB: bulk_insert_records ====================


@pytest.mark.asyncio
async def test_bulk_insert_records(session):
    """Records are inserted and count is correct."""
    user = await _create_user(session, tg_id=1002)
    user_id = user.id  # capture before bulk_insert commits and expires the object

    rows = [
        {"date": date(2025, 1, 1), "operation": "-", "amount": Decimal("50"), "category": "Кафе", "account_id": None},
        {"date": date(2025, 1, 2), "operation": "+", "amount": Decimal("3000"), "category": "Зарплата", "account_id": None},
    ]
    count = await bulk_insert_records(session, user_id, rows)
    assert count == 2

    from sqlalchemy import select
    from core.database.models import Record
    result = await session.execute(select(Record).where(Record.user_id == user_id))
    db_records = result.scalars().all()
    assert len(db_records) == 2


@pytest.mark.asyncio
async def test_bulk_insert_records_empty(session):
    """Empty rows list inserts nothing and returns 0."""
    user = await _create_user(session, tg_id=1005)
    user_id = user.id
    count = await bulk_insert_records(session, user_id, [])
    assert count == 0


# ==================== DB: get_or_create_account ====================


@pytest.mark.asyncio
async def test_get_or_create_account_new(session):
    """New account is created when it doesn't exist."""
    user = await _create_user(session, tg_id=1003)
    user_id = user.id
    acc = await get_or_create_account(session, user_id, "Карта")
    await session.commit()
    await session.refresh(acc)  # re-load after commit
    assert acc is not None
    assert acc.name == "Карта"
    assert acc.user_id == user_id


@pytest.mark.asyncio
async def test_get_or_create_account_existing(session):
    """Existing account is returned without creating a duplicate."""
    from core.database.models import Account

    user = await _create_user(session, tg_id=1004)
    user_id = user.id
    existing = Account(user_id=user_id, name="Наличные")
    session.add(existing)
    await session.commit()
    await session.refresh(existing)
    existing_id = existing.id

    acc = await get_or_create_account(session, user_id, "Наличные")
    assert acc is not None
    assert acc.id == existing_id


@pytest.mark.asyncio
async def test_get_or_create_account_limit(session):
    """Returns None when user already has 10 accounts."""
    from core.database.models import Account

    user = await _create_user(session, tg_id=1006)
    user_id = user.id
    for i in range(10):
        session.add(Account(user_id=user_id, name=f"Счёт {i}"))
    await session.commit()

    acc = await get_or_create_account(session, user_id, "Новый счёт")
    assert acc is None
