from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def delete_period_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data="del_period:day")],
            [InlineKeyboardButton(text="Месяц", callback_data="del_period:month")],
            [InlineKeyboardButton(text="Год", callback_data="del_period:year")],
        ]
    )
