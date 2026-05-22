"""
Клавиатуры бота: главное меню, выбор периода, типа отчёта и т.д.
"""

from functools import lru_cache
from typing import List

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import RU_MONTHS, format_money, goal_emoji, is_goal_overdue

# Кнопка отмены для inline-клавиатур
CANCEL_BUTTON = InlineKeyboardButton(text="Отмена", callback_data="cancel")


# Главное меню с основными действиями
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Доход"), KeyboardButton(text="Расход")],
            [KeyboardButton(text="История"), KeyboardButton(text="Отчёт")],
            [KeyboardButton(text="Счета"), KeyboardButton(text="Удалить запись")],
            [KeyboardButton(text="Накопления"), KeyboardButton(text="Категории")],
            [KeyboardButton(text="Бюджеты"), KeyboardButton(text="Экспорт")],
            [KeyboardButton(text="Импорт"), KeyboardButton(text="Цели")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


# Inline-клавиатура для выбора периода (день/месяц/год) - для удаления
@lru_cache(maxsize=1)
def delete_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="del_period:day"),
                InlineKeyboardButton(
                    text="Вчера", callback_data="del_period:yesterday"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Этот месяц", callback_data="del_period:month"
                ),
                InlineKeyboardButton(text="Этот год", callback_data="del_period:year"),
            ],
            [
                InlineKeyboardButton(
                    text="Выбрать месяц →", callback_data="del_select_month"
                )
            ],
            [CANCEL_BUTTON],
        ]
    )


# Inline-клавиатура для выбора периода истории (расширенная)
@lru_cache(maxsize=1)
def history_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="hist_period:day"),
                InlineKeyboardButton(
                    text="Вчера", callback_data="hist_period:yesterday"
                ),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data="hist_period:week"),
                InlineKeyboardButton(
                    text="30 дней", callback_data="hist_period:month30"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Этот месяц", callback_data="hist_period:month"
                ),
                InlineKeyboardButton(
                    text="Прошлый месяц", callback_data="hist_period:prev_month"
                ),
            ],
            [
                InlineKeyboardButton(text="Этот год", callback_data="hist_period:year"),
                InlineKeyboardButton(
                    text="Свой период", callback_data="hist_period:custom"
                ),
            ],
            [CANCEL_BUTTON],
        ]
    )


# Inline-клавиатура с доступными годами для отчёта
def get_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=str(year), callback_data=f"report_year:{year}")]
        for year in sorted(years)
    ]
    buttons.append([CANCEL_BUTTON])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура с месяцами для выбранного года
def get_months_keyboard(year: int, months: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=RU_MONTHS[month],
                callback_data=f"report_month:{year}:{month}",
            )
        ]
        for month in sorted(months)
    ]
    buttons.append(
        [InlineKeyboardButton(text="← Назад", callback_data="report_back_years")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура с годами для удаления
def get_delete_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=str(year), callback_data=f"del_year:{year}")]
        for year in sorted(years, reverse=True)  # Новые годы сверху
    ]
    buttons.append(
        [InlineKeyboardButton(text="← Назад", callback_data="del_back_to_period")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура с месяцами для удаления
def get_delete_months_keyboard(year: int, months: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=RU_MONTHS[month],
                callback_data=f"del_month:{year}:{month}",
            )
        ]
        for month in sorted(months, reverse=True)  # Новые месяцы сверху
    ]
    buttons.append(
        [InlineKeyboardButton(text="← Назад", callback_data="del_back_to_years")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Reply-клавиатура для выбора типа отчёта (доход/расход)
@lru_cache(maxsize=1)
def report_type_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text="Доход"), KeyboardButton(text="Расход")]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# Inline-клавиатура подтверждения удаления
def confirm_delete_keyboard(record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"confirm_del:{record_id}"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="cancel_del"),
            ]
        ]
    )


# ==================== Счета ====================


