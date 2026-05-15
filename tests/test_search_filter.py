"""Tests for search and filter features: parse_search_query, search_records,
get_history_data with filters, get_top_categories_for_period."""
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import Record
from core.database.requests import (
    get_history_data,
    get_top_categories_for_period,
    search_records,
    set_user,
)
from core.utils import parse_search_query

# ==================== parse_search_query ====================


def test_parse_search_query_gt():
    result = parse_search_query(">1000")
    assert result == {"type": "gt", "value": 1000.0}


def test_parse_search_query_lt():
    result = parse_search_query("<500")
    assert result == {"type": "lt", "value": 500.0}


def test_parse_search_query_eq():
    result = parse_search_query("=250")
    assert result == {"type": "eq", "value": 250.0}


def test_parse_search_query_text():
    result = parse_search_query("такси")
    assert result == {"type": "text", "value": "такси"}


def test_parse_search_query_text_preserves_case():
    result = parse_search_query("Еда")
    assert result["type"] == "text"
    assert result["value"] == "Еда"


def test_parse_search_query_gt_with_spaces():
    result = parse_search_query("> 999")
    assert result == {"type": "gt", "value": 999.0}


def test_parse_search_query_invalid_gt_falls_back_to_text():
    result = parse_search_query(">abc")
    assert result["type"] == "text"


def test_parse_search_query_empty_string():
    result = parse_search_query("")
    assert result["type"] == "text"
    assert result["value"] == ""


def test_parse_search_query_whitespace_only():
    result = parse_search_query("   ")
    assert result["type"] == "text"
    assert result["value"] == ""


def test_parse_search_query_eq_decimal():
    result = parse_search_query("=1500.50")
    assert result == {"type": "eq", "value": 1500.5}


def test_parse_search_query_income_gt():
    result = parse_search_query("+>1000")
    assert result == {"type": "gt", "value": 1000.0, "operation": "+"}


def test_parse_search_query_expense_gt():
    result = parse_search_query("->1000")
    assert result == {"type": "gt", "value": 1000.0, "operation": "-"}


def test_parse_search_query_expense_alias_gt():
    result = parse_search_query("expense>1000")
    assert result == {"type": "gt", "value": 1000.0, "operation": "-"}


# ==================== search_records ====================


async def _make_user(session, tg_id: int = 99):
    user = await set_user(session, tg_id, name="SearchTest")
    return user.id


@pytest.mark.asyncio
async def test_search_records_text_match(session):
    uid = await _make_user(session)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Такси"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("300"), category="Транспорт"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "ТАКС")
    assert total == 1
    assert records[0].category == "Такси"


@pytest.mark.asyncio
async def test_search_records_text_partial_match(session):
    uid = await _make_user(session, tg_id=100)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Кафе Мира"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "Кафе")
    assert total == 1
    assert "Кафе" in records[0].category


@pytest.mark.asyncio
async def test_search_records_gt_amount(session):
    uid = await _make_user(session, tg_id=101)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("500"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("1500"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("2000"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, ">1000")
    assert total == 2
    assert all(float(r.amount) > 1000 for r in records)


@pytest.mark.asyncio
async def test_search_records_gt_amount_includes_income_and_expense(session):
    uid = await _make_user(session, tg_id=107)
    session.add(Record(user_id=uid, operation="+", amount=Decimal("1500"), category="Зарплата"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("8000"), category="Заказ"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("500"), category="Еда"))
    await session.commit()

    total, income_sum, expense_sum, records = await search_records(session, uid, ">1000")
    assert total == 2
    assert income_sum == Decimal("1500.00")
    assert expense_sum == Decimal("8000.00")
    assert {r.operation for r in records} == {"+", "-"}


@pytest.mark.asyncio
async def test_search_records_expense_amount_prefix(session):
    uid = await _make_user(session, tg_id=108)
    session.add(Record(user_id=uid, operation="+", amount=Decimal("1500"), category="Зарплата"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("8000"), category="Заказ"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("500"), category="Еда"))
    await session.commit()

    total, income_sum, expense_sum, records = await search_records(session, uid, "->1000")
    assert total == 1
    assert income_sum == Decimal("0")
    assert expense_sum == Decimal("8000.00")
    assert records[0].operation == "-"


@pytest.mark.asyncio
async def test_search_records_lt_amount(session):
    uid = await _make_user(session, tg_id=102)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("600"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "<500")
    assert total == 1
    assert float(records[0].amount) == 100.0


@pytest.mark.asyncio
async def test_search_records_eq_amount(session):
    uid = await _make_user(session, tg_id=103)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("250"), category="Кафе"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("300"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "=250")
    assert total == 1
    assert float(records[0].amount) == 250.0


@pytest.mark.asyncio
async def test_search_records_empty_result(session):
    uid = await _make_user(session, tg_id=104)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "несуществующая")
    assert total == 0
    assert records == []


@pytest.mark.asyncio
async def test_search_records_excludes_system_categories(session):
    uid = await _make_user(session, tg_id=105)
    session.add(Record(user_id=uid, operation="+", amount=Decimal("500"), category="Перевод"))
    session.add(Record(user_id=uid, operation="+", amount=Decimal("1000"), category="Установка баланса"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, ">0")
    assert total == 1
    assert records[0].category == "Еда"


@pytest.mark.asyncio
async def test_search_records_sorted_newest_first(session):
    uid = await _make_user(session, tg_id=106)
    old_dt = datetime(2025, 1, 1, 12, 0)
    new_dt = datetime(2025, 6, 1, 12, 0)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда", created_at=old_dt))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Еда", created_at=new_dt))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "Еда")
    assert total == 2
    assert records[0].created_at > records[1].created_at


