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
    aggregate_search_by_description,
    get_history_data,
    get_top_categories_for_period,
    search_records,
    set_user,
)
from core.handlers.history import _build_breakdown_text, _build_search_page_text
from core.utils import parse_search_query, strip_search_needle

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


def test_parse_search_query_quoted_is_whole_word():
    result = parse_search_query('"газ"')
    assert result["type"] == "text"
    assert result["value"] == "газ"
    assert result["whole_word"] is True


def test_parse_search_query_unquoted_no_whole_word():
    result = parse_search_query("газ")
    assert "whole_word" not in result


def test_parse_search_query_quoted_skips_amount_parsing():
    # quotes force literal text search, even for amount-like content
    result = parse_search_query('">1000"')
    assert result["value"] == ">1000"
    assert result["whole_word"] is True


def test_parse_search_query_empty_quotes_no_whole_word():
    # "" / "  " → plain empty text, NOT whole_word (else it would skip the
    # truthy-value guard and return all records)
    for q in ('""', '"  "'):
        result = parse_search_query(q)
        assert result["value"] == ""
        assert "whole_word" not in result


def test_parse_search_query_single_quote_is_plain_text():
    # one-sided quote is not a whole-word query
    assert "whole_word" not in parse_search_query('"газ')
    assert "whole_word" not in parse_search_query('газ"')


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


@pytest.mark.asyncio
async def test_search_records_matches_description(session):
    uid = await _make_user(session, tg_id=130)
    session.add(Record(
        user_id=uid, operation="-", amount=Decimal("450"),
        category="Транспорт", description="такси до аэропорта",
    ))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "аэропорт")
    assert total == 1
    assert records[0].description == "такси до аэропорта"


@pytest.mark.asyncio
async def test_search_records_description_case_insensitive(session):
    uid = await _make_user(session, tg_id=131)
    session.add(Record(
        user_id=uid, operation="-", amount=Decimal("300"),
        category="Подарки", description="Цветы Маме",
    ))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "цветы")
    assert total == 1
    assert records[0].description == "Цветы Маме"


@pytest.mark.asyncio
async def test_search_records_matches_category_or_description(session):
    uid = await _make_user(session, tg_id=132)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Кафе"))
    session.add(Record(
        user_id=uid, operation="-", amount=Decimal("250"),
        category="Развлечения", description="кафе с друзьями",
    ))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("500"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "кафе")
    assert total == 2
    found = {(r.category, r.description) for r in records}
    assert found == {("Кафе", None), ("Развлечения", "кафе с друзьями")}


@pytest.mark.asyncio
async def test_search_records_partial_matches_substring(session):
    # unquoted search is substring: "газ" finds "газель"
    uid = await _make_user(session, tg_id=150)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Газель"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Газ"))
    await session.commit()

    total, _, _, _ = await search_records(session, uid, "газ")
    assert total == 2  # both "Газ" and "Газель"


@pytest.mark.asyncio
async def test_search_records_whole_word_excludes_substring(session):
    # quoted search is whole-word: "газ" excludes "газель"
    uid = await _make_user(session, tg_id=151)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Газель"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"), category="Газ"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, '"газ"')
    assert total == 1
    assert records[0].category == "Газ"


@pytest.mark.asyncio
async def test_search_records_whole_word_matches_inside_phrase(session):
    # whole-word matches the term as a word inside a multi-word description
    uid = await _make_user(session, tg_id=152)
    session.add(Record(
        user_id=uid, operation="-", amount=Decimal("100"),
        category="Машина", description="газ solaris",
    ))
    session.add(Record(
        user_id=uid, operation="-", amount=Decimal("200"),
        category="Машина", description="газель solaris",
    ))
    await session.commit()

    total, _, _, records = await search_records(session, uid, '"газ"')
    assert total == 1
    assert records[0].description == "газ solaris"


@pytest.mark.asyncio
async def test_search_records_whole_word_case_insensitive(session):
    uid = await _make_user(session, tg_id=153)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Газ"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, '"ГАЗ"')
    assert total == 1
    assert records[0].category == "Газ"


@pytest.mark.asyncio
async def test_search_records_whole_word_escapes_like_wildcards(session):
    # "%" inside needle must be literal, not a LIKE wildcard
    uid = await _make_user(session, tg_id=154)
    session.add(Record(
        user_id=uid, operation="-", amount=Decimal("100"),
        category="Скидка", description="50% solaris",
    ))
    session.add(Record(
        user_id=uid, operation="-", amount=Decimal("200"),
        category="Еда", description="обед",
    ))
    await session.commit()

    total, _, _, records = await search_records(session, uid, '"50%"')
    assert total == 1
    assert records[0].description == "50% solaris"


@pytest.mark.asyncio
async def test_search_records_null_description_not_matched(session):
    uid = await _make_user(session, tg_id=133)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"), category="Еда"))
    await session.commit()

    total, _, _, records = await search_records(session, uid, "Еда")
    assert total == 1, "NULL description must not break category match"

    total_miss, _, _, _ = await search_records(session, uid, "такси")
    assert total_miss == 0


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


# ==================== strip_search_needle ====================


def test_strip_needle_removes_text_term():
    assert strip_search_needle("газ solaris", "solaris") == "газ"


def test_strip_needle_case_and_space_insensitive():
    assert strip_search_needle("Газ  SOLARIS ", "solaris") == "Газ"


def test_strip_needle_full_match_keeps_original():
    # stripping would empty the text → keep original (only real-empty → "(без описания)")
    assert strip_search_needle("solaris", "solaris") == "solaris"