# Inline-клавиатура управления счетами (показывается под балансом)
@lru_cache(maxsize=1)
def accounts_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Создать", callback_data="acc_create"),
                InlineKeyboardButton(
                    text="✏️ Переименовать", callback_data="acc_rename"
                ),
            ],
            [
                InlineKeyboardButton(text="🗑️ Удалить", callback_data="acc_delete"),
                InlineKeyboardButton(text="↔️ Перевод", callback_data="acc_transfer"),
            ],
            [
                InlineKeyboardButton(
                    text="💰 Установить баланс", callback_data="acc_set_balance"
                ),
                InlineKeyboardButton(text="📋 История", callback_data="acc_history"),
            ],
        ]
    )


def account_select_keyboard(accounts: List) -> InlineKeyboardMarkup:
    """Keyboard for selecting account when adding a record. Used only when 2+ accounts."""
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(text=acc.name, callback_data=f"acc_select:{acc.id}")
    builder.adjust(2)
    return builder.as_markup()


def account_manage_keyboard(accounts: List, action: str) -> InlineKeyboardMarkup:
    """Keyboard for selecting account for rename/delete/transfer.

    action: 'rename_select' | 'delete_select' | 'transfer_from' | 'transfer_to:{from_id}'
    """
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(text=acc.name, callback_data=f"acc_{action}:{acc.id}")
    builder.button(text="← Назад", callback_data="acc_back")
    builder.adjust(1)
    return builder.as_markup()


@lru_cache(maxsize=1)
def acc_back_keyboard() -> InlineKeyboardMarkup:
    """Single «← Назад» button — returns to accounts main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="acc_back")]
        ]
    )


def confirm_account_delete_keyboard(account_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard for account deletion (no records case)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"acc_delete_confirm:{account_id}"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="acc_delete_cancel"),
            ]
        ]
    )


def account_delete_move_keyboard(from_id: int, targets: List) -> InlineKeyboardMarkup:
    """Shows target accounts to move records before deletion."""
    builder = InlineKeyboardBuilder()
    for acc in targets:
        builder.button(
            text=acc.name, callback_data=f"acc_delete_move:{from_id}:{acc.id}"
        )
    builder.button(text="Отмена", callback_data="acc_delete_cancel")
    builder.adjust(1)
    return builder.as_markup()


# ==================== Накопления ====================


def savings_view_keyboard(
    prev_date=None,
    next_date=None,
    snapshot_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Main savings view keyboard with navigation, actions, and wealth link."""
    rows = []

    nav_btns = []
    if prev_date:
        nav_btns.append(
            InlineKeyboardButton(
                text="◀", callback_data=f"sav_date:{prev_date.isoformat()}"
            )
        )
    if next_date:
        nav_btns.append(
            InlineKeyboardButton(
                text="▶", callback_data=f"sav_date:{next_date.isoformat()}"
            )
        )
    if nav_btns:
        rows.append(nav_btns)

    if snapshot_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить запись", callback_data="sav_add"
                ),
                InlineKeyboardButton(
                    text="➕ Добавить поле",
                    callback_data=f"sav_add_field:{snapshot_id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Изменить", callback_data=f"sav_edit:{snapshot_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"sav_delete:{snapshot_id}"
                ),
            ]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="➕ Добавить запись", callback_data="sav_add")]
        )

    rows.append(
        [
            InlineKeyboardButton(text="💰 Активы/Пассивы", callback_data="sav_wealth"),
            InlineKeyboardButton(text="← Назад", callback_data="sav_back"),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def savings_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm keyboard shown after all amounts are entered before saving."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Сохранить", callback_data="sav_confirm_save"
                ),
                InlineKeyboardButton(
                    text="➕ Добавить поле", callback_data="sav_confirm_add_field"
                ),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="sav_cancel_action")],
        ]
    )


def savings_items_keyboard(
    items: List, action: str, snapshot_id: int | None = None
) -> InlineKeyboardMarkup:
    """Shows savings items as selectable buttons for edit or delete."""
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(
            text=f"{item.name}  —  {format_money(float(item.amount))}",
            callback_data=f"sav_{action}_item:{item.id}",
        )
    if action == "delete":
        sid = (
            snapshot_id
            if snapshot_id is not None
            else (items[0].snapshot_id if items else None)
        )
        if sid is not None:
            builder.button(
                text="🗑 Удалить весь снимок",
                callback_data=f"sav_delete_all:{sid}",
            )
    builder.button(text="← Назад", callback_data="sav_cancel_action")
    builder.adjust(1)
    return builder.as_markup()


