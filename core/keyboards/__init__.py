"""Bot keyboards, organized by feature.

Re-exported here so existing call sites keep using `from core.keyboards import X`.
"""

from .accounts import (
    acc_back_keyboard,
    account_delete_move_keyboard,
    account_manage_keyboard,
    account_select_keyboard,
    accounts_menu_keyboard,
    confirm_account_delete_keyboard,
)
from .budgets import budget_category_keyboard, budget_menu_keyboard
from .categories import (
    category_manage_keyboard,
    category_select_keyboard,
    category_suggest_keyboard,
    user_categories_menu_keyboard,
)
from .common import CANCEL_BUTTON, main_menu_keyboard
from .debts import (
    debt_archive_keyboard,
    debt_delete_confirm_keyboard,
    debt_detail_keyboard,
    debt_direction_keyboard,
    debt_due_date_keyboard,
    debt_reminder_open_keyboard,
    debt_select_keyboard,
    debt_skip_keyboard,
    debts_menu_keyboard,
)
from .delete import (
    confirm_delete_keyboard,
    delete_period_keyboard,
    get_delete_months_keyboard,
    get_delete_years_keyboard,
)
from .export_import import (
    export_period_keyboard,
    export_type_keyboard,
    import_confirm_keyboard,
)
from .goals import (
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
)
from .history import (
    history_category_filter_keyboard,
    history_filter_keyboard,
    history_period_keyboard,
    history_record_select_keyboard,
    search_result_keyboard,
)
from .notifications import notification_settings_keyboard, notify_onboarding_keyboard
from .records import (
    record_account_select_keyboard,
    record_delete_confirm_keyboard,
    record_detail_keyboard,
    record_edit_field_keyboard,
)
from .reports import (
    chart_period_keyboard,
    get_months_keyboard,
    get_years_keyboard,
    report_section_keyboard,
    report_type_keyboard,
    stacked_period_keyboard,
    stacked_type_keyboard,
    weekday_report_period_keyboard,
)
from .savings import (
    savings_confirm_keyboard,
    savings_items_keyboard,
    savings_view_keyboard,
)
from .wealth import (
    wealth_back_keyboard,
    wealth_items_keyboard,
    wealth_menu_keyboard,
    wealth_type_keyboard,
)

__all__ = [
    "CANCEL_BUTTON",
    "acc_back_keyboard",
    "account_delete_move_keyboard",
    "account_manage_keyboard",
    "account_select_keyboard",
    "accounts_menu_keyboard",
    "budget_category_keyboard",
    "budget_menu_keyboard",
    "category_manage_keyboard",
    "category_select_keyboard",
    "category_suggest_keyboard",
    "chart_period_keyboard",
    "confirm_account_delete_keyboard",
    "confirm_delete_keyboard",
    "debt_archive_keyboard",
    "debt_delete_confirm_keyboard",
    "debt_detail_keyboard",
    "debt_direction_keyboard",
    "debt_due_date_keyboard",
    "debt_reminder_open_keyboard",
    "debt_select_keyboard",
    "debt_skip_keyboard",
    "debts_menu_keyboard",
    "delete_period_keyboard",
    "export_period_keyboard",
    "export_type_keyboard",
    "get_delete_months_keyboard",
    "get_delete_years_keyboard",
    "get_months_keyboard",
    "get_years_keyboard",
    "goal_account_keyboard",
    "goal_achievement_keyboard",
    "goal_archive_list_keyboard",
    "goal_confirm_complete_keyboard",
    "goal_confirm_delete_keyboard",
    "goal_deadline_keyboard",
    "goal_detail_keyboard",
    "goal_edit_deadline_keyboard",
    "goal_edit_menu_keyboard",
    "goal_empty_keyboard",
    "goal_quick_amounts_keyboard",
    "goals_list_keyboard",
    "history_category_filter_keyboard",
    "history_filter_keyboard",
    "history_period_keyboard",
    "history_record_select_keyboard",
    "import_confirm_keyboard",
    "main_menu_keyboard",
    "notification_settings_keyboard",
    "notify_onboarding_keyboard",
    "record_account_select_keyboard",
    "record_delete_confirm_keyboard",
    "record_detail_keyboard",
    "record_edit_field_keyboard",
    "report_section_keyboard",
    "report_type_keyboard",
    "savings_confirm_keyboard",
    "savings_items_keyboard",
    "savings_view_keyboard",
    "search_result_keyboard",
    "stacked_period_keyboard",
    "stacked_type_keyboard",
    "user_categories_menu_keyboard",
    "wealth_back_keyboard",
    "wealth_items_keyboard",
    "wealth_menu_keyboard",
    "wealth_type_keyboard",
    "weekday_report_period_keyboard",
]
