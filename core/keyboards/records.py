"""Keyboards for record card and editing: detail, field select, account, delete."""

from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


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


def description_prompt_keyboard(record_id: int) -> InlineKeyboardMarkup:
    """Single button offering to add a description to the just-added record."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Добавить описание",
                    callback_data=f"add_desc:{record_id}",
                )
            ]
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
        [
            InlineKeyboardButton(
                text="Описание",
                callback_data=f"record:field:{record_id}:description",
            )
        ]
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