# ==================== Активы / Пассивы ====================


@lru_cache(maxsize=1)
def wealth_menu_keyboard() -> InlineKeyboardMarkup:
    """Wealth section main keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить", callback_data="wealth_add"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="wealth_edit"),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить", callback_data="wealth_delete"),
                InlineKeyboardButton(text="← Назад", callback_data="wealth_to_savings"),
            ],
        ]
    )


@lru_cache(maxsize=1)
def wealth_type_keyboard() -> InlineKeyboardMarkup:
    """Select asset (A) or liability (P) type."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💚 Актив", callback_data="wealth_type:A"),
                InlineKeyboardButton(text="🔴 Пассив", callback_data="wealth_type:P"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="wealth_back")],
        ]
    )


@lru_cache(maxsize=1)
def wealth_back_keyboard() -> InlineKeyboardMarkup:
    """Single «← Назад» — returns to wealth balances view."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="wealth_back")]
        ]
    )


def wealth_items_keyboard(items: List, action: str) -> InlineKeyboardMarkup:
    """Shows wealth items as selectable buttons for edit or delete."""
    builder = InlineKeyboardBuilder()
    for item in items:
        icon = "💚" if item.type == "A" else "🔴"
        builder.button(
            text=f"{icon} {item.name}  —  {format_money(float(item.amount))}",
            callback_data=f"wealth_{action}_item:{item.id}",
        )
    builder.button(text="← Назад", callback_data="wealth_to_savings")
    builder.adjust(1)
    return builder.as_markup()


# ==================== Редактирование записей ====================


def record_detail_keyboard(record_id: int) -> InlineKeyboardMarkup:
    """Card keyboard: edit, delete, back to history."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Изменить", callback_data=f"record:edit:{record_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"record:delete:{record_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Назад в историю", callback_data="record:back_history"
                )
            ],
        ]
    )


def record_edit_field_keyboard(
    record_id: int, has_accounts: bool = True
) -> InlineKeyboardMarkup:
    """Field selection keyboard for record editing."""
    rows = [
        [
            InlineKeyboardButton(
                text="Сумму", callback_data=f"record:field:{record_id}:amount"
            ),
            InlineKeyboardButton(
                text="Категорию", callback_data=f"record:field:{record_id}:category"
            ),
        ],
        [
            InlineKeyboardButton(
                text="Дату", callback_data=f"record:field:{record_id}:date"
            ),
        ],
    ]
    if has_accounts:
        rows[1].append(
            InlineKeyboardButton(
                text="Счёт", callback_data=f"record:field:{record_id}:account"
            )
        )
    rows.append(
        [InlineKeyboardButton(text="Отмена", callback_data=f"record:view:{record_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def record_account_select_keyboard(
    record_id: int, accounts: List
) -> InlineKeyboardMarkup:
    """Account selection keyboard for record account editing."""
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(
            text=acc.name, callback_data=f"record:account:{record_id}:{acc.id}"
        )
    builder.button(text="Отмена", callback_data=f"record:view:{record_id}")
    builder.adjust(2)
    return builder.as_markup()


# ==================== Бюджеты ====================


@lru_cache(maxsize=1)
def budget_menu_keyboard() -> InlineKeyboardMarkup:
    """Main budget section keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить", callback_data="budget_add"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="budget_edit"),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить", callback_data="budget_delete"),
            ],
        ]
    )


def budget_category_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    """Shows expense categories as selectable buttons for budget setup."""
    builder = InlineKeyboardBuilder()
    for name in categories:
        builder.button(text=name, callback_data=f"budget_cat:{name}")
    builder.button(text="← Назад", callback_data="budget_to_menu")
    builder.adjust(1)
    return builder.as_markup()


@lru_cache(maxsize=1)
def weekday_report_period_keyboard() -> InlineKeyboardMarkup:
    """Period selection for weekday report."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Этот месяц", callback_data="wd_period:month"
                ),
                InlineKeyboardButton(text="3 месяца", callback_data="wd_period:3m"),
            ],
            [
                InlineKeyboardButton(text="Полгода", callback_data="wd_period:6m"),
                InlineKeyboardButton(text="Год", callback_data="wd_period:year"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="wd_period:back")],
        ]
    )


