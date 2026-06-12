"""Shared helpers, command filters, and FSM state definitions."""

from collections.abc import Sequence

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from core.database.models import async_session
from core.database.requests import add_record, get_user_by_tg_id, set_user


def get_message(cb: CallbackQuery) -> Message:
    """Extract Message from callback; raises AssertionError if inaccessible."""
    assert isinstance(cb.message, Message)
    return cb.message


async def get_user_id_from_event(
    event: Message | CallbackQuery,
    kwargs: dict,
    create_if_missing: bool = False,
) -> int | None:
    """Получает user_id из middleware или БД."""
    user_id = kwargs.get("user_id")
    if user_id:
        return user_id

    tg_id = event.from_user.id if event.from_user else None
    if not tg_id:
        return None

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if user:
            return user.id

        if create_if_missing:
            name = event.from_user.full_name if event.from_user else "Unknown"
            user = await set_user(session, tg_id, name=name)
            if user:
                return user.id

    return None


async def save_parsed_records(
    user_id: int,
    records_to_add: Sequence[tuple],
    account_id: int | None = None,
) -> list[tuple]:
    """Сохраняет распарсенные записи в БД атомарно (все или ни одной).

    Каждый элемент records_to_add — (operation, amount, category, date, description).
    Возвращает добавленные записи в том же 5-элементном формате.
    """
    added_records = []
    async with async_session() as session:
        try:
            for operation, amount, category, record_date, description in records_to_add:
                ok = await add_record(
                    session,
                    user_id,
                    operation,
                    amount,
                    category,
                    record_date,
                    account_id,
                    description,
                )
                if ok:
                    added_records.append(
                        (operation, amount, category, record_date, description)
                    )
            await session.commit()
        except Exception:
            await session.rollback()
            return []
    return added_records


# ==================== Фильтры команд ====================


