"""Keyboards for accounts: menu, selection, manage, delete-move."""

from functools import lru_cache
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


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