def test_strip_needle_whole_word_only():
    # broad LIKE search "газ" finds "газель" but display must NOT mutilate it
    assert strip_search_needle("газель", "газ") == "газель"
    assert strip_search_needle("газель газ", "газ") == "газель"


def test_strip_needle_amount_query_keeps_text():
    assert strip_search_needle("ремонт", ">1000") == "ремонт"


def test_strip_needle_empty_query_keeps_text():
    assert strip_search_needle("газ", "") == "газ"


# ==================== aggregate_search_by_description ====================


@pytest.mark.asyncio
async def test_aggregate_groups_and_normalizes(session):
    uid = await _make_user(session, tg_id=140)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"),
                       category="Машина", description="газ solaris"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("200"),
                       category="Машина", description="Газ  SOLARIS"))  # same group
    session.add(Record(user_id=uid, operation="-", amount=Decimal("1000"),
                       category="Машина", description="ремонт solaris"))
    await session.commit()

    groups = await aggregate_search_by_description(session, uid, "solaris")
    by_label = {g["label"].casefold(): g for g in groups}

    assert by_label["газ"]["expense"] == Decimal("300")  # 100 + 200 merged
    assert by_label["газ"]["count"] == 2
    assert by_label["ремонт"]["expense"] == Decimal("1000")
    assert by_label["ремонт"]["count"] == 1


@pytest.mark.asyncio
async def test_aggregate_empty_description_label(session):
    uid = await _make_user(session, tg_id=141)
    # matched via category, description empty → "(без описания)"
    session.add(Record(user_id=uid, operation="-", amount=Decimal("500"),
                       category="Авто solaris", description=None))
    await session.commit()

    groups = await aggregate_search_by_description(session, uid, "solaris")
    assert len(groups) == 1
    assert groups[0]["label"] == "(без описания)"
    assert groups[0]["expense"] == Decimal("500")


@pytest.mark.asyncio
async def test_aggregate_description_equals_term_kept(session):
    uid = await _make_user(session, tg_id=144)
    # description == search term → stripping would empty it, original is kept as label
    session.add(Record(user_id=uid, operation="-", amount=Decimal("500"),
                       category="Машина", description="solaris"))
    await session.commit()

    groups = await aggregate_search_by_description(session, uid, "solaris")
    assert len(groups) == 1
    assert groups[0]["label"] == "solaris"


@pytest.mark.asyncio
async def test_aggregate_sorted_by_expense_desc(session):
    uid = await _make_user(session, tg_id=142)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"),
                       category="Машина", description="газ solaris"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("9000"),
                       category="Машина", description="ремонт solaris"))
    session.add(Record(user_id=uid, operation="-", amount=Decimal("500"),
                       category="Машина", description="мойка solaris"))
    await session.commit()

    groups = await aggregate_search_by_description(session, uid, "solaris")
    expenses = [g["expense"] for g in groups]
    assert expenses == sorted(expenses, reverse=True)
    assert groups[0]["label"].casefold() == "ремонт"


@pytest.mark.asyncio
async def test_aggregate_income_and_expense_separated(session):
    uid = await _make_user(session, tg_id=143)
    session.add(Record(user_id=uid, operation="-", amount=Decimal("100"),
                       category="Машина", description="газ solaris"))
    session.add(Record(user_id=uid, operation="+", amount=Decimal("50"),
                       category="Машина", description="газ solaris"))  # refund
    await session.commit()

    groups = await aggregate_search_by_description(session, uid, "solaris")
    assert len(groups) == 1
    g = groups[0]
    assert g["expense"] == Decimal("100")
    assert g["income"] == Decimal("50")
    assert g["count"] == 2


# ==================== search results rendering (A1) ====================


def test_search_page_shows_description_without_needle():
    recs = [
        Record(operation="-", amount=Decimal("800"), category="Машина",
               description="мойка solaris", created_at=datetime(2025, 1, 18, 12, 0)),
        Record(operation="-", amount=Decimal("5400"), category="Машина",
               description="ремонт solaris", created_at=datetime(2025, 1, 18, 13, 0)),
    ]
    text = _build_search_page_text(recs, 0, 1, 2, "solaris")
    assert "Машина · <i>мойка</i>" in text
    assert "Машина · <i>ремонт</i>" in text
    # the needle is stripped from record lines (it only stays in the header)
    header, _, body = text.partition("\n")
    assert "solaris" in header
    assert "solaris" not in body


def test_search_page_empty_description_no_separator():
    recs = [
        Record(operation="-", amount=Decimal("100"), category="Машина",
               description=None, created_at=datetime(2025, 1, 18, 12, 0)),
    ]
    text = _build_search_page_text(recs, 0, 1, 1, "Машина")
    assert " · " not in text


# ==================== breakdown text ====================


def test_build_breakdown_text_empty():
    text = _build_breakdown_text("solaris", [])
    assert "Ничего не найдено" in text


def test_build_breakdown_text_lists_groups_and_totals():
    groups = [
        {"label": "ремонт", "expense": Decimal("9000"), "income": Decimal("0"), "count": 2},
        {"label": "газ", "expense": Decimal("1200"), "income": Decimal("0"), "count": 8},
    ]
    text = _build_breakdown_text("solaris", groups)
    assert "<b>ремонт</b>" in text
    assert "<b>газ</b>" in text
    assert "(2)" in text
    assert "(8)" in text
    assert "Итого расход" in text


def test_build_breakdown_text_mixed_group_shows_both_sums():
    groups = [
        {"label": "газ", "expense": Decimal("100"), "income": Decimal("50"), "count": 2},
    ]
    text = _build_breakdown_text("solaris", groups)
    # both expense and income must appear for a mixed group (regression: income was dropped)
    assert "−" in text and "100" in text
    assert "+" in text and "50" in text
