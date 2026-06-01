"""Keyboard for the Settings section."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Description input modes shown as a radio list (active one marked).
DESCRIPTION_MODES: list[tuple[str, str]] = [
    ("off", "Выключено"),
    ("brackets", "В скобках: 5000 еда (обед)"),
    ("button", "Кнопкой после записи"),
    ("auto", "Авто: категория + описание"),
]


def settings_menu_keyboard(user) -> InlineKeyboardMarkup:
    """Settings menu: description-mode radio list + link to notification settings."""
    current = user.description_mode or "off"
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if mode == current else '⚪'} {label}",
                callback_data=f"settings:mode:{mode}",
            )
        ]
        for mode, label in DESCRIPTION_MODES
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="🔔 Уведомления",
                callback_data="settings:notifications",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
