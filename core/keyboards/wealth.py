"""Keyboards for wealth items (assets/liabilities): menu, type, item list."""

from functools import lru_cache
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import format_money


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
