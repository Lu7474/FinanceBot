import sys
from pathlib import Path
import pytest
from datetime import datetime
from collections import defaultdict

# Добавляем путь к корню проекта
sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.utils import (
    parse_date,
    build_report_pie,
    make_report_text,
    make_history_text,
    RU_MONTHS,
)


def test_parse_date_formats():
    d1 = parse_date("10.06.24")
    assert d1 is not None
    assert d1.day == 10 and d1.month == 6 and d1.year == 2024

    d2 = parse_date("10.06.2024")
    assert d2 is not None
    assert d2.year == 2024

    d4 = parse_date("не дата")
    assert d4 is None


def test_make_report_text():
    categories = {"Еда": 1000.0, "Транспорт": 500.0}
    date = datetime(2024, 6, 1)
    text = make_report_text(categories, 1500.0, date)
    assert "Еда" in text and "Транспорт" in text
    assert f"{RU_MONTHS[6]} 2024" in text


def test_build_report_pie_and_caption():
    categories = {"Еда": 1000.0, "Транспорт": 500.0}
    date = datetime(2024, 6, 1)
    buf, caption = build_report_pie(categories, 1500.0, date)

    assert buf is not None
    assert caption.startswith("📊 Траты за")
    assert "Еда" in caption

    # Проверка с пустыми данными
    empty_buf, empty_caption = build_report_pie({}, 0, date)
    assert empty_buf is None
    assert "Нет данных для построения отчета" in empty_caption


def test_make_history_text():
    class FakeRecord:
        def __init__(self, op, amount, cat, dt):
            self.operation = op
            self.amount = amount
            self.category = cat
            self.created_at = dt

    now = datetime(2024, 6, 10)
    records = [
        FakeRecord("+", 1000.0, "Зарплата", now),
        FakeRecord("-", 500.0, "Еда", now),
    ]
    text = make_history_text(records)
    assert "Зарплата" in text
    assert "Еда" in text
    assert "Сумма доходов" in text
    assert "Сумма расходов" in text
    assert "Остаток" in text

    # Пустой список
    empty_text = make_history_text([])
    assert "Нет записей" in empty_text
