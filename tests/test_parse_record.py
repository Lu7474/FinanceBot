"""
Тесты парсинга записей: суммы, категории, даты.
"""
import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.handlers import parse_record_line


# ==================== Базовый парсинг ====================

def test_parse_simple_record():
    """Парсинг простой записи: сумма + категория."""
    result = parse_record_line("500 еда", default_operation="-")
    assert result is not None
    op, amount, category, date = result
    assert op == "-"
    assert amount == Decimal("500")
    assert category == "еда"
    assert date is None


def test_parse_record_with_sign():
    """Парсинг записи со знаком операции."""
    result = parse_record_line("+1000 зарплата")
    assert result is not None
    op, amount, category, date = result
    assert op == "+"
    assert amount == Decimal("1000")
    assert category == "зарплата"

    result2 = parse_record_line("-500 кафе")
    assert result2 is not None
    assert result2[0] == "-"
    assert result2[1] == Decimal("500")


def test_parse_record_decimal():
    """Парсинг записи с дробной суммой."""
    result = parse_record_line("99.50 кофе", default_operation="-")
    assert result is not None
    assert result[1] == Decimal("99.50")

    result2 = parse_record_line("100,50 обед", default_operation="-")
    assert result2 is not None
    assert result2[1] == Decimal("100.50")


# ==================== Парсинг с датой ====================

def test_parse_record_with_date_ddmm():
    """Парсинг записи с датой ДД.ММ."""
    result = parse_record_line("27.01 500 продукты", default_operation="-")
    assert result is not None
    op, amount, category, date = result
    assert op == "-"
    assert amount == Decimal("500")
    assert category == "продукты"
    assert date is not None
    assert date.day == 27
    assert date.month == 1
    assert date.year == datetime.now().year


def test_parse_record_with_date_ddmmyy():
    """Парсинг записи с датой ДД.ММ.ГГ."""
    result = parse_record_line("15.12.25 1000 подарок", default_operation="+")
    assert result is not None
    op, amount, category, date = result
    assert op == "+"
    assert amount == Decimal("1000")
    assert category == "подарок"
    assert date is not None
    assert date.day == 15
    assert date.month == 12
    assert date.year == 2025


def test_parse_record_with_date_and_sign():
    """Парсинг записи с датой и знаком операции."""
    result = parse_record_line("20.01.26 +5000 зарплата")
    assert result is not None
    op, amount, category, date = result
    assert op == "+"
    assert amount == Decimal("5000")
    assert category == "зарплата"
    assert date.day == 20
    assert date.month == 1
    assert date.year == 2026

    result2 = parse_record_line("20.01 -350 магазин")
    assert result2 is not None
    assert result2[0] == "-"
    assert result2[1] == Decimal("350")
    assert result2[2] == "магазин"


# ==================== Ошибки парсинга ====================

def test_parse_invalid_date():
    """Невалидная дата (32.13) не распознаётся как дата, парсится как сумма."""
    result = parse_record_line("32.13 500 тест", default_operation="-")
    assert result is not None
    # 32.13 парсится как сумма (не как дата), категория "500 тест"
    assert result[1] == Decimal("32.13")
    assert result[3] is None  # Нет даты


def test_parse_invalid_date_february():
    """Невалидная дата 31.02 (31 февраля) — regex матчит, но дата невалидна.
    Невалидная часть пропускается, парсится оставшаяся часть строки."""
    result = parse_record_line("31.02 500 тест", default_operation="-")
    assert result is not None
    assert result[1] == Decimal("500")  # Парсится сумма после невалидной даты
    assert result[2] == "тест"  # Категория
    assert result[3] is None  # Дата невалидна — не установлена


def test_parse_no_operation():
    """Без default_operation и знака → None."""
    result = parse_record_line("500 еда")
    assert result is None


def test_parse_empty_line():
    """Пустая строка → None."""
    result = parse_record_line("", default_operation="-")
    assert result is None
    result2 = parse_record_line("   ", default_operation="-")
    assert result2 is None


def test_parse_no_amount():
    """Без суммы → None."""
    result = parse_record_line("просто текст", default_operation="-")
    assert result is None
