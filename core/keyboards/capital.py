"""Keyboards for the Capital section: live view, snapshot history, item lists."""

from functools import lru_cache
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import format_money


@lru_cache(maxsize=2)
def capital_menu_keyboard(has_manual: bool = False) -> InlineKeyboardMarkup:
    """Main live capital view: snapshot, history, CRUD of manual items.

    ✏️/🗑 are shown only when there are manual WealthItems to act on
    (virtual rows from accounts/debts are read-only).
    """
    rows = [
        [
            InlineKeyboardButton(text="📸 Снимок", callback_data="cap_snapshot"),
            InlineKeyboardButton(text="🕘 История", callback_data="cap_history"),
        ]
    ]
    if has_manual:
        rows.append(
            [
                InlineKeyboardButton(text="➕ Добавить", callback_data="cap_add"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="cap_wealth_edit"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data="cap_wealth_delete"),
            ]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="➕ Добавить", callback_data="cap_add")]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="cap_close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@lru_cache(maxsize=1)
def capital_type_keyboard() -> InlineKeyboardMarkup:
    """Select asset (A) or liability (P) when adding a manual item."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💚 Актив", callback_data="cap_type:A"),
                InlineKeyboardButton(text="🔴 Пассив", callback_data="cap_type:P"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="cap_back")],
        ]
    )


@lru_cache(maxsize=1)
def capital_back_keyboard() -> InlineKeyboardMarkup:
    """Single «← Назад» — cancels the current wizard step, returns to capital view."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="cap_back")]
        ]
    )


def capital_snapshot_back_keyboard(snapshot_id: int) -> InlineKeyboardMarkup:
    """Single «← Назад» that returns to the snapshot view (not the live capital)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data=f"cap_snap:{snapshot_id}")]
        ]
    )


@lru_cache(maxsize=1)
def capital_confirm_snapshot_keyboard() -> InlineKeyboardMarkup:
    """Overwrite confirmation when a snapshot for today already exists."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Перезаписать", callback_data="cap_snapshot_confirm"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="cap_back"),
            ],
        ]
    )


def capital_wealth_items_keyboard(items: List, action: str) -> InlineKeyboardMarkup:
    """Manual wealth items as selectable buttons for edit or delete."""
    builder = InlineKeyboardBuilder()
    for item in items:
        icon = "💚" if item.type == "A" else "🔴"
        builder.button(
            text=f"{icon} {item.name}  —  {format_money(float(item.amount))}",
            callback_data=f"cap_wealth_{action}_item:{item.id}",
        )
    builder.button(text="← Назад", callback_data="cap_back")
    builder.adjust(1)
    return builder.as_markup()


def capital_history_keyboard(
    prev_date=None,
    next_date=None,
    snapshot_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Snapshot history view: date nav, per-row edit/delete, back to capital."""
    rows = []

    nav_btns = []
    if prev_date:
        nav_btns.append(
            InlineKeyboardButton(
                text="◀", callback_data=f"cap_date:{prev_date.isoformat()}"
            )
        )
    if next_date:
        nav_btns.append(
            InlineKeyboardButton(
                text="▶", callback_data=f"cap_date:{next_date.isoformat()}"
            )
        )
    if nav_btns:
        rows.append(nav_btns)

    if snapshot_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Изменить строку", callback_data=f"cap_edit:{snapshot_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"cap_delete:{snapshot_id}"
                ),
            ]
        )

    rows.append(
        [InlineKeyboardButton(text="← К капиталу", callback_data="cap_to_capital")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def capital_snapshot_items_keyboard(
    items: List, action: str, snapshot_id: int
) -> InlineKeyboardMarkup:
    """Snapshot rows as selectable buttons for edit or delete (+ delete whole snapshot)."""
    builder = InlineKeyboardBuilder()
    for item in items:
        icon = "💚" if item.type == "A" else "🔴"
        builder.button(
            text=f"{icon} {item.name}  —  {format_money(float(item.amount))}",
            callback_data=f"cap_{action}_item:{item.id}",
        )
    if action == "delete":
        builder.button(
            text="🗑 Удалить весь снимок",
            callback_data=f"cap_delete_all:{snapshot_id}",
        )
    builder.button(text="← Назад", callback_data=f"cap_snap:{snapshot_id}")
    builder.adjust(1)
    return builder.as_markup()
