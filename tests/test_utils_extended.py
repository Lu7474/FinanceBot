"""Tests for format_snapshot, format_wealth, format_date_ru, normalize_category."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.utils import (
    format_date_ru,
    format_snapshot,
    format_wealth,
    normalize_category,
)

# ==================== format_date_ru ====================


def test_format_date_ru_january():
    assert format_date_ru(date(2025, 1, 15)) == "15 января 2025"


def test_format_date_ru_december():
    assert format_date_ru(date(2024, 12, 31)) == "31 декабря 2024"


def test_format_date_ru_june():
    assert format_date_ru(date(2025, 6, 1)) == "1 июня 2025"


def test_format_date_ru_all_months():
    expected = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }
    for month, name in expected.items():
        result = format_date_ru(date(2025, month, 1))
        assert name in result, f"Month {month}: expected '{name}' in '{result}'"


# ==================== normalize_category ====================


def test_normalize_category_lowercase():
    assert normalize_category("кофе") == "Кофе"


def test_normalize_category_strips_spaces():
    assert normalize_category("  зарплата  ") == "Зарплата"


def test_normalize_category_already_capitalized():
    assert normalize_category("Еда") == "Еда"


def test_normalize_category_empty():
    assert normalize_category("") == ""


def test_normalize_category_preserves_rest():
    assert normalize_category("уже С заглавной") == "Уже С заглавной"


# ==================== format_snapshot helpers ====================


class _Item:
    def __init__(self, name: str, amount):
        self.name = name
        self.amount = Decimal(str(amount))


# ==================== format_snapshot ====================


def test_format_snapshot_contains_names():
    items = [_Item("Карта", "5000"), _Item("Наличные", "3000")]
    text = format_snapshot(items, None, date(2025, 6, 1))
    assert "Карта" in text
    assert "Наличные" in text


def test_format_snapshot_total():
    items = [_Item("Карта", "5000"), _Item("Наличные", "3000")]
    text = format_snapshot(items, None, date(2025, 6, 1))
    assert "8 000" in text


def test_format_snapshot_date_in_output():
    text = format_snapshot([], None, date(2025, 6, 1))
    assert "июня" in text
    assert "2025" in text


def test_format_snapshot_no_prev_no_diff_markers():
    items = [_Item("Карта", "5000")]
    text = format_snapshot(items, None, date(2025, 7, 1))
    assert "(+" not in text
    assert "(−" not in text
    assert "(=" not in text


def test_format_snapshot_prev_growth():
    items = [_Item("Карта", "6000")]
    prev = [_Item("Карта", "5000")]
    text = format_snapshot(items, prev, date(2025, 7, 1))
    assert "+1 000" in text


def test_format_snapshot_prev_decline():
    items = [_Item("Карта", "4000")]
    prev = [_Item("Карта", "5000")]
    text = format_snapshot(items, prev, date(2025, 7, 1))
    assert "−1 000" in text


def test_format_snapshot_prev_equal():
    items = [_Item("Карта", "5000")]
    prev = [_Item("Карта", "5000")]
    text = format_snapshot(items, prev, date(2025, 7, 1))
    assert "(=)" in text


def test_format_snapshot_new_item_no_diff():
    items = [_Item("Новый", "3000")]
    prev = [_Item("Карта", "5000")]
    text = format_snapshot(items, prev, date(2025, 7, 1))
    assert "Новый" in text
    assert "(+" not in text
    assert "(−" not in text


def test_format_snapshot_empty_items():
    text = format_snapshot([], None, date(2025, 1, 1))
    assert "Итого" in text
    assert "0₽" in text


# ==================== format_wealth helpers ====================


class _WealthItem:
    def __init__(self, type_: str, name: str, amount, note: str | None = None):
        self.type = type_
        self.name = name
        self.amount = Decimal(str(amount))
        self.note = note


# ==================== format_wealth ====================


def test_format_wealth_empty_shows_no_data():
    text = format_wealth([])
    assert "АКТИВЫ" in text
    assert "ПАССИВЫ" in text
    assert "Нет данных" in text


def test_format_wealth_empty_net_zero():
    text = format_wealth([])
    assert "0₽" in text


def test_format_wealth_asset_name_and_amount():
    items = [_WealthItem("A", "Квартира", "5000000")]
    text = format_wealth(items)
    assert "Квартира" in text
    assert "5 000 000" in text


def test_format_wealth_liabilities_no_data_when_empty():
    items = [_WealthItem("A", "Актив", "1000000")]
    text = format_wealth(items)
    assert "Нет данных" in text


def test_format_wealth_net_positive():
    items = [
        _WealthItem("A", "Актив", "1000000"),
        _WealthItem("P", "Долг", "300000"),
    ]
    text = format_wealth(items)
    assert "+700 000" in text


def test_format_wealth_net_negative_no_plus():
    items = [
        _WealthItem("A", "Актив", "100000"),
        _WealthItem("P", "Долг", "500000"),
    ]
    text = format_wealth(items)
    assert "+400 000" not in text
    assert "-400 000" in text


def test_format_wealth_with_note():
    items = [_WealthItem("A", "Вклад", "500000", "Сбербанк")]
    text = format_wealth(items)
    assert "Сбербанк" in text


def test_format_wealth_liability_shown():
    items = [_WealthItem("P", "Ипотека", "2000000")]
    text = format_wealth(items)
    assert "Ипотека" in text
    assert "2 000 000" in text


# ==================== XSS: format_record_card (review #7) ====================


class _FakeAccount:
    def __init__(self, name: str):
        self.name = name


class _FakeRecord:
    def __init__(self, category: str, account_name: str | None):
        from datetime import datetime

        self.id = 1
        self.operation = "-"
        self.amount = Decimal("100")
        self.category = category
        self.created_at = datetime(2025, 5, 17, 12, 0, 0)
        self.account = _FakeAccount(account_name) if account_name else None


def test_format_record_card_escapes_html_in_category():
    from core.utils import format_record_card

    rec = _FakeRecord(category="<script>alert(1)</script>", account_name="Карта")
    text = format_record_card(rec)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_record_card_escapes_html_in_account_name():
    from core.utils import format_record_card

    rec = _FakeRecord(category="Еда", account_name="<b>Карта</b>")
    text = format_record_card(rec)
    assert "<b>Карта</b>" not in text
    assert "&lt;b&gt;Карта&lt;/b&gt;" in text


def test_format_record_card_handles_missing_account():
    """account=None — рендерится '—', без падения и без HTML-инъекций."""
    from core.utils import format_record_card

    rec = _FakeRecord(category="Еда", account_name=None)
    text = format_record_card(rec)
    assert "—" in text
