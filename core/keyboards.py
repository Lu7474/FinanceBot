"""
Клавиатуры бота: главное меню, выбор периода, типа отчёта и т.д.
"""
from functools import lru_cache

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from core.utils import RU_MONTHS


# Кнопка отмены для inline-клавиатур
CANCEL_BUTTON = InlineKeyboardButton(text="Отмена", callback_data="cancel")


# Главное меню с основными действиями (кэшируется)
@lru_cache(maxsize=1)
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Доход"), KeyboardButton(text="Расход")],
            [KeyboardButton(text="История"), KeyboardButton(text="Отчёт")],
            [KeyboardButton(text="Удалить запись")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


# Inline-клавиатура для выбора периода (день/месяц/год) - для удаления
def delete_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data="del_period:day")],
            [InlineKeyboardButton(text="Месяц", callback_data="del_period:month")],
            [InlineKeyboardButton(text="Год", callback_data="del_period:year")],
            [CANCEL_BUTTON],
        ]
    )


# Inline-клавиатура для выбора периода истории (расширенная)
def history_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="hist_period:day"),
                InlineKeyboardButton(text="Вчера", callback_data="hist_period:yesterday"),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data="hist_period:week"),
                InlineKeyboardButton(text="30 дней", callback_data="hist_period:month30"),
            ],
            [
                InlineKeyboardButton(text="Этот месяц", callback_data="hist_period:month"),
                InlineKeyboardButton(text="Прошлый месяц", callback_data="hist_period:prev_month"),
            ],
            [
                InlineKeyboardButton(text="Этот год", callback_data="hist_period:year"),
                InlineKeyboardButton(text="Свой период", callback_data="hist_period:custom"),
            ],
            [CANCEL_BUTTON],
        ]
    )


# Inline-клавиатура с доступными годами для отчёта
def get_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=str(year), callback_data=f"report_year:{year}")]
        for year in sorted(years)
    ]
    buttons.append([CANCEL_BUTTON])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура с месяцами для выбранного года
def get_months_keyboard(year: int, months: list[int]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=RU_MONTHS[month],
                callback_data=f"report_month:{year}:{month}",
            )
        ]
        for month in sorted(months)
    ]
    buttons.append([CANCEL_BUTTON])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Reply-клавиатура для выбора типа отчёта (доход/расход)
def report_type_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text="Доход"), KeyboardButton(text="Расход")]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# Inline-клавиатура подтверждения удаления
def confirm_delete_keyboard(record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"confirm_del:{record_id}"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel_del"),
            ]
        ]
    )
