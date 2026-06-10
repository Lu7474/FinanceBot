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
            [KeyboardButton(text="Ещё")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


# Подменю «Ещё» — второй экран reply-клавиатуры со второстепенными разделами
def more_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Накопления"), KeyboardButton(text="Категории")],
            [KeyboardButton(text="Бюджеты"), KeyboardButton(text="Цели")],
            [KeyboardButton(text="Долги"), KeyboardButton(text="Платежи")],
            [KeyboardButton(text="Семья"), KeyboardButton(text="Настройки")],
            [KeyboardButton(text="Экспорт"), KeyboardButton(text="Импорт")],
            [KeyboardButton(text="Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
