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
            [
                InlineKeyboardButton(text="🔁 Переводы", callback_data="acc_transfers"),
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


def transfers_list_keyboard(
    transfers: List[dict], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """List of transfer pairs (one button per pair) + pagination + back."""
    builder = InlineKeyboardBuilder()
    row_sizes: list[int] = []
    for t in transfers:
        date_str = t["date"].strftime("%d.%m")
        amount_str = f"{t['amount']:,.0f}₽".replace(",", " ")
        # Trim names so the button label stays readable on narrow clients.
        from_name = (t["from_name"] or "(удалён)")[:15]
        to_name = (t["to_name"] or "(удалён)")[:15]
        text = f"{date_str} │ {from_name} → {to_name} │ {amount_str}"
        builder.button(text=text, callback_data=f"acc_tr_view:{t['transfer_id']}")
        row_sizes.append(1)

    if total_pages > 1:
        nav = 0
        if page > 0:
            builder.button(text="◀ Назад", callback_data=f"acc_tr_page:{page - 1}")
            nav += 1
        builder.button(
            text=f"{page + 1}/{total_pages}", callback_data="acc_tr_page:noop"
        )
        nav += 1
        if page < total_pages - 1:
            builder.button(text="Вперёд ▶", callback_data=f"acc_tr_page:{page + 1}")
            nav += 1
        row_sizes.append(nav)

    builder.button(text="← Назад", callback_data="acc_back")
    row_sizes.append(1)
    builder.adjust(*row_sizes)
    return builder.as_markup()


def transfer_card_keyboard(transfer_id: int) -> InlineKeyboardMarkup:
    """Single transfer card: cancel + back to list."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Отменить перевод",
                    callback_data=f"acc_tr_cancel:{transfer_id}",
                ),
            ],
            [InlineKeyboardButton(text="← К списку", callback_data="acc_transfers")],
        ]
    )


def confirm_transfer_cancel_keyboard(transfer_id: int) -> InlineKeyboardMarkup:
    """Confirmation for transfer cancellation."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отменить",
                    callback_data=f"acc_tr_del:{transfer_id}",
                ),
                InlineKeyboardButton(
                    text="Нет", callback_data=f"acc_tr_view:{transfer_id}"
                ),
            ]
        ]
    )
