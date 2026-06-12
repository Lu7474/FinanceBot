"""Tests for report/caption formatters in core/reports.py.

Covers the pure text builders that were previously untested:
format_budget_status, format_period_caption, format_stacked_caption,
format_balance_caption, format_yearly_report, make_comparison_text, plus
the truncation / dict-record edge cases of make_report_text.
make_report_text basics, format_budget_trend and get_available_years_and_months
are covered elsewhere (test_utils, test_budgets, test_db_extended).
"""

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from config import MAX_CAPTION_LENGTH
from core.reports import (
    format_balance_caption,
    format_budget_status,
    format_period_caption,
    format_stacked_caption,
    format_yearly_report,
    make_comparison_text,
    make_report_text,
)

# ==================== format_budget_status ====================


def test_format_budget_status_empty():
    text = format_budget_status([])
    assert "Нет активных бюджетов" in text


def test_format_budget_status_over_limit_warns():
    budgets = [
        {
            "category": "Еда",
            "pct": 120,
            "spent": Decimal("6000"),
            "limit": Decimal("5000"),
        }
    ]
    text = format_budget_status(budgets)
    assert "Еда" in text
    assert "120%" in text
    assert "⚠️" in text


def test_format_budget_status_under_limit_no_warn():
    budgets = [
        {"category": "Кафе", "pct": 40, "spent": Decimal("400"), "limit": Decimal("1000")}
    ]
    text = format_budget_status(budgets)
    assert "⚠️" not in text


def test_format_budget_status_escapes_html():
    budgets = [
        {"category": "<b>x</b>", "pct": 10, "spent": Decimal("1"), "limit": Decimal("10")}
    ]
    text = format_budget_status(budgets)
    assert "&lt;b&gt;x&lt;/b&gt;" in text


# ==================== format_period_caption ====================


def test_format_period_caption_basic():
    cats = {"Еда": Decimal("1200"), "Кафе": Decimal("800")}
    text = format_period_caption(cats, Decimal("2000"), "2025 год", "expense")
    assert "Расходы" in text
    assert "2025 год" in text
    # Sorted desc: Еда (1200) before Кафе (800).
    assert text.index("Еда") < text.index("Кафе")


def test_format_period_caption_income_icon():
    text = format_period_caption({"Зарплата": Decimal("5000")}, Decimal("5000"), "Q1", "income")
    assert "Доходы" in text


def test_format_period_caption_truncates():
    cats = {f"Категория-{i}": Decimal("100") for i in range(200)}
    text = format_period_caption(cats, Decimal("20000"), "год", "expense")
    assert len(text) <= MAX_CAPTION_LENGTH
    assert "обрезано" in text


# ==================== format_stacked_caption ====================


def test_format_stacked_caption_empty():
    text = format_stacked_caption([], "-")
    assert "Нет данных" in text


def test_format_stacked_caption_single_month():
    data = [
        {"year": 2025, "month": 5, "category": "Еда", "total": Decimal("1000")},
        {"year": 2025, "month": 5, "category": "Кафе", "total": Decimal("500")},
    ]
    text = format_stacked_caption(data, "-")
    assert "Май 2025" in text
    assert "Еда" in text and "Кафе" in text


def test_format_stacked_caption_cross_month_period():
    data = [
        {"year": 2025, "month": 4, "category": "Еда", "total": Decimal("1000")},
        {"year": 2025, "month": 6, "category": "Еда", "total": Decimal("2000")},
    ]
    text = format_stacked_caption(data, "-")
    assert "Апрель 2025" in text and "Июнь 2025" in text


def test_format_stacked_caption_folds_tail_into_other():
    # More than MAX_CATEGORIES_IN_PIE (5) categories → tail collapses to "Прочее".
    data = [
        {"year": 2025, "month": 5, "category": f"cat{i}", "total": Decimal(str(100 - i))}
        for i in range(8)
    ]
    text = format_stacked_caption(data, "-")
    assert "Прочее" in text


# ==================== format_balance_caption ====================


def test_format_balance_caption_empty():
    text = format_balance_caption([], 2025, 5)
    assert "Нет данных за месяц" in text


def test_format_balance_caption_positive_net():
    daily = [(1, Decimal("1000")), (5, Decimal("-300"))]
    text = format_balance_caption(daily, 2025, 5)
    assert "🟢" in text  # net positive
    assert "Доходы" in text and "Расходы" in text