def is_income(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Доход'."""
    if not message.text:
        return False
    text = (message.text or "").strip().lower()
    return text in ("доход", "➕ доход", "+доход", "+ доход")


def is_expense(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Расход'."""
    if not message.text:
        return False
    text = (message.text or "").strip().lower()
    return text in ("расход", "➖ расход", "-расход", "- расход")


def is_history(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'История'."""
    if not message.text:
        return False
    text = (message.text or "").strip().lower()
    return text in ("история", "🕘 история")


def is_report(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Отчёт'."""
    if not message.text:
        return False
    text = (message.text or "").strip().lower()
    return text in ("отчёт", "отчет", "📊 отчёт", "📊 отчет")


def is_delete(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Удалить запись'."""
    if not message.text:
        return False
    text = (message.text or "").strip().lower()
    return text in ("удалить запись", "удалить", "🗑️ удалить запись", "🗑️ удалить")


def is_accounts(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Счета'."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() in ("счета", "💳 счета")


def is_capital(message: Message) -> bool:
    """Проверяет команду 'Капитал' (вкл. легаси-алиас 'Накопления')."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() in (
        "капитал",
        "💼 капитал",
        "📊 капитал",
        "накопления",
        "💰 накопления",
    )


def is_categories(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Категории'."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "категории"


def is_budgets(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Бюджеты'."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "бюджеты"


def is_export(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Экспорт'."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "экспорт"


def is_import(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Импорт'."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "импорт"


def is_goals(message: Message) -> bool:
    """Checks if message is the 'Цели' menu command."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "цели"


def is_debts(message: Message) -> bool:
    """Checks if message is the 'Долги' menu command."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "долги"


def is_payments(message: Message) -> bool:
    """Checks if message is the 'Платежи' menu command."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "платежи"


def is_family(message: Message) -> bool:
    """Checks if message is the 'Семья' menu command."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() in ("семья", "👨‍👩‍👧 семья")


def is_more(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Ещё' (открыть подменю)."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() in ("ещё", "еще")


def is_settings(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Настройки'."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "настройки"


def is_back(message: Message) -> bool:
    """Проверяет, является ли сообщение командой 'Назад' (вернуться в меню)."""
    if not message.text:
        return False
    return (message.text or "").strip().lower() == "назад"


def is_main_menu_button(message: Message) -> bool:
    """True если сообщение — кнопка главного меню."""
    return any(
        [
            is_income(message),
            is_expense(message),
            is_history(message),
            is_report(message),
            is_delete(message),
            is_accounts(message),
            is_capital(message),
            is_categories(message),
            is_budgets(message),
            is_export(message),
            is_import(message),
            is_goals(message),
            is_debts(message),
            is_payments(message),
            is_family(message),
            is_more(message),
            is_settings(message),
            is_back(message),
        ]
    )


# ==================== FSM States ====================


class AddRecord(StatesGroup):
    """Состояния для добавления записи дохода/расхода."""

    waiting_for_amount = State()
    waiting_for_account = State()
    waiting_for_description = State()  # button-mode: typing description post-save


class BudgetStates(StatesGroup):
    """States for budget management."""

    choosing_action = State()
    choosing_category = State()
    entering_amount = State()


class MenuStates(StatesGroup):
    """Состояния для навигации по меню."""

    waiting_for_history_period = State()
    waiting_for_history_page = State()
    waiting_for_custom_period = State()
    waiting_for_report_year = State()
    waiting_for_report_month = State()
    waiting_for_delete_period = State()
    waiting_for_delete_record = State()
    waiting_for_delete_confirm = State()
    waiting_for_delete_bulk_confirm = State()
    waiting_for_search_query = State()
    waiting_for_search_page = State()
    waiting_for_history_category_filter = State()
    waiting_for_yearly_type = State()
    waiting_for_yearly_year = State()
    waiting_for_yearly_cats = State()
    waiting_for_balance_year = State()
    waiting_for_balance_month = State()


class AccountStates(StatesGroup):
    """Состояния для управления счетами."""

    waiting_for_account_name = State()
    waiting_for_rename_name = State()
    waiting_for_transfer_amount = State()
    waiting_for_set_balance = State()
    waiting_for_acc_hist_period = State()
    waiting_for_acc_hist_page = State()
    waiting_for_transfers_page = State()


class AdminStates(StatesGroup):
    """Состояния для режима администратора."""

    in_admin = State()
    broadcast_text = State()
    dm_text = State()  # ввод текста личного сообщения одному юзеру
    search_query = State()


class CapitalStates(StatesGroup):
    """States for the Capital section: manual item CRUD + snapshot row editing."""

    choosing_type = State()  # add: pick asset/liability
    entering_name = State()  # add: item name
    entering_amount = State()  # add: item amount
    entering_note = State()  # add: optional note
    editing_amount = State()  # edit a manual wealth item amount
    editing_snapshot_amount = State()  # edit a frozen snapshot row amount


class RecordEditStates(StatesGroup):
    """States for viewing and editing individual records."""

    waiting_for_record_edit_value = State()


class CategoryStates(StatesGroup):
    """States for category management and smart category suggestion."""

    choosing_action = State()
    choosing_type_for_add = State()
    entering_name_for_add = State()
    choosing_category_to_rename = State()
    entering_new_name = State()
    confirming_merge = State()
    choosing_category_to_delete = State()
    confirming_delete = State()
    # Record-adding flow
    choosing_category_for_record = State()
    confirming_suggested_category = State()
    entering_category_for_record = State()


class ExportImportStates(StatesGroup):
    """States for export/import workflow."""

    waiting_for_export_period = State()
    waiting_for_export_type = State()
    waiting_for_import_file = State()
    waiting_for_import_confirm = State()


class GoalStates(StatesGroup):
    """States for financial goals workflow."""

    viewing_list = State()
    viewing_detail = State()
    viewing_archive = State()
    entering_name = State()
    entering_amount = State()
    entering_deadline = State()
    choosing_scope = State()
    selecting_deposit_account = State()
    entering_deposit_amount = State()
    entering_deposit_note = State()
    selecting_withdraw_account = State()
    entering_withdraw_amount = State()
    entering_withdraw_note = State()
    editing_name = State()
    editing_amount = State()
    editing_deadline = State()


class DebtStates(StatesGroup):
    """States for debts (loans receivable / payable) workflow."""

    viewing_list = State()
    viewing_detail = State()
    viewing_archive = State()
    waiting_direction = State()
    waiting_person = State()
    waiting_amount = State()
    waiting_description = State()
    waiting_due_date = State()
    waiting_payment_amount = State()
    waiting_payment_note = State()


class PaymentStates(StatesGroup):
    """States for payment-reminder workflow."""

    viewing_list = State()
    viewing_detail = State()
    waiting_title = State()
    waiting_amount = State()
    waiting_due_date = State()
    waiting_period = State()
    waiting_category = State()  # creation: pick expense category
    editing_title = State()
    editing_amount = State()
    editing_due_date = State()
    editing_category = State()  # edit: pick a new category
    waiting_pay_amount = State()  # pay flow: enter actual paid amount
    choosing_pay_account = State()  # pay flow: pick account for the record


class FamilyStates(StatesGroup):
    """States for the family budget workflow."""

    summary = State()
    creating_name = State()  # entering family name on creation
    joining_code = State()  # entering invite code to join
    viewing_history = State()  # paginated shared history
    renaming = State()  # owner entering new family name