def history_record_select_keyboard(records: List) -> InlineKeyboardMarkup:
    """Shows current history page records as selectable buttons."""
    builder = InlineKeyboardBuilder()
    for r in records:
        sign = "+" if r.operation == "+" else "-"
        date_str = r.created_at.strftime("%d.%m")
        cat = (r.category or "")[:15]
        if len(r.category or "") > 15:
            cat += "…"
        amount_str = f"{float(r.amount):,.0f}".replace(",", " ")
        builder.button(
            text=f"{date_str} {sign}{amount_str} {cat}",
            callback_data=f"record:view:{r.id}",
        )
    builder.button(text="← Назад", callback_data="hist_back_from_select")
    builder.adjust(1)
    return builder.as_markup()


def record_delete_confirm_keyboard(record_id: int) -> InlineKeyboardMarkup:
    """Confirmation keyboard for deleting a record from its card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=f"record:delete_confirm:{record_id}",
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=f"record:view:{record_id}",
                ),
            ]
        ]
    )


# ==================== Категории ====================


@lru_cache(maxsize=1)
def user_categories_menu_keyboard() -> InlineKeyboardMarkup:
    """Action buttons for categories menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить", callback_data="cat_action:add"
                ),
                InlineKeyboardButton(
                    text="✏️ Переименовать", callback_data="cat_action:rename"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data="cat_action:delete"
                ),
            ]
        ]
    )


def category_select_keyboard(
    categories: List,
    with_other_btn: bool = True,
) -> InlineKeyboardMarkup:
    """Keyboard for selecting category when adding a record (4 per row).

    callback_data format: cat_select:<id>
    """
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(text=cat.name, callback_data=f"cat_select:{cat.id}")
    if with_other_btn:
        builder.button(text="✏️ Другое...", callback_data="cat_select_other")
    builder.adjust(4)
    return builder.as_markup()


def category_manage_keyboard(
    categories: List,
    action: str,
) -> InlineKeyboardMarkup:
    """List of categories for rename/delete selection (one per row).

    callback_data format: cat_<action>:<id>
    """
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(text=cat.name, callback_data=f"cat_{action}:{cat.id}")
    builder.button(text="← Назад", callback_data="cat_manage_back")
    builder.adjust(1)
    return builder.as_markup()


def category_suggest_keyboard() -> InlineKeyboardMarkup:
    """Confirmation keyboard for a suggested category."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data="cat_suggest_yes"),
                InlineKeyboardButton(
                    text="🔄 Другую", callback_data="cat_suggest_other"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Ввести вручную", callback_data="cat_suggest_manual"
                )
            ],
        ]
    )


# ==================== История: фильтры и поиск ====================


def history_filter_keyboard(
    active_operation: str | None = None,
    active_category: str | None = None,
) -> InlineKeyboardMarkup:
    """Filter keyboard: Все / Расходы / Доходы / По категории / Сбросить / Поиск."""
    kb = InlineKeyboardBuilder()
    all_text = "✓ Все" if active_operation is None else "Все"
    expense_text = "✓ Расходы" if active_operation == "-" else "Только расходы"
    income_text = "✓ Доходы" if active_operation == "+" else "Только доходы"
    kb.button(text=all_text, callback_data="hist_filter:all")
    kb.button(text=expense_text, callback_data="hist_filter:expense")
    kb.button(text=income_text, callback_data="hist_filter:income")
    cat_text = f"● {active_category} ▾" if active_category else "По категории ▾"
    kb.button(text=cat_text, callback_data="hist_filter:category")
    kb.button(text="Сбросить", callback_data="hist_filter:reset")
    kb.button(text="🔍 Поиск", callback_data="hist_search:start")
    kb.adjust(3, 3)
    return kb.as_markup()


def history_category_filter_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    """Grid of category buttons (max 15). Each: callback hist_cat_filter:{name}."""
    kb = InlineKeyboardBuilder()
    for cat in categories[:15]:
        kb.button(text=cat, callback_data=f"hist_cat_filter:{cat}")
    kb.button(text="◀ Назад", callback_data="hist_cat_filter_back")
    kb.adjust(2)
    return kb.as_markup()


def search_result_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Pagination + [🔍 Новый поиск] + [◀ К фильтрам]."""
    kb = InlineKeyboardBuilder()
    nav_count = 0
    if total_pages > 1:
        if page > 0:
            kb.button(text="◀ Назад", callback_data=f"search_page:{page - 1}")
            nav_count += 1
        kb.button(text=f"{page + 1}/{total_pages}", callback_data="search_page:noop")
        nav_count += 1
        if page < total_pages - 1:
            kb.button(text="Вперёд ▶", callback_data=f"search_page:{page + 1}")
            nav_count += 1
    kb.button(text="🔍 Новый поиск", callback_data="search_new")
    kb.button(text="◀ К фильтрам", callback_data="search_back")
    row_sizes = ([nav_count] if nav_count > 0 else []) + [2]
    kb.adjust(*row_sizes)
    return kb.as_markup()


