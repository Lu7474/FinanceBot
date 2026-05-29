"""Keyboards for categories: menu, select, manage, suggestion."""

from functools import lru_cache
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


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
