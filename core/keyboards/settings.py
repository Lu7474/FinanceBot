"""Keyboard for the Settings section."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def settings_menu_keyboard(user) -> InlineKeyboardMarkup:
    """Settings menu: description toggle + link to notification settings."""
    flag = "✅" if user.use_description else "❌"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{flag} Описание записей",
                    callback_data="settings:toggle_desc",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔔 Уведомления",
                    callback_data="settings:notifications",
                )
            ],
        ]
    )