@pytest.mark.asyncio
async def test_search_records_text_case_insensitive(session):
    uid = await _make_user(session, tg_id=107)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Заказ"))
    await session.commit()

    total_upper, _, _, records_upper = await search_records(session, uid, "ЗАКАЗ")
    total_lower, _, _, records_lower = await search_records(session, uid, "заказ")
    assert total_upper == 1, "ALL-CAPS query must find the record"
    assert total_lower == 1
    assert records_upper[0].category == records_lower[0].category


# ==================== get_history_data with filters ====================


@pytest.mark.asyncio
async def test_get_history_data_operation_filter_income(session):
    uid = await _make_user(session, tg_id=110)
    session.add(Record(user_id=uid, operation="+", amount=Decimal("1000"), category="Зарплата"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("300"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Транспорт"))
    await session.commit()

    total, income, expense, records = await get_history_data(
        session, uid, operation_filter="+"
    )
    assert total == 1
    assert all(r.operation == "+" for r in records)
    assert income == Decimal("1000")
    assert expense == Decimal("0")


@pytest.mark.asyncio
async def test_get_history_data_operation_filter_expense(session):
    uid = await _make_user(session, tg_id=111)
    session.add(Record(user_id=uid, operation="+", amount=Decimal("1000"), category="Зарплата"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("300"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Транспорт"))
    await session.commit()

    total, income, expense, records = await get_history_data(
        session, uid, operation_filter="-"
    )
    assert total == 2
    assert all(r.operation == "-" for r in records)
    assert income == Decimal("0")
    assert expense == Decimal("500")


@pytest.mark.asyncio
async def test_get_history_data_category_filter(session):
    uid = await _make_user(session, tg_id=112)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("150"), category="Кафе"))
    await session.commit()

    total, income, expense, records = await get_history_data(
        session, uid, category_filter="Еда"
    )
    assert total == 2
    assert all(r.category == "Еда" for r in records)
    assert expense == Decimal("300")


@pytest.mark.asyncio
async def test_get_history_data_combined_filters(session):
    uid = await _make_user(session, tg_id=113)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда"))
    session.add(Record(user_id=uid, operation="+", amount=Decimal("500"), category="Еда"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Кафе"))
    await session.commit()

    total, income, expense, records = await get_history_data(
        session, uid, operation_filter="-", category_filter="Еда"
    )
    assert total == 1
    assert records[0].operation == "-"
    assert records[0].category == "Еда"


# ==================== get_top_categories_for_period ====================


@pytest.mark.asyncio
async def test_get_top_categories_returns_sorted_by_frequency(session):
    uid = await _make_user(session, tg_id=120)
    for _ in range(5):
        session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда"))
    for _ in range(3):
        session.add(Record(user_id=uid, operation="-", amount=Decimal("50"), category="Кафе"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Транспорт"))
    await session.commit()

    cats = await get_top_categories_for_period(session, uid, "all")
    assert cats[0] == "Еда"
    assert cats[1] == "Кафе"
    assert cats[2] == "Транспорт"


@pytest.mark.asyncio
async def test_get_top_categories_max_15(session):
    uid = await _make_user(session, tg_id=121)
    for i in range(20):
        session.add(Record(
            user_id=uid, operation="-", amount=Decimal("100"), category=f"Категория{i}"
        ))
    await session.commit()

    cats = await get_top_categories_for_period(session, uid, "all")
    assert len(cats) <= 15


@pytest.mark.asyncio
async def test_get_top_categories_excludes_system(session):
    uid = await _make_user(session, tg_id=122)
    for _ in range(10):
        session.add(Record(user_id=uid, operation="+", amount=Decimal("100"), category="Перевод"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("50"), category="Еда"))
    await session.commit()

    cats = await get_top_categories_for_period(session, uid, "all")
    assert "Перевод" not in cats
    assert "Еда" in cats


@pytest.mark.asyncio
async def test_get_top_categories_empty_period(session):
    uid = await _make_user(session, tg_id=123)
    cats = await get_top_categories_for_period(session, uid, "all")
    assert cats == []