def test_format_balance_caption_negative_net_and_extremes():
    daily = [(1, Decimal("100")), (2, Decimal("-500")), (3, Decimal("200"))]
    text = format_balance_caption(daily, 2025, 5)
    assert "🔴" in text  # net negative (100-500+200 = -200)
    # running: 100, -400, -200 → high=100, low=-400
    assert "Максимум" in text and "Минимум" in text


# ==================== format_yearly_report ====================


def test_format_yearly_report_specific_year_monthly_table():
    data = [
        {"year": 2024, "month": 1, "category": "Еда", "total": Decimal("1000")},
        {"year": 2024, "month": 2, "category": "Кафе", "total": Decimal("500")},
    ]
    text = format_yearly_report(data, 2024, "-")
    assert "2024" in text
    assert "<pre>" in text  # monthly table
    assert "Среднее/мес" in text


def test_format_yearly_report_all_time_per_year():
    data = [
        {"year": 2023, "month": 5, "category": "Еда", "total": Decimal("1000")},
        {"year": 2024, "month": 6, "category": "Еда", "total": Decimal("2000")},
    ]
    text = format_yearly_report(data, None, "-")
    assert "за всё время" in text
    assert "Среднее/год" in text


def test_format_yearly_report_income_wording():
    data = [{"year": 2024, "month": 1, "category": "Зарплата", "total": Decimal("5000")}]
    text = format_yearly_report(data, 2024, "+")
    assert "доходов" in text


# ==================== make_comparison_text ====================


def test_make_comparison_text_increase():
    text = make_comparison_text(
        current_categories={"Еда": Decimal("1500")},
        prev_categories={"Еда": Decimal("1000")},
        current_total=Decimal("1500"),
        prev_total=Decimal("1000"),
        current_month=(2025, 5),
        prev_month=(2025, 4),
        report_type="expense",
    )
    assert "Сравнение" in text
    assert "📈" in text  # total increased
    assert "+50%" in text  # 500/1000


def test_make_comparison_text_decrease():
    text = make_comparison_text(
        current_categories={"Еда": Decimal("500")},
        prev_categories={"Еда": Decimal("1000")},
        current_total=Decimal("500"),
        prev_total=Decimal("1000"),
        current_month=(2025, 5),
        prev_month=(2025, 4),
        report_type="expense",
    )
    assert "📉" in text


def test_make_comparison_text_no_prev_no_pct():
    text = make_comparison_text(
        current_categories={"Еда": Decimal("500")},
        prev_categories={},
        current_total=Decimal("500"),
        prev_total=Decimal("0"),
        current_month=(2025, 5),
        prev_month=(2025, 4),
        report_type="expense",
    )
    # No percentage when prev_total == 0.
    assert "%" not in text


def test_make_comparison_text_avg_monthly_line():
    text = make_comparison_text(
        current_categories={},
        prev_categories={},
        current_total=Decimal("0"),
        prev_total=Decimal("0"),
        current_month=(2025, 5),
        prev_month=(2025, 4),
        report_type="expense",
        avg_monthly=Decimal("1234"),
    )
    assert "Средний за период" in text


def test_make_comparison_text_escapes_category():
    text = make_comparison_text(
        current_categories={"<b>x</b>": Decimal("100")},
        prev_categories={"<b>x</b>": Decimal("50")},
        current_total=Decimal("100"),
        prev_total=Decimal("50"),
        current_month=(2025, 5),
        prev_month=(2025, 4),
        report_type="expense",
    )
    assert "&lt;b&gt;x&lt;/b&gt;" in text


# ==================== make_report_text edge cases ====================


def test_make_report_text_with_dict_records():
    # Records given as dicts (not ORM objects) exercise the dict access branch.
    date = datetime(2025, 5, 14)
    records = [
        {
            "operation": "-",
            "amount": Decimal("200"),
            "category": "Еда",
            "created_at": datetime(2025, 5, 10),
        }
    ]
    text = make_report_text({"Еда": Decimal("200")}, Decimal("200"), date, "expense", records)
    assert "10.05" in text
    assert "Еда" in text


def test_make_report_text_truncates_long_output():
    date = datetime(2025, 5, 14)
    cats = {f"Очень-длинная-категория-{i}": Decimal("100") for i in range(300)}
    text = make_report_text(cats, Decimal("30000"), date, "expense")
    assert len(text) <= MAX_CAPTION_LENGTH
    assert "обрезано" in text
