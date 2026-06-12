"""Keyboards for budgets: menu and category selection."""

from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


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
                InlineKeyboardButton(text="📈 Тренд", callback_data="budget_trend"),
            ],
        ]
    )


@lru_cache(maxsize=1)
def budget_trend_keyboard() -> InlineKeyboardMarkup:
    """Back button for the trend view."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Назад", callback_data="budget_back_status")]
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
