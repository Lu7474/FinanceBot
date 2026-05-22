"""
Тесты клавиатур: структура, callback_data, сортировка.
"""

import sys
from datetime import date as _date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.keyboards import (
    acc_back_keyboard,
    account_delete_move_keyboard,
    account_manage_keyboard,
    account_select_keyboard,
    accounts_menu_keyboard,
    budget_category_keyboard,
    budget_menu_keyboard,
    category_manage_keyboard,
    category_select_keyboard,
    category_suggest_keyboard,
    confirm_account_delete_keyboard,
    confirm_delete_keyboard,
    delete_period_keyboard,
    export_period_keyboard,
    export_type_keyboard,
    get_delete_months_keyboard,
    get_delete_years_keyboard,
    get_months_keyboard,
    get_years_keyboard,
    goal_account_keyboard,
    goal_achievement_keyboard,
    goal_archive_list_keyboard,
    goal_confirm_complete_keyboard,
    goal_confirm_delete_keyboard,
    goal_deadline_keyboard,
    goal_detail_keyboard,
    goal_edit_deadline_keyboard,
    goal_edit_menu_keyboard,
    goal_empty_keyboard,
    goal_quick_amounts_keyboard,
    goals_list_keyboard,
    history_category_filter_keyboard,
    history_filter_keyboard,
    history_period_keyboard,
    history_record_select_keyboard,
    import_confirm_keyboard,
    record_account_select_keyboard,
    record_delete_confirm_keyboard,
    record_detail_keyboard,
    record_edit_field_keyboard,
    report_type_keyboard,
    savings_confirm_keyboard,
    savings_items_keyboard,
    savings_view_keyboard,
    search_result_keyboard,
    user_categories_menu_keyboard,
    wealth_back_keyboard,
    wealth_items_keyboard,
    wealth_menu_keyboard,
    wealth_type_keyboard,
    weekday_report_period_keyboard,
)
from core.utils import RU_MONTHS


# Проверяет структуру главного меню (5 рядов, правильные тексты)
def test_main_menu_keyboard():
    from core.keyboards import main_menu_keyboard as _mkb

    keyboard = _mkb()

    # Проверяем структуру
    assert keyboard.keyboard is not None
    assert len(keyboard.keyboard) == 6  # 6 рядов кнопок

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
    assert len(third_row) == 2
    assert third_row[0].text == "Счета"
    assert third_row[1].text == "Удалить запись"

    fourth_row = keyboard.keyboard[3]
    assert len(fourth_row) == 2
    assert fourth_row[0].text == "Накопления"
    assert fourth_row[1].text == "Категории"

    fifth_row = keyboard.keyboard[4]
    assert len(fifth_row) == 2
    assert fifth_row[0].text == "Бюджеты"
    assert fifth_row[1].text == "Экспорт"

    sixth_row = keyboard.keyboard[5]
    assert len(sixth_row) == 2
    assert sixth_row[0].text == "Импорт"
    assert sixth_row[1].text == "Цели"

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

    # Кнопка ← Назад (step-back: с месяцев → к выбору года)
    assert buttons[3][0].text == "← Назад"
    assert buttons[3][0].callback_data == "report_back_years"


# Пустой список месяцев → только кнопка «Назад»
def test_get_months_keyboard_empty():
    keyboard = get_months_keyboard(2024, [])

    assert keyboard.inline_keyboard is not None
    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].text == "← Назад"
    assert keyboard.inline_keyboard[0][0].callback_data == "report_back_years"


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
        assert (
            keyboard.inline_keyboard[i][0].callback_data
            == f"report_month:2024:{month_num}"
        )

    # Кнопка ← Назад
    assert keyboard.inline_keyboard[12][0].text == "← Назад"
    assert keyboard.inline_keyboard[12][0].callback_data == "report_back_years"


