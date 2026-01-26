"""
Клавиатуры бота: главное меню, выбор периода, типа отчёта и т.д.
"""
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from core.utils import RU_MONTHS


# Главное меню с основными действиями
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Доход"), KeyboardButton(text="➖ Расход")],
            [KeyboardButton(text="🕘 История"), KeyboardButton(text="📊 Отчёт")],
            [KeyboardButton(text="🗑️ Удалить запись")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


# Inline-клавиатура для выбора периода (день/месяц/год)
def delete_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data="del_period:day")],
            [InlineKeyboardButton(text="Месяц", callback_data="del_period:month")],
            [InlineKeyboardButton(text="Год", callback_data="del_period:year")],
        ]
    )


# Inline-клавиатура с доступными годами для отчёта
def get_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(year), callback_data=f"report_year:{year}")]
            for year in sorted(years)
        ]
    )
    return kb


# Inline-клавиатура с месяцами для выбранного года
def get_months_keyboard(year: int, months: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=RU_MONTHS[month],
                    callback_data=f"report_month:{year}:{month}",
                )
            ]
            for month in sorted(months)
        ]
    )
    return kb


# Reply-клавиатура для выбора типа отчёта (доход/расход)
def report_type_keyboard():
    keyboard = [[KeyboardButton(text="Доход"), KeyboardButton(text="Расход")]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
