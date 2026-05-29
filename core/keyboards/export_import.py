"""Keyboards for export/import: period, type, import confirmation."""

from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .common import CANCEL_BUTTON


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