# Step-back клавиатура для раздела «Счета»: единственная кнопка «← Назад» → acc_back
def test_acc_back_keyboard():
    keyboard = acc_back_keyboard()

    assert len(keyboard.inline_keyboard) == 1
    assert len(keyboard.inline_keyboard[0]) == 1
    btn = keyboard.inline_keyboard[0][0]
    assert btn.text == "← Назад"
    assert btn.callback_data == "acc_back"


# lru_cache: повторный вызов возвращает тот же объект
def test_acc_back_keyboard_cached():
    assert acc_back_keyboard() is acc_back_keyboard()


# Step-back клавиатура для wealth-wizard'а: единственная кнопка «← Назад» → wealth_back
def test_wealth_back_keyboard():
    keyboard = wealth_back_keyboard()

    assert len(keyboard.inline_keyboard) == 1
    assert len(keyboard.inline_keyboard[0]) == 1
    btn = keyboard.inline_keyboard[0][0]
    assert btn.text == "← Назад"
    assert btn.callback_data == "wealth_back"


def test_wealth_back_keyboard_cached():
    assert wealth_back_keyboard() is wealth_back_keyboard()


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


# ==================== Helpers ====================


def _kb(kb) -> list:
    """Extract rows from InlineKeyboardMarkup or ReplyKeyboardMarkup."""
    return kb.inline_keyboard if hasattr(kb, "inline_keyboard") else kb.keyboard


def _flat(kb) -> list:
    return [btn for row in _kb(kb) for btn in row]


class _FakeAcc:
    def __init__(self, acc_id: int, name: str):
        self.id = acc_id
        self.name = name


class _FakeGoal:
    def __init__(
        self, goal_id: int, name: str, current: float = 0, target: float = 1000
    ):
        self.id = goal_id
        self.name = name
        self.current_amount = current
        self.target_amount = target
        self.deadline = None


class _FakeSavItem:
    def __init__(self, item_id: int, name: str, amount: float, snapshot_id: int = 1):
        from decimal import Decimal

        self.id = item_id
        self.name = name
        self.amount = Decimal(str(amount))
        self.snapshot_id = snapshot_id


class _FakeWealthItem:
    def __init__(self, item_id: int, name: str, amount: float, type_: str):
        from decimal import Decimal

        self.id = item_id
        self.name = name
        self.amount = Decimal(str(amount))
        self.type = type_


class _FakeRec:
    def __init__(
        self,
        rec_id: int,
        operation: str = "-",
        amount: float = 100,
        category: str = "Еда",
    ):
        from datetime import datetime
        from decimal import Decimal

        self.id = rec_id
        self.operation = operation
        self.amount = Decimal(str(amount))
        self.category = category
        self.created_at = datetime(2025, 5, 17, 12, 0, 0)


# ==================== Static keyboards (smoke) ====================


def test_history_period_keyboard():
    kb = history_period_keyboard()
    assert len(kb.inline_keyboard) == 5
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert all(cb.startswith("hist_period:") or cb == "cancel" for cb in callbacks)


def test_report_type_keyboard():
    kb = report_type_keyboard()
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert "Доход" in texts and "Расход" in texts


def test_accounts_menu_keyboard():
    kb = accounts_menu_keyboard()
    assert len(kb.inline_keyboard) == 3
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "acc_create" in callbacks and "acc_rename" in callbacks


def test_wealth_menu_keyboard():
    kb = wealth_menu_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "wealth_add" for btn in callbacks)
    assert any(btn.callback_data == "wealth_delete" for btn in callbacks)


def test_wealth_type_keyboard():
    kb = wealth_type_keyboard()
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "wealth_type:A" in callbacks
    assert "wealth_type:P" in callbacks


def test_budget_menu_keyboard():
    kb = budget_menu_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "budget_add" for btn in callbacks)
    assert any(btn.callback_data == "budget_delete" for btn in callbacks)


def test_user_categories_menu_keyboard():
    kb = user_categories_menu_keyboard()
    assert len(kb.inline_keyboard) == 1
    assert len(kb.inline_keyboard[0]) == 3


def test_weekday_report_period_keyboard():
    kb = weekday_report_period_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "wd_period:month" for btn in callbacks)
    assert any(btn.callback_data == "wd_period:year" for btn in callbacks)


