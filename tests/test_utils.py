"""
Тесты утилит: генерация отчётов, графики, форматирование.
"""

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.charts import build_report_pie
from core.reports import make_report_text
from core.utils import RU_MONTHS, format_money, parse_flex_date

# ==================== format_money ====================


def test_format_money():
    """Форматирование сумм с пробелами."""
    assert format_money(1000) == "1 000₽"
    assert format_money(1000000) == "1 000 000₽"
    assert format_money(500) == "500₽"
    assert format_money(0) == "0₽"


# ==================== make_report_text ====================


def test_make_report_text():
    """Формирование текстового отчёта с категориями и итогом."""
    categories = {"Еда": 1000.0, "Транспорт": 500.0}
    date = datetime(2024, 6, 1)
    text = make_report_text(categories, 1500.0, date, "expense")
    assert "Еда" in text and "Транспорт" in text
    assert RU_MONTHS[6] in text
    assert "Расходы" in text

    # Проверка для доходов
    text_income = make_report_text(categories, 1500.0, date, "income")
    assert "Доходы" in text_income


def test_make_report_text_with_records():
    """Отчёт с детализацией по датам."""

    class FakeRecord:
        def __init__(self, op, amount, cat, dt):
            self.operation = op
            self.amount = amount
            self.category = cat
            self.created_at = dt

    categories = {"Зарплата": 5000.0}
    date = datetime(2024, 6, 1)
    records = [FakeRecord("+", 5000.0, "Зарплата", datetime(2024, 6, 15))]

    text = make_report_text(categories, 5000.0, date, "income", records)
    assert "По датам" in text
    assert "15.06" in text


# ==================== build_report_pie ====================


@pytest.mark.asyncio
async def test_build_report_pie_and_caption():
    """Генерация графика: буфер + caption, пустые данные → None."""
    categories = {"Еда": 1000.0, "Транспорт": 500.0}
    date = datetime(2024, 6, 1)
    buf, caption = await build_report_pie(categories, 1500.0, date, "expense")

    assert buf is not None
    assert "Расходы" in caption
    assert "Еда" in caption

    # Проверка для доходов
    buf_inc, caption_inc = await build_report_pie(categories, 1500.0, date, "income")
    assert buf_inc is not None
    assert "Доходы" in caption_inc

    # Проверка с пустыми данными
    empty_buf, empty_caption = await build_report_pie({}, 0, date, "expense")
    assert empty_buf is None
    assert "Нет данных для построения отчета" in empty_caption


# ==================== parse_flex_date ====================


def test_parse_flex_date_two_digit_year():
    """ДД.ММ.ГГ → 2000-е."""
    assert parse_flex_date("20.02.20") == date(2020, 2, 20)
    assert parse_flex_date("01.06.26") == date(2026, 6, 1)


def test_parse_flex_date_four_digit_year():
    """ДД.ММ.ГГГГ тоже принимается."""
    assert parse_flex_date("20.02.2020") == date(2020, 2, 20)


def test_parse_flex_date_single_digit_day_month():
    """Без ведущих нулей."""
    assert parse_flex_date("5.6.24") == date(2024, 6, 5)


def test_parse_flex_date_strips_whitespace():
    assert parse_flex_date("  20.02.20  ") == date(2020, 2, 20)


def test_parse_flex_date_rejects_missing_year():
    """ДД.ММ без года не принимается (год обязателен)."""
    assert parse_flex_date("20.02") is None


def test_parse_flex_date_rejects_extra_part():
    """Лишняя точка / 4 части → None."""
    assert parse_flex_date("20.02.2020.") is None
    assert parse_flex_date("20.02.20.30") is None


def test_parse_flex_date_rejects_three_digit_year():
    assert parse_flex_date("20.02.020") is None


def test_parse_flex_date_rejects_invalid_date():
    """Несуществующая дата → None."""
    assert parse_flex_date("32.13.20") is None
    assert parse_flex_date("99.99.99") is None


def test_parse_flex_date_rejects_garbage():
    assert parse_flex_date("abc") is None
    assert parse_flex_date("") is None
    assert parse_flex_date("20-02-2020") is None


def test_parse_flex_date_two_digit_year_century_pivot():
    """Документирует поведение %y: 00–68 → 20xx, 69–99 → 19xx."""
    assert parse_flex_date("01.01.68") == date(2068, 1, 1)
    assert parse_flex_date("01.01.69") == date(1969, 1, 1)
