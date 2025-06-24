import sys
from pathlib import Path
import pytest

# Добавляем путь к корню проекта
sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.keyboards import (
    main_menu_keyboard,
    delete_period_keyboard,
    get_years_keyboard,
    get_months_keyboard,
)
from core.utils import RU_MONTHS


def test_main_menu_keyboard():
    """Тест главного меню"""
    keyboard = main_menu_keyboard()

    # Проверяем структуру
    assert keyboard.keyboard is not None
    assert len(keyboard.keyboard) == 3  # 3 ряда кнопок

    # Проверяем кнопки
    first_row = keyboard.keyboard[0]
    assert len(first_row) == 2
    assert first_row[0].text == "➕ Доход"
    assert first_row[1].text == "➖ Расход"

    second_row = keyboard.keyboard[1]
    assert len(second_row) == 2
    assert second_row[0].text == "🕘 История"
    assert second_row[1].text == "📊 Отчёт"

    third_row = keyboard.keyboard[2]
    assert len(third_row) == 1
    assert third_row[0].text == "🗑️ Удалить запись"

    # Проверяем настройки
    assert keyboard.resize_keyboard is True
    assert keyboard.one_time_keyboard is False


def test_delete_period_keyboard():
    """Тест клавиатуры выбора периода для удаления"""
    keyboard = delete_period_keyboard()

    # Проверяем структуру
    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 3  # 3 кнопки

    # Проверяем кнопки
    buttons = keyboard.inline_keyboard
    assert buttons[0][0].text == "Сегодня"
    assert buttons[0][0].callback_data == "del_period:day"

    assert buttons[1][0].text == "Месяц"
    assert buttons[1][0].callback_data == "del_period:month"

    assert buttons[2][0].text == "Год"
    assert buttons[2][0].callback_data == "del_period:year"


def test_get_years_keyboard():
    """Тест клавиатуры выбора года"""
    years = [2022, 2024, 2023]  # Несортированный список
    keyboard = get_years_keyboard(years)

    # Проверяем структуру
    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 3  # 3 года

    # Проверяем сортировку (должны быть отсортированы)
    buttons = keyboard.inline_keyboard
    assert buttons[0][0].text == "2022"
    assert buttons[0][0].callback_data == "report_year:2022"

    assert buttons[1][0].text == "2023"
    assert buttons[1][0].callback_data == "report_year:2023"

    assert buttons[2][0].text == "2024"
    assert buttons[2][0].callback_data == "report_year:2024"


def test_get_years_keyboard_empty():
    """Тест клавиатуры выбора года с пустым списком"""
    keyboard = get_years_keyboard([])

    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 0


def test_get_months_keyboard():
    """Тест клавиатуры выбора месяца"""
    year = 2024
    months = [3, 1, 12]  # Несортированный список
    keyboard = get_months_keyboard(year, months)

    # Проверяем структуру
    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 3  # 3 месяца

    # Проверяем сортировку и использование RU_MONTHS
    buttons = keyboard.inline_keyboard
    assert buttons[0][0].text == RU_MONTHS[1]  # Январь
    assert buttons[0][0].callback_data == "report_month:2024:1"

    assert buttons[1][0].text == RU_MONTHS[3]  # Март
    assert buttons[1][0].callback_data == "report_month:2024:3"

    assert buttons[2][0].text == RU_MONTHS[12]  # Декабрь
    assert buttons[2][0].callback_data == "report_month:2024:12"


def test_get_months_keyboard_empty():
    """Тест клавиатуры выбора месяца с пустым списком"""
    keyboard = get_months_keyboard(2024, [])

    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 0


def test_get_months_keyboard_all_months():
    """Тест клавиатуры выбора месяца со всеми месяцами"""
    year = 2024
    months = list(range(1, 13))  # Все 12 месяцев
    keyboard = get_months_keyboard(year, months)

    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 12

    # Проверяем, что все месяцы используют правильные названия
    for i, button in enumerate(keyboard.inline_keyboard):
        month_num = i + 1
        assert button[0].text == RU_MONTHS[month_num]
        assert button[0].callback_data == f"report_month:2024:{month_num}"


def test_keyboard_callback_data_format():
    """Тест формата callback_data во всех клавиатурах"""
    # Тест delete_period_keyboard
    delete_kb = delete_period_keyboard()
    for row in delete_kb.inline_keyboard:
        assert row[0].callback_data.startswith("del_period:")

    # Тест get_years_keyboard
    years_kb = get_years_keyboard([2024])
    for row in years_kb.inline_keyboard:
        assert row[0].callback_data.startswith("report_year:")

    # Тест get_months_keyboard
    months_kb = get_months_keyboard(2024, [6])
    for row in months_kb.inline_keyboard:
        assert row[0].callback_data.startswith("report_month:")
        assert ":" in row[0].callback_data  # Должно быть два двоеточия