def test_export_period_keyboard():
    kb = export_period_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "export_period:month" for btn in callbacks)
    assert any(btn.callback_data == "export_period:all" for btn in callbacks)


def test_export_type_keyboard():
    kb = export_type_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "export_type:all" for btn in callbacks)
    assert any(btn.callback_data == "export_type:expense" for btn in callbacks)


def test_import_confirm_keyboard():
    kb = import_confirm_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "import_confirm:yes" for btn in callbacks)
    assert any(btn.callback_data == "import_confirm:cancel" for btn in callbacks)


def test_savings_confirm_keyboard():
    kb = savings_confirm_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "sav_confirm_save" for btn in callbacks)
    assert any(btn.callback_data == "sav_cancel_action" for btn in callbacks)


def test_category_suggest_keyboard():
    kb = category_suggest_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "cat_suggest_yes" for btn in callbacks)
    assert any(btn.callback_data == "cat_suggest_manual" for btn in callbacks)


def test_goal_empty_keyboard():
    kb = goal_empty_keyboard()
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].callback_data == "goal:new"


def test_goal_deadline_keyboard():
    kb = goal_deadline_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:no_deadline" for btn in callbacks)
    assert any(btn.callback_data == "goal:cancel" for btn in callbacks)


# ==================== Delete-period keyboards (reverse sort) ====================


def test_get_delete_years_keyboard_reverse_sorted():
    kb = get_delete_years_keyboard([2022, 2024, 2023])
    year_rows = kb.inline_keyboard[:-1]
    texts = [row[0].text for row in year_rows]
    assert texts == ["2024", "2023", "2022"]
    for row in year_rows:
        assert row[0].callback_data.startswith("del_year:")


def test_get_delete_months_keyboard_reverse_sorted():
    kb = get_delete_months_keyboard(2024, [1, 3, 12])
    month_rows = kb.inline_keyboard[:-1]
    callbacks = [row[0].callback_data for row in month_rows]
    assert callbacks == ["del_month:2024:12", "del_month:2024:3", "del_month:2024:1"]


# ==================== Account keyboards ====================


def test_confirm_delete_keyboard():
    kb = confirm_delete_keyboard(42)
    buttons = kb.inline_keyboard[0]
    assert buttons[0].callback_data == "confirm_del:42"
    assert buttons[1].callback_data == "cancel_del"


def test_confirm_account_delete_keyboard():
    kb = confirm_account_delete_keyboard(7)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "acc_delete_confirm:7" for btn in callbacks)
    assert any(btn.callback_data == "acc_delete_cancel" for btn in callbacks)


def test_account_select_keyboard():
    accs = [_FakeAcc(1, "Карта"), _FakeAcc(2, "Наличные")]
    kb = account_select_keyboard(accs)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "acc_select:1" in callbacks
    assert "acc_select:2" in callbacks


def test_account_manage_keyboard_callback_prefix():
    accs = [_FakeAcc(3, "Карта"), _FakeAcc(4, "Вклад")]
    kb = account_manage_keyboard(accs, "rename_select")
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "acc_rename_select:3" in callbacks
    assert "acc_rename_select:4" in callbacks
    assert "acc_back" in callbacks


def test_account_delete_move_keyboard():
    targets = [_FakeAcc(5, "Карта"), _FakeAcc(6, "Вклад")]
    kb = account_delete_move_keyboard(from_id=2, targets=targets)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "acc_delete_move:2:5" in callbacks
    assert "acc_delete_move:2:6" in callbacks
    assert "acc_delete_cancel" in callbacks


# ==================== Savings keyboards ====================


def test_savings_view_keyboard_no_nav_no_snapshot():
    kb = savings_view_keyboard()
    callbacks = _flat(kb)
    assert any(btn.callback_data == "sav_add" for btn in callbacks)
    assert any(btn.callback_data == "sav_wealth" for btn in callbacks)
    assert not any(btn.callback_data.startswith("sav_date:") for btn in callbacks)


