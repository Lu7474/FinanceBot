"""
Тесты клавиатур: структура, callback_data, сортировка.
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.keyboards import (
    main_menu_keyboard,
    delete_period_keyboard,
    get_years_keyboard,
    get_months_keyboard,
)
from core.utils import RU_MONTHS


# Проверяет структуру главного меню (3 ряда, правильные тексты)
def test_main_menu_keyboard():
    keyboard = main_menu_keyboard()

    # Проверяем структуру
    assert keyboard.keyboard is not None
    assert len(keyboard.keyboard) == 3  # 3 ряда кнопок

    # Проверяем кнопки
    first_row = keyboard.keyboard[0]
    assert len(first_row) == 2
    assert first_row[0].text == "Доход"
    assert first_row[1].text == "Расход"

    second_row = keyboard.keyboard[1]
    assert len(second_row) == 2
    assert second_row[0].text == "История"
    assert second_row[1].text == "Отчёт"

    third_row = keyboard.keyboard[2]
    assert len(third_row) == 1
    assert third_row[0].text == "Удалить запись"

    # Проверяем настройки
    assert keyboard.resize_keyboard is True
    assert keyboard.one_time_keyboard is False


# Проверяет кнопки выбора периода для удаления
def test_delete_period_keyboard():
    keyboard = delete_period_keyboard()

    # Проверяем структуру: 4 ряда
    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 4

    buttons = keyboard.inline_keyboard

    # Первый ряд: Сегодня, Вчера
    assert len(buttons[0]) == 2
    assert buttons[0][0].text == "Сегодня"
    assert buttons[0][0].callback_data == "del_period:day"
    assert buttons[0][1].text == "Вчера"
    assert buttons[0][1].callback_data == "del_period:yesterday"

    # Второй ряд: Этот месяц, Этот год
    assert len(buttons[1]) == 2
    assert buttons[1][0].text == "Этот месяц"
    assert buttons[1][0].callback_data == "del_period:month"
    assert buttons[1][1].text == "Этот год"
    assert buttons[1][1].callback_data == "del_period:year"

    # Третий ряд: Выбрать месяц
    assert buttons[2][0].text == "Выбрать месяц →"
    assert buttons[2][0].callback_data == "del_select_month"

    # Четвёртый ряд: Отмена
    assert buttons[3][0].text == "Отмена"
    assert buttons[3][0].callback_data == "cancel"


# Проверяет сортировку годов и формат callback_data
def test_get_years_keyboard():
    years = [2022, 2024, 2023]  # Несортированный список
    keyboard = get_years_keyboard(years)

    # Проверяем структуру
    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 4  # 3 года + отмена

    # Проверяем сортировку (должны быть отсортированы)
    buttons = keyboard.inline_keyboard
    assert buttons[0][0].text == "2022"
    assert buttons[0][0].callback_data == "report_year:2022"

    assert buttons[1][0].text == "2023"
    assert buttons[1][0].callback_data == "report_year:2023"

    assert buttons[2][0].text == "2024"
    assert buttons[2][0].callback_data == "report_year:2024"

    # Проверяем кнопку отмены
    assert buttons[3][0].text == "Отмена"
    assert buttons[3][0].callback_data == "cancel"


# Пустой список годов → только кнопка отмены
def test_get_years_keyboard_empty():
    keyboard = get_years_keyboard([])

    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 1  # Только кнопка отмены
    assert keyboard.inline_keyboard[0][0].text == "Отмена"
    assert keyboard.inline_keyboard[0][0].callback_data == "cancel"


# Проверяет сортировку месяцев и русские названия
def test_get_months_keyboard():
    year = 2024
    months = [3, 1, 12]  # Несортированный список
    keyboard = get_months_keyboard(year, months)

    # Проверяем структуру
    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 4  # 3 месяца + отмена

    # Проверяем сортировку и использование RU_MONTHS
    buttons = keyboard.inline_keyboard
    assert buttons[0][0].text == RU_MONTHS[1]  # Январь
    assert buttons[0][0].callback_data == "report_month:2024:1"

    assert buttons[1][0].text == RU_MONTHS[3]  # Март
    assert buttons[1][0].callback_data == "report_month:2024:3"

    assert buttons[2][0].text == RU_MONTHS[12]  # Декабрь
    assert buttons[2][0].callback_data == "report_month:2024:12"

    # Проверяем кнопку отмены
    assert buttons[3][0].text == "Отмена"
    assert buttons[3][0].callback_data == "cancel"


# Пустой список месяцев → только кнопка отмены
def test_get_months_keyboard_empty():
    keyboard = get_months_keyboard(2024, [])

    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 1  # Только кнопка отмены
    assert keyboard.inline_keyboard[0][0].text == "Отмена"
    assert keyboard.inline_keyboard[0][0].callback_data == "cancel"


# Проверяет все 12 месяцев и их названия
def test_get_months_keyboard_all_months():
    year = 2024
    months = list(range(1, 13))  # Все 12 месяцев
    keyboard = get_months_keyboard(year, months)

    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 13  # 12 месяцев + отмена

    # Проверяем, что все месяцы используют правильные названия
    for i in range(12):  # Проверяем только месяцы, не кнопку отмены
        month_num = i + 1
        assert keyboard.inline_keyboard[i][0].text == RU_MONTHS[month_num]
        assert keyboard.inline_keyboard[i][0].callback_data == f"report_month:2024:{month_num}"

    # Проверяем кнопку отмены
    assert keyboard.inline_keyboard[12][0].text == "Отмена"
    assert keyboard.inline_keyboard[12][0].callback_data == "cancel"


# Проверяет префиксы callback_data (del_period, report_year, report_month)
def test_keyboard_callback_data_format():
    # Тест delete_period_keyboard: первые два ряда — del_period:, третий — del_select_month
    delete_kb = delete_period_keyboard()
    # Ряды 0 и 1 содержат кнопки с del_period:
    for row in delete_kb.inline_keyboard[:2]:
        for btn in row:
            assert btn.callback_data.startswith("del_period:")
    # Ряд 2 — кнопка выбора месяца
    assert delete_kb.inline_keyboard[2][0].callback_data == "del_select_month"

    # Тест get_years_keyboard (исключая кнопку отмены)
    years_kb = get_years_keyboard([2024])
    for row in years_kb.inline_keyboard[:-1]:  # Исключаем последнюю кнопку (отмена)
        assert row[0].callback_data.startswith("report_year:")

    # Тест get_months_keyboard (исключая кнопку отмены)
    months_kb = get_months_keyboard(2024, [6])
    for row in months_kb.inline_keyboard[:-1]:  # Исключаем последнюю кнопку (отмена)
        assert row[0].callback_data.startswith("report_month:")
        assert ":" in row[0].callback_data  # Должно быть два двоеточия
