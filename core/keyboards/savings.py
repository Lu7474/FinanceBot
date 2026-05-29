"""Keyboards for savings snapshots: view, confirm, item list."""

from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import format_money


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