def test_savings_view_keyboard_with_nav():
    kb = savings_view_keyboard(prev_date=_date(2025, 4, 1), next_date=_date(2025, 6, 1))
    callbacks = _flat(kb)
    assert any(btn.callback_data == "sav_date:2025-04-01" for btn in callbacks)
    assert any(btn.callback_data == "sav_date:2025-06-01" for btn in callbacks)


def test_savings_view_keyboard_with_snapshot():
    kb = savings_view_keyboard(snapshot_id=99)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "sav_add_field:99" for btn in callbacks)
    assert any(btn.callback_data == "sav_edit:99" for btn in callbacks)
    assert any(btn.callback_data == "sav_delete:99" for btn in callbacks)


def test_savings_items_keyboard_edit():
    items = [_FakeSavItem(1, "Карта", 5000), _FakeSavItem(2, "Кэш", 1000)]
    kb = savings_items_keyboard(items, "edit")
    callbacks = _flat(kb)
    assert any(btn.callback_data == "sav_edit_item:1" for btn in callbacks)
    assert any(btn.callback_data == "sav_edit_item:2" for btn in callbacks)
    assert any(btn.callback_data == "sav_cancel_action" for btn in callbacks)


def test_savings_items_keyboard_delete_adds_all_button():
    items = [_FakeSavItem(1, "Карта", 5000, snapshot_id=7)]
    kb = savings_items_keyboard(items, "delete", snapshot_id=7)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "sav_delete_all:7" for btn in callbacks)


# ==================== Wealth keyboards ====================


def test_wealth_items_keyboard_assets():
    items = [_FakeWealthItem(1, "Квартира", 5_000_000, "A")]
    kb = wealth_items_keyboard(items, "edit")
    callbacks = _flat(kb)
    assert any(btn.callback_data == "wealth_edit_item:1" for btn in callbacks)
    assert any(btn.callback_data == "wealth_to_savings" for btn in callbacks)
    assert any("💚" in btn.text for btn in callbacks)


def test_wealth_items_keyboard_liability_icon():
    items = [_FakeWealthItem(2, "Ипотека", 2_000_000, "P")]
    kb = wealth_items_keyboard(items, "delete")
    assert any("🔴" in btn.text for btn in _flat(kb))


# ==================== Record keyboards ====================


def test_record_detail_keyboard():
    kb = record_detail_keyboard(55)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "record:edit:55" for btn in callbacks)
    assert any(btn.callback_data == "record:delete:55" for btn in callbacks)
    assert any(btn.callback_data == "record:back_history" for btn in callbacks)


def test_record_edit_field_keyboard_with_accounts():
    kb = record_edit_field_keyboard(10, has_accounts=True)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "record:field:10:amount" for btn in callbacks)
    assert any(btn.callback_data == "record:field:10:account" for btn in callbacks)


def test_record_edit_field_keyboard_without_accounts():
    kb = record_edit_field_keyboard(10, has_accounts=False)
    callbacks = _flat(kb)
    assert not any(btn.callback_data == "record:field:10:account" for btn in callbacks)
    assert any(btn.callback_data == "record:field:10:date" for btn in callbacks)


def test_record_account_select_keyboard():
    accs = [_FakeAcc(1, "Карта"), _FakeAcc(2, "Наличные")]
    kb = record_account_select_keyboard(77, accs)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "record:account:77:1" for btn in callbacks)
    assert any(btn.callback_data == "record:account:77:2" for btn in callbacks)


def test_record_delete_confirm_keyboard():
    kb = record_delete_confirm_keyboard(33)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "record:delete_confirm:33" for btn in callbacks)
    assert any(btn.callback_data == "record:view:33" for btn in callbacks)


# ==================== Budget keyboards ====================


def test_budget_category_keyboard_name_based():
    """callback_data должен содержать имя категории."""
    cats = ["Еда", "Транспорт", "Развлечения"]
    kb = budget_category_keyboard(cats)
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "budget_cat:Еда" in callbacks
    assert "budget_cat:Транспорт" in callbacks
    assert "budget_cat:Развлечения" in callbacks
    assert "budget_to_menu" in callbacks


