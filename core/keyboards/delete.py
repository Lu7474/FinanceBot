"""Keyboards for the record-deletion flow (period / year / month / confirm)."""

from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.utils import RU_MONTHS

from .common import CANCEL_BUTTON


# Inline-клавиатура для выбора периода (день/месяц/год) - для удаления
@lru_cache(maxsize=1)
def delete_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="del_period:day"),
                InlineKeyboardButton(
                    text="Вчера", callback_data="del_period:yesterday"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Этот месяц", callback_data="del_period:month"
                ),
                InlineKeyboardButton(text="Этот год", callback_data="del_period:year"),
            ],
            [
                InlineKeyboardButton(
                    text="🗂 За всё время", callback_data="del_period:all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Выбрать месяц →", callback_data="del_select_month"
                )
            ],
            [CANCEL_BUTTON],
        ]
    )


# Inline-клавиатура с годами для удаления
def get_delete_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=str(year), callback_data=f"del_year:{year}")]
        for year in sorted(years, reverse=True)  # Новые годы сверху
    ]
    buttons.append(
        [InlineKeyboardButton(text="← Назад", callback_data="del_back_to_period")]
    )
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
    buttons.append(
        [InlineKeyboardButton(text="← Назад", callback_data="del_back_to_years")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура подтверждения удаления
def confirm_delete_keyboard(record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"confirm_del:{record_id}"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="cancel_del"),
            ]
        ]
    )
