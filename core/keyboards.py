from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

from core.utils import RU_MONTHS


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Доход"), KeyboardButton(text="➖ Расход")],
            [KeyboardButton(text="🕘 История"), KeyboardButton(text="📊 Отчёт")],
            [KeyboardButton(text="🗑️ Удалить запись")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def delete_period_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data="del_period:day")],
            [InlineKeyboardButton(text="Месяц", callback_data="del_period:month")],
            [InlineKeyboardButton(text="Год", callback_data="del_period:year")],
        ]
    )


def get_years_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(year), callback_data=f"report_year:{year}")]
            for year in sorted(years)
        ]
    )
    return kb


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


# создает инлайн-клавиатуру с категориями для быстрого выбора категории при добавлении записи.
# def category_keyboard(categories: list[str]):
#     return InlineKeyboardMarkup(
#         inline_keyboard=[
#             [InlineKeyboardButton(text=cat, callback_data=f"cat:{cat}")]
#             for cat in categories
#         ]
#     )