# ==================== Category keyboards ====================


def test_category_select_keyboard_with_other():
    class _Cat:
        def __init__(self, cat_id, name):
            self.id = cat_id
            self.name = name

    cats = [_Cat(1, "Еда"), _Cat(2, "Транспорт")]
    kb = category_select_keyboard(cats, with_other_btn=True)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "cat_select:1" for btn in callbacks)
    assert any(btn.callback_data == "cat_select_other" for btn in callbacks)


def test_category_select_keyboard_without_other():
    class _Cat:
        def __init__(self, cat_id, name):
            self.id = cat_id
            self.name = name

    cats = [_Cat(1, "Еда")]
    kb = category_select_keyboard(cats, with_other_btn=False)
    callbacks = _flat(kb)
    assert not any(btn.callback_data == "cat_select_other" for btn in callbacks)


def test_category_manage_keyboard():
    class _Cat:
        def __init__(self, cat_id, name):
            self.id = cat_id
            self.name = name

    cats = [_Cat(3, "Хобби")]
    kb = category_manage_keyboard(cats, "delete")
    callbacks = _flat(kb)
    assert any(btn.callback_data == "cat_delete:3" for btn in callbacks)
    assert any(btn.callback_data == "cat_manage_back" for btn in callbacks)


# ==================== History keyboards ====================


def test_history_filter_keyboard_no_active():
    kb = history_filter_keyboard()
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "✓ Все" in texts
    assert "Только расходы" in texts


def test_history_filter_keyboard_active_expense():
    kb = history_filter_keyboard(active_operation="-")
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "✓ Расходы" in texts


def test_history_filter_keyboard_active_category():
    kb = history_filter_keyboard(active_category="Еда")
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("● Еда" in t for t in texts)


def test_history_category_filter_keyboard():
    cats = ["Еда", "Транспорт", "Хобби"]
    kb = history_category_filter_keyboard(cats)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "hist_cat_filter:Еда" for btn in callbacks)
    assert any(btn.callback_data == "hist_cat_filter:Транспорт" for btn in callbacks)
    assert any(btn.callback_data == "hist_cat_filter_back" for btn in callbacks)


def test_history_record_select_keyboard():
    recs = [_FakeRec(1, "-", 500, "Еда"), _FakeRec(2, "+", 10000, "Зарплата")]
    kb = history_record_select_keyboard(recs)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "record:view:1" for btn in callbacks)
    assert any(btn.callback_data == "record:view:2" for btn in callbacks)
    assert any(btn.callback_data == "hist_back_from_select" for btn in callbacks)


def test_search_result_keyboard_single_page():
    kb = search_result_keyboard(page=0, total_pages=1)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "search_new" for btn in callbacks)
    assert not any(
        btn.callback_data.startswith("search_page:") and "noop" not in btn.callback_data
        for btn in callbacks
    )


def test_search_result_keyboard_middle_page():
    kb = search_result_keyboard(page=2, total_pages=5)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "search_page:1" for btn in callbacks)
    assert any(btn.callback_data == "search_page:3" for btn in callbacks)
    assert any(btn.callback_data == "search_page:noop" for btn in callbacks)


def test_search_result_keyboard_last_page():
    kb = search_result_keyboard(page=4, total_pages=5)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "search_page:3" for btn in callbacks)
    assert not any(btn.callback_data == "search_page:5" for btn in callbacks)


# ==================== Goals keyboards ====================


def test_goals_list_keyboard_no_archive():
    goals = [_FakeGoal(1, "Машина"), _FakeGoal(2, "Отпуск")]
    kb = goals_list_keyboard(goals, archive_count=0)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:detail:1" for btn in callbacks)
    assert any(btn.callback_data == "goal:detail:2" for btn in callbacks)
    assert any(btn.callback_data == "goal:new" for btn in callbacks)
    assert not any(btn.callback_data == "goal:archive" for btn in callbacks)


