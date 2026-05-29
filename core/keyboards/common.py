"""Shared keyboard primitives and the main reply menu."""

from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup

# Кнопка отмены для inline-клавиатур
CANCEL_BUTTON = InlineKeyboardButton(text="Отмена", callback_data="cancel")


# Главное меню с основными действиями
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Доход"), KeyboardButton(text="Расход")],
            [KeyboardButton(text="История"), KeyboardButton(text="Отчёт")],
            [KeyboardButton(text="Счета"), KeyboardButton(text="Удалить запись")],
            [KeyboardButton(text="Накопления"), KeyboardButton(text="Категории")],
            [KeyboardButton(text="Бюджеты"), KeyboardButton(text="Экспорт")],
            [KeyboardButton(text="Импорт"), KeyboardButton(text="Цели")],
            [KeyboardButton(text="Долги")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
