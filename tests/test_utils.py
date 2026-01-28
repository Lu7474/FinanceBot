"""
Тесты утилит: генерация отчётов, графики, форматирование.
"""
import sys
from pathlib import Path
import pytest
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.utils import (
    build_report_pie,
    make_report_text,
    format_money,
    RU_MONTHS,
)


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