def test_goals_list_keyboard_with_archive():
    goals = [_FakeGoal(3, "Дача")]
    kb = goals_list_keyboard(goals, archive_count=5)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:archive" for btn in callbacks)
    assert any("Архив (5)" in btn.text for btn in callbacks)


def test_goals_list_keyboard_long_name_truncated():
    name = "А" * 70
    goals = [_FakeGoal(1, name)]
    kb = goals_list_keyboard(goals)
    btn_text = _flat(kb)[0].text
    assert len(btn_text) <= 64


def test_goal_archive_list_keyboard():
    goals = [_FakeGoal(10, "Завершённая цель")]
    kb = goal_archive_list_keyboard(goals)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:detail:10" for btn in callbacks)
    assert any(btn.callback_data == "goal:list" for btn in callbacks)


def test_goal_detail_keyboard_active():
    kb = goal_detail_keyboard(goal_id=5, is_completed=False)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:deposit:5" for btn in callbacks)
    assert any(btn.callback_data == "goal:withdraw:5" for btn in callbacks)
    assert any(btn.callback_data == "goal:complete:5" for btn in callbacks)
    assert any(btn.callback_data == "goal:delete:5" for btn in callbacks)


def test_goal_detail_keyboard_completed():
    kb = goal_detail_keyboard(goal_id=5, is_completed=True)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:reactivate:5" for btn in callbacks)
    assert not any(btn.callback_data == "goal:deposit:5" for btn in callbacks)


def test_goal_edit_menu_keyboard():
    kb = goal_edit_menu_keyboard(goal_id=8)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:edit_name:8" for btn in callbacks)
    assert any(btn.callback_data == "goal:edit_amount:8" for btn in callbacks)
    assert any(btn.callback_data == "goal:edit_deadline:8" for btn in callbacks)


def test_goal_edit_deadline_keyboard():
    kb = goal_edit_deadline_keyboard(goal_id=9)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:clear_deadline:9" for btn in callbacks)
    assert any(btn.callback_data == "goal:detail:9" for btn in callbacks)


def test_goal_quick_amounts_keyboard_generates_pct_buttons():
    kb = goal_quick_amounts_keyboard(
        goal_id=1, action="qd", remaining=10000, monthly=2000
    )
    callbacks = _flat(kb)
    amounts = [
        btn.callback_data
        for btn in callbacks
        if btn.callback_data.startswith("goal:qd:")
    ]
    assert len(amounts) >= 3


def test_goal_quick_amounts_keyboard_zero_remaining_only_cancel():
    kb = goal_quick_amounts_keyboard(goal_id=1, action="qd", remaining=0)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:detail:1" for btn in callbacks)
    assert not any(btn.callback_data.startswith("goal:qd:1:") for btn in callbacks)


def test_goal_achievement_keyboard():
    kb = goal_achievement_keyboard(goal_id=12)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:complete_confirm:12" for btn in callbacks)
    assert any(btn.callback_data == "goal:list" for btn in callbacks)


def test_goal_confirm_complete_keyboard():
    kb = goal_confirm_complete_keyboard(goal_id=3)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:complete_confirm:3" for btn in callbacks)
    assert any(btn.callback_data == "goal:detail:3" for btn in callbacks)


def test_goal_confirm_delete_keyboard():
    kb = goal_confirm_delete_keyboard(goal_id=4)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:delete_confirm:4" for btn in callbacks)
    assert any(btn.callback_data == "goal:detail:4" for btn in callbacks)


def test_goal_account_keyboard():
    accs = [_FakeAcc(1, "Карта"), _FakeAcc(2, "Наличные")]
    kb = goal_account_keyboard(accs, "deposit_acc", goal_id=7)
    callbacks = _flat(kb)
    assert any(btn.callback_data == "goal:deposit_acc:7:1" for btn in callbacks)
    assert any(btn.callback_data == "goal:deposit_acc:7:2" for btn in callbacks)
    assert any(btn.callback_data == "goal:deposit_acc:7:0" for btn in callbacks)
