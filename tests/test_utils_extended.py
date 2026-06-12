"""Tests for format_capital_snapshot, format_capital, format_date_ru, normalize_category."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.utils import (
    format_capital,
    format_capital_snapshot,
    format_date_ru,
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


# ==================== format_capital_snapshot helpers ====================


def _snap(name: str, amount, type_: str = "A"):
    return NS(type=type_, name=name, amount=Decimal(str(amount)))


# ==================== format_capital_snapshot ====================


def test_format_capital_snapshot_contains_names():
    items = [_snap("Карта", "5000"), _snap("Наличные", "3000")]
    text = format_capital_snapshot(items, None, date(2025, 6, 1))
    assert "Карта" in text
    assert "Наличные" in text


def test_format_capital_snapshot_assets_total():
    items = [_snap("Карта", "5000"), _snap("Наличные", "3000")]
    text = format_capital_snapshot(items, None, date(2025, 6, 1))
    assert "8 000" in text


def test_format_capital_snapshot_date_in_output():
    text = format_capital_snapshot([], None, date(2025, 6, 1))
    assert "июня" in text
    assert "2025" in text


def test_format_capital_snapshot_no_prev_no_diff_markers():
    items = [_snap("Карта", "5000")]
    text = format_capital_snapshot(items, None, date(2025, 7, 1))
    assert "(+" not in text
    assert "(−" not in text
    assert "(=" not in text
    assert "новое" not in text


def test_format_capital_snapshot_prev_growth():
    items = [_snap("Карта", "6000")]
    prev = [_snap("Карта", "5000")]
    text = format_capital_snapshot(items, prev, date(2025, 7, 1))
    assert "+1 000" in text


def test_format_capital_snapshot_prev_decline():
    items = [_snap("Карта", "4000")]
    prev = [_snap("Карта", "5000")]
    text = format_capital_snapshot(items, prev, date(2025, 7, 1))
    assert "−1 000" in text


def test_format_capital_snapshot_prev_equal():
    items = [_snap("Карта", "5000")]
    prev = [_snap("Карта", "5000")]
    text = format_capital_snapshot(items, prev, date(2025, 7, 1))
    assert "(=)" in text


def test_format_capital_snapshot_new_item_marked():
    items = [_snap("Новый", "3000")]
    prev = [_snap("Карта", "5000")]
    text = format_capital_snapshot(items, prev, date(2025, 7, 1))
    assert "Новый" in text
    assert "новое" in text


def test_format_capital_snapshot_diff_keyed_by_type():
    """Same name, different type → treated as a new row, not a diff."""
    items = [_snap("X", "5000", type_="P")]
    prev = [_snap("X", "5000", type_="A")]
    text = format_capital_snapshot(items, prev, date(2025, 7, 1))
    assert "новое" in text


def test_format_capital_snapshot_net_worth_with_liabilities():
    items = [_snap("Актив", "100000", "A"), _snap("Ипотека", "30000", "P")]
    text = format_capital_snapshot(items, None, date(2025, 7, 1))
    assert "Чистый капитал" in text
    assert "70 000" in text


def test_format_capital_snapshot_empty_items():
    text = format_capital_snapshot([], None, date(2025, 1, 1))
    assert "Чистый капитал" in text
    assert "0₽" in text


# ==================== format_capital helpers ====================


def _wi(type_: str, name: str, amount, note: str | None = None):
    return NS(type=type_, name=name, amount=Decimal(str(amount)), note=note)


def _debt(direction: str, person: str, remaining):
    return NS(direction=direction, person_name=person, remaining=Decimal(str(remaining)))


# ==================== format_capital ====================


def test_format_capital_empty_shows_no_data():
    text = format_capital([], [], [])
    assert "АКТИВЫ" in text
    assert "ПАССИВЫ" in text
    assert "Нет данных" in text


def test_format_capital_empty_net_zero():
    text = format_capital([], [], [])
    assert "0₽" in text


def test_format_capital_asset_name_and_amount():
    text = format_capital([_wi("A", "Квартира", "5000000")], [], [])
    assert "Квартира" in text
    assert "5 000 000" in text


def test_format_capital_net_positive():
    items = [_wi("A", "Актив", "1000000"), _wi("P", "Долг", "300000")]
    text = format_capital(items, [], [])
    assert "+700 000" in text


def test_format_capital_net_negative_no_plus():
    items = [_wi("A", "Актив", "100000"), _wi("P", "Долг", "500000")]
    text = format_capital(items, [], [])
    assert "+400 000" not in text
    assert "-400 000" in text


def test_format_capital_with_note():
    text = format_capital([_wi("A", "Вклад", "500000", "Сбербанк")], [], [])
    assert "Сбербанк" in text


def test_format_capital_debt_in_asset_uses_remaining():
    """Debt I → asset «Мне должны», by remaining; counts toward net worth."""
    debts = [_debt("I", "Андрей", "15000")]
    text = format_capital([], debts, [])
    assert "Мне должны: Андрей" in text
    assert "15 000" in text
    assert "💳" in text


def test_format_capital_debt_o_in_liability():
    debts = [_debt("O", "Банк", "28000")]
    text = format_capital([], debts, [])
    assert "Долг: Банк" in text


def test_format_capital_positive_balance_is_asset():
    balances = [(NS(name="Наличка"), Decimal("45000"))]
    text = format_capital([], [], balances)
    # asset section appears before liability section
    a_idx = text.index("АКТИВЫ")
    p_idx = text.index("ПАССИВЫ")
    assert a_idx < text.index("Наличка") < p_idx


def test_format_capital_negative_balance_is_liability_abs():
    balances = [(NS(name="Кредитка"), Decimal("-5000"))]
    text = format_capital([], [], balances)
    # liability row shows the absolute value, after the ПАССИВЫ header
    p_idx = text.index("ПАССИВЫ")
    assert "Кредитка  —  5 000₽" in text[p_idx:]


def test_format_capital_zero_balance_skipped():
    balances = [(NS(name="Пустой"), Decimal("0"))]
    text = format_capital([], [], balances)
    assert "Пустой" not in text


def test_format_capital_last_snapshot_diff_line():
    snap = NS(date=date(2025, 6, 1), items=[_snap("x", "100000", "A")])
    text = format_capital([_wi("A", "Деньги", "120000")], [], [], snap)
    assert "Последний снимок" in text
    assert "июня" in text


def test_format_capital_no_snapshot_no_diff_line():
    text = format_capital([_wi("A", "Деньги", "120000")], [], [])
    assert "Последний снимок" not in text


# ==================== XSS: format_record_card (review #7) ====================


class _FakeAccount:
    def __init__(self, name: str):
        self.name = name


class _FakeRecord:
    def __init__(
        self, category: str, account_name: str | None, description: str | None = None
    ):
        from datetime import datetime

        self.id = 1
        self.operation = "-"
        self.amount = Decimal("100")
        self.category = category
        self.created_at = datetime(2025, 5, 17, 12, 0, 0)
        self.account = _FakeAccount(account_name) if account_name else None
        self.description = description


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


# ==================== XSS: format_capital_snapshot ====================


def test_format_capital_snapshot_escapes_html_in_item_name():
    name_xss = '<script>alert("xss")</script>'
    item = _snap(name=name_xss, amount=Decimal("500"))
    text = format_capital_snapshot([item], None, date(2025, 5, 1))
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_capital_snapshot_escapes_html_in_prev_item_name():
    """prev_items are used as a lookup map by (type, name) — injection via previous snapshot."""
    name_xss = '<img src=x onerror="steal()">'
    curr = _snap(name=name_xss, amount=Decimal("600"))
    prev = _snap(name=name_xss, amount=Decimal("400"))
    text = format_capital_snapshot([curr], [prev], date(2025, 5, 1))
    assert "<img" not in text
    assert "&lt;img" in text


# ==================== XSS: format_capital ====================


def test_format_capital_escapes_html_in_asset_name():
    item = _wi("A", "<img src=x>", Decimal("1000"))
    text = format_capital([item], [], [])
    assert "<img src=x>" not in text
    assert "&lt;img src=x&gt;" in text


def test_format_capital_escapes_html_in_liability_name():
    item = _wi("P", '<a href="evil">', Decimal("200"))
    text = format_capital([item], [], [])
    assert '<a href="evil">' not in text
    assert "&lt;a href=" in text


def test_format_capital_escapes_html_in_note():
    item = _wi("A", "Квартира", Decimal("5000000"), "<script>steal()</script>")
    text = format_capital([item], [], [])
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_capital_escapes_html_in_debt_person():
    debts = [_debt("I", "<b>evil</b>", Decimal("100"))]
    text = format_capital([], debts, [])
    assert "<b>evil</b>" not in text
    assert "&lt;b&gt;evil&lt;/b&gt;" in text


def test_format_capital_escapes_html_in_account_name():
    balances = [(NS(name="<i>acc</i>"), Decimal("100"))]
    text = format_capital([], [], balances)
    assert "<i>acc</i>" not in text
    assert "&lt;i&gt;acc&lt;/i&gt;" in text
