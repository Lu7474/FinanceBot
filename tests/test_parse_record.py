"""
Тесты парсинга записей: суммы, категории, даты.
"""

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.handlers import parse_record_line

# ==================== Базовый парсинг ====================


def test_parse_simple_record():
    """Парсинг простой записи: сумма + категория."""
    result = parse_record_line("500 еда", default_operation="-")
    assert result is not None
    op, amount, category, date, _desc = result
    assert op == "-"
    assert amount == Decimal("500")
    assert category == "Еда"  # Нормализация: первая заглавная
    assert date is None


def test_parse_record_with_sign():
    """Парсинг записи со знаком операции."""
    result = parse_record_line("+1000 зарплата")
    assert result is not None
    op, amount, category, date, _desc = result
    assert op == "+"
    assert amount == Decimal("1000")
    assert category == "Зарплата"  # Нормализация: первая заглавная

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
    op, amount, category, date, _desc = result
    assert op == "-"
    assert amount == Decimal("500")
    assert category == "Продукты"  # Нормализация
    assert date is not None
    assert date.day == 27
    assert date.month == 1
    assert date.year == datetime.now().year


def test_parse_record_with_date_ddmmyy():
    """Парсинг записи с датой ДД.ММ.ГГ."""
    result = parse_record_line("15.12.25 1000 подарок", default_operation="+")
    assert result is not None
    op, amount, category, date, _desc = result
    assert op == "+"
    assert amount == Decimal("1000")
    assert category == "Подарок"  # Нормализация
    assert date is not None
    assert date.day == 15
    assert date.month == 12
    assert date.year == 2025


def test_parse_record_with_date_and_sign():
    """Парсинг записи с датой и знаком операции."""
    result = parse_record_line("20.01.26 +5000 зарплата")
    assert result is not None
    op, amount, category, date, _desc = result
    assert op == "+"
    assert amount == Decimal("5000")
    assert category == "Зарплата"  # Нормализация
    assert date.day == 20
    assert date.month == 1
    assert date.year == 2026

    result2 = parse_record_line("20.01 -350 магазин")
    assert result2 is not None
    assert result2[0] == "-"
    assert result2[1] == Decimal("350")
    assert result2[2] == "Магазин"  # Нормализация


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
    Запись отклоняется полностью (возвращается None)."""
    result = parse_record_line("31.02 500 тест", default_operation="-")
    assert result is None  # Невалидная дата — запись отклоняется


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


# ==================== Режимы описания ====================


def test_mode_off_no_description():
    """Режим off (по умолчанию): описание не выделяется, категория многословная."""
    result = parse_record_line("500 обед с клиентом", default_operation="-")
    assert result is not None
    assert result.category == "Обед с клиентом"
    assert result.description is None


def test_mode_brackets():
    """Режим brackets: хвост в скобках → описание, до скобок → категория."""
    result = parse_record_line("500 еда (обед с клиентом)", "-", mode="brackets")
    assert result.category == "Еда"
    assert result.description == "обед с клиентом"


def test_mode_brackets_no_parens():
    """Режим brackets без скобок: вся строка — категория, описания нет."""
    result = parse_record_line("500 еда", "-", mode="brackets")
    assert result.category == "Еда"
    assert result.description is None


def test_mode_auto_known_category():
    """Режим auto: greedy-match по заведённой многословной категории."""
    cats = ["Магазин А", "Еда"]
    result = parse_record_line(
        "500 Магазин А у дома", "-", mode="auto", user_categories=cats
    )
    assert result.category == "Магазин А"
    assert result.description == "у дома"


def test_mode_auto_fallback_first_word():
    """Режим auto без совпадения: первое слово — категория, остальное — описание."""
    result = parse_record_line(
        "500 такси аэропорт", "-", mode="auto", user_categories=["Еда"]
    )
    assert result.category == "Такси"
    assert result.description == "аэропорт"


def test_mode_button_like_off():
    """Режим button парсит как off (описание добавляется постфактум кнопкой)."""
    result = parse_record_line("500 такси домой", "-", mode="button")
    assert result.category == "Такси домой"
    assert result.description is None
