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

from core.utils import RU_MONTHS, format_money

# Кнопка отмены для inline-клавиатур
CANCEL_BUTTON = InlineKeyboardButton(text="Отмена", callback_data="cancel")


# Главное меню с основными действиями (кэшируется)
@lru_cache(maxsize=1)
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Доход"), KeyboardButton(text="Расход")],
            [KeyboardButton(text="История"), KeyboardButton(text="Отчёт")],
            [KeyboardButton(text="Счета"), KeyboardButton(text="Удалить запись")],
            [KeyboardButton(text="Накопления")],
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
                InlineKeyboardButton(text="Вчера", callback_data="del_period:yesterday"),
            ],
            [
                InlineKeyboardButton(text="Этот месяц", callback_data="del_period:month"),
                InlineKeyboardButton(text="Этот год", callback_data="del_period:year"),
            ],
            [InlineKeyboardButton(text="Выбрать месяц →", callback_data="del_select_month")],
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
                InlineKeyboardButton(text="Вчера", callback_data="hist_period:yesterday"),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data="hist_period:week"),
                InlineKeyboardButton(text="30 дней", callback_data="hist_period:month30"),
            ],
            [
                InlineKeyboardButton(text="Этот месяц", callback_data="hist_period:month"),
                InlineKeyboardButton(text="Прошлый месяц", callback_data="hist_period:prev_month"),
            ],
            [
                InlineKeyboardButton(text="Этот год", callback_data="hist_period:year"),
                InlineKeyboardButton(text="Свой период", callback_data="hist_period:custom"),
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
    buttons.append([CANCEL_BUTTON])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура с годами для удаления
def get_delete_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=str(year), callback_data=f"del_year:{year}")]
        for year in sorted(years, reverse=True)  # Новые годы сверху
    ]
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="del_back_to_period")])
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
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="del_back_to_years")])
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
                InlineKeyboardButton(text="Да, удалить", callback_data=f"confirm_del:{record_id}"),
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
                InlineKeyboardButton(text="✏️ Переименовать", callback_data="acc_rename"),
            ],
            [
                InlineKeyboardButton(text="🗑️ Удалить", callback_data="acc_delete"),
                InlineKeyboardButton(text="↔️ Перевод", callback_data="acc_transfer"),
            ],
            [
                InlineKeyboardButton(text="💰 Установить баланс", callback_data="acc_set_balance"),
                InlineKeyboardButton(text="📋 История", callback_data="acc_history"),
            ],
        ]
    )


def account_select_keyboard(accounts: List) -> InlineKeyboardMarkup:
    """Keyboard for selecting account when adding a record (includes 'Skip')."""
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(text=acc.name, callback_data=f"acc_select:{acc.id}")
    builder.button(text="Пропустить", callback_data="acc_skip")
    builder.adjust(2)
    return builder.as_markup()


def account_manage_keyboard(accounts: List, action: str) -> InlineKeyboardMarkup:
    """Keyboard for selecting account for rename/delete/transfer.

    action: 'rename_select' | 'delete_select' | 'transfer_from' | 'transfer_to:{from_id}'
    """
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(text=acc.name, callback_data=f"acc_{action}:{acc.id}")
    builder.button(text="Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()


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
        builder.button(text=acc.name, callback_data=f"acc_delete_move:{from_id}:{acc.id}")
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
        nav_btns.append(InlineKeyboardButton(text="◀", callback_data=f"sav_date:{prev_date.isoformat()}"))
    if next_date:
        nav_btns.append(InlineKeyboardButton(text="▶", callback_data=f"sav_date:{next_date.isoformat()}"))
    if nav_btns:
        rows.append(nav_btns)

    if snapshot_id is not None:
        rows.append([
            InlineKeyboardButton(text="➕ Добавить запись", callback_data="sav_add"),
            InlineKeyboardButton(text="➕ Добавить поле", callback_data=f"sav_add_field:{snapshot_id}"),
        ])
        rows.append([
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"sav_edit:{snapshot_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"sav_delete:{snapshot_id}"),
        ])
    else:
        rows.append([InlineKeyboardButton(text="➕ Добавить запись", callback_data="sav_add")])

    rows.append([
        InlineKeyboardButton(text="💰 Активы/Пассивы", callback_data="sav_wealth"),
        InlineKeyboardButton(text="← Назад", callback_data="sav_back"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def savings_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirm keyboard shown after all amounts are entered before saving."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Сохранить", callback_data="sav_confirm_save"),
            InlineKeyboardButton(text="➕ Добавить поле", callback_data="sav_confirm_add_field"),
        ],
        [InlineKeyboardButton(text="Отмена", callback_data="sav_cancel_action")],
    ])


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
        sid = snapshot_id if snapshot_id is not None else (items[0].snapshot_id if items else None)
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
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="wealth_add"),
            InlineKeyboardButton(text="✏️ Изменить", callback_data="wealth_edit"),
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data="wealth_delete"),
            InlineKeyboardButton(text="← Назад", callback_data="wealth_back"),
        ],
    ])


@lru_cache(maxsize=1)
def wealth_type_keyboard() -> InlineKeyboardMarkup:
    """Select asset (A) or liability (P) type."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💚 Актив", callback_data="wealth_type:A"),
            InlineKeyboardButton(text="🔴 Пассив", callback_data="wealth_type:P"),
        ],
        [CANCEL_BUTTON],
    ])


def wealth_items_keyboard(items: List, action: str) -> InlineKeyboardMarkup:
    """Shows wealth items as selectable buttons for edit or delete."""
    builder = InlineKeyboardBuilder()
    for item in items:
        icon = "💚" if item.type == "A" else "🔴"
        builder.button(
            text=f"{icon} {item.name}  —  {format_money(float(item.amount))}",
            callback_data=f"wealth_{action}_item:{item.id}",
        )
    builder.button(text="← Назад", callback_data="wealth_back")
    builder.adjust(1)
    return builder.as_markup()
