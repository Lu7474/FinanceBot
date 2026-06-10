"""Keyboards for notification settings and onboarding."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def notification_settings_keyboard(
    user, back_to_settings: bool = False
) -> InlineKeyboardMarkup:
    """Toggle keyboard for notification settings based on current user flags.

    When opened from the Settings section, `back_to_settings` adds a row that
    navigates back to the settings menu.
    """

    def flag(v: bool) -> str:
        return "✅" if v else "❌"

    rows = [
        [
            InlineKeyboardButton(
                text=f"{flag(user.notify_weekly)} Еженедельная сводка (вс, 20:00)",
                callback_data="notify_toggle:weekly",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{flag(user.notify_monthly)} Ежемесячная сводка (конец месяца)",
                callback_data="notify_toggle:monthly",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{flag(user.notify_daily)} Ежедневные итоги (21:00)",
                callback_data="notify_toggle:daily",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{flag(user.notify_reminder)} Напоминание о записи (2 дня)",
                callback_data="notify_toggle:reminder",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{flag(user.notify_debts)} Напоминания о долгах (10:00)",
                callback_data="notify_toggle:debts",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{flag(user.notify_payments)} Напоминания о платежах (9:00)",
                callback_data="notify_toggle:payments",
            )
        ],
    ]
    if back_to_settings:
        rows.append(
            [
                InlineKeyboardButton(
                    text="‹ Назад к настройкам",
                    callback_data="settings:back",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notify_onboarding_keyboard() -> InlineKeyboardMarkup:
    """Two-button onboarding prompt: enable all or skip."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Включить уведомления",
                    callback_data="notify_enable_all",
                ),
                InlineKeyboardButton(
                    text="Нет, спасибо",
                    callback_data="notify_skip",
                ),
            ]
        ]
    )