@lru_cache(maxsize=1)
def export_period_keyboard() -> InlineKeyboardMarkup:
    """Period selection for export."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Этот месяц", callback_data="export_period:month"
                ),
                InlineKeyboardButton(text="3 месяца", callback_data="export_period:3m"),
            ],
            [
                InlineKeyboardButton(
                    text="Этот год", callback_data="export_period:year"
                ),
                InlineKeyboardButton(
                    text="Всё время", callback_data="export_period:all"
                ),
            ],
            [CANCEL_BUTTON],
        ]
    )


@lru_cache(maxsize=1)
def export_type_keyboard() -> InlineKeyboardMarkup:
    """Data type selection for export."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все записи", callback_data="export_type:all")],
            [
                InlineKeyboardButton(
                    text="Только расходы", callback_data="export_type:expense"
                ),
                InlineKeyboardButton(
                    text="Только доходы", callback_data="export_type:income"
                ),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="export_back_period")],
        ]
    )


@lru_cache(maxsize=1)
def import_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirmation keyboard for import."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, импортировать", callback_data="import_confirm:yes"
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data="import_confirm:cancel"
                ),
            ]
        ]
    )


# ==================== Цели ====================


def goal_empty_keyboard() -> InlineKeyboardMarkup:
    """Empty state: single 'Create first goal' button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Создать первую цель", callback_data="goal:new"
                )
            ]
        ]
    )


def goals_list_keyboard(goals: list, archive_count: int = 0) -> InlineKeyboardMarkup:
    """List of active goals + New goal + optional Archive. No back button (use reply menu)."""
    builder = InlineKeyboardBuilder()
    for goal in goals:
        if goal.current_amount >= goal.target_amount:
            emoji = "✅"
        elif is_goal_overdue(goal):
            emoji = "⚠️"
        else:
            emoji = goal_emoji(goal.name)
        # Усечение для лимита Telegram (64 char на текст кнопки)
        name = goal.name if len(goal.name) <= 55 else goal.name[:54] + "…"
        builder.button(text=f"{emoji} {name}", callback_data=f"goal:detail:{goal.id}")
    builder.button(text="➕ Новая цель", callback_data="goal:new")
    if archive_count > 0:
        builder.button(text=f"📁 Архив ({archive_count})", callback_data="goal:archive")
    # Goals 1-per-row; system buttons share last row (2 if archive shown, else 1)
    rows = [1] * len(goals) + [2 if archive_count > 0 else 1]
    builder.adjust(*rows)
    return builder.as_markup()


def goal_archive_list_keyboard(goals: list) -> InlineKeyboardMarkup:
    """List of completed (archived) goals + Back to active."""
    builder = InlineKeyboardBuilder()
    for goal in goals:
        name = goal.name if len(goal.name) <= 55 else goal.name[:54] + "…"
        builder.button(text=f"✅ {name}", callback_data=f"goal:detail:{goal.id}")
    builder.button(text="← К активным", callback_data="goal:list")
    builder.adjust(1)
    return builder.as_markup()


def goal_detail_keyboard(goal_id: int, is_completed: bool) -> InlineKeyboardMarkup:
    """Goal card actions. For completed goals: reactivate + delete + back."""
    rows = []
    if not is_completed:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💰 Пополнить", callback_data=f"goal:deposit:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="📤 Снять", callback_data=f"goal:withdraw:{goal_id}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать", callback_data=f"goal:edit:{goal_id}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Завершить", callback_data=f"goal:complete:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"goal:delete:{goal_id}"
                ),
            ]
        )
        rows.append([InlineKeyboardButton(text="← Назад", callback_data="goal:list")])
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩️ Переоткрыть", callback_data=f"goal:reactivate:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"goal:delete:{goal_id}"
                ),
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="← В архив", callback_data="goal:archive")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def goal_edit_menu_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Submenu: choose which field to edit."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Имя", callback_data=f"goal:edit_name:{goal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Сумму", callback_data=f"goal:edit_amount:{goal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Дедлайн", callback_data=f"goal:edit_deadline:{goal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад", callback_data=f"goal:detail:{goal_id}"
                )
            ],
        ]
    )


def goal_edit_deadline_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Edit deadline: clear-deadline option + cancel back to detail."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Убрать дедлайн",
                    callback_data=f"goal:clear_deadline:{goal_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"goal:detail:{goal_id}"
                )
            ],
        ]
    )


def goal_quick_amounts_keyboard(
    goal_id: int,
    action: str,
    remaining: float | None,
    monthly: float | None = None,
) -> InlineKeyboardMarkup:
    """Quick amount buttons for deposit (action='qd') or withdraw (action='qw').

    Deposit: 10% / 25% / 50% of remaining + monthly + full remaining.
    Withdraw: 10% / 25% / 50% / 100% of current_amount (passed as remaining).
    """
    builder = InlineKeyboardBuilder()
    if remaining and remaining > 0:
        for pct in (10, 25, 50):
            amount = int(remaining * pct / 100)
            if amount > 0:
                builder.button(
                    text=f"{pct}% ({format_money(amount)})",
                    callback_data=f"goal:{action}:{goal_id}:{amount}",
                )
        if action == "qd" and monthly and monthly > 0:
            m = int(monthly)
            builder.button(
                text=f"Ежемес. ({format_money(m)})",
                callback_data=f"goal:{action}:{goal_id}:{m}",
            )
        full = int(remaining)
        label = "Остаток" if action == "qd" else "Всё"
        builder.button(
            text=f"{label} ({format_money(full)})",
            callback_data=f"goal:{action}:{goal_id}:{full}",
        )
    builder.button(text="Отмена", callback_data=f"goal:detail:{goal_id}")
    builder.adjust(2)
    return builder.as_markup()


def goal_deadline_keyboard() -> InlineKeyboardMarkup:
    """Skip deadline or cancel."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без дедлайна", callback_data="goal:no_deadline"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="goal:cancel"),
            ]
        ]
    )


def goal_achievement_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Nudge shown right after deposit makes goal achieved: close it or keep open."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Завершить цель",
                    callback_data=f"goal:complete_confirm:{goal_id}",
                )
            ],
            [InlineKeyboardButton(text="📋 К списку целей", callback_data="goal:list")],
        ]
    )


def goal_confirm_complete_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Confirmation for goal completion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, завершить",
                    callback_data=f"goal:complete_confirm:{goal_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"goal:detail:{goal_id}"
                ),
            ]
        ]
    )


def goal_confirm_delete_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Confirmation for goal deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"goal:delete_confirm:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"goal:detail:{goal_id}"
                ),
            ]
        ]
    )


def goal_account_keyboard(
    accounts: list, action: str, goal_id: int
) -> InlineKeyboardMarkup:
    """Account selection for deposit/withdraw. action: 'deposit_acc' | 'withdraw_acc'."""
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(text=acc.name, callback_data=f"goal:{action}:{goal_id}:{acc.id}")
    builder.button(text="Пропустить", callback_data=f"goal:{action}:{goal_id}:0")
    builder.adjust(2)
    return builder.as_markup()
