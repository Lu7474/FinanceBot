# МЕНЯТЬ ВСЕ. НЕ ИСПОЛЬЗУЕТСЯ


from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

main = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Регистрация")],
        [KeyboardButton(text="Каталог")],
        [KeyboardButton(text="")],
        [KeyboardButton(text="")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите пункт меню...",
)


catalog = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="ТУДА", callback_data="ff")],
        [InlineKeyboardButton(text="СЮДА", callback_data="ss")],
        [InlineKeyboardButton(text="ОТТУДА", callback_data="aa")],
    ]
)


get_number = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отправить номер", request_contact=True)]],
    resize_keyboard=True,
)
