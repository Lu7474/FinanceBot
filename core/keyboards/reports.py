"""Keyboards for reports: year/month pickers, report type, weekday report."""

from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.utils import RU_MONTHS

from .common import CANCEL_BUTTON


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
    buttons.append(
        [InlineKeyboardButton(text="← Назад", callback_data="report_back_years")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура для выбора типа отчёта (доход/расход)
@lru_cache(maxsize=1)
def report_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Доход", callback_data="report_type:income"),
                InlineKeyboardButton(
                    text="Расход", callback_data="report_type:expense"
                ),
            ]
        ]
    )


@lru_cache(maxsize=1)
def weekday_report_period_keyboard() -> InlineKeyboardMarkup:
    """Period selection for weekday report."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Этот месяц", callback_data="wd_period:month"
                ),
                InlineKeyboardButton(text="3 месяца", callback_data="wd_period:3m"),
            ],
            [
                InlineKeyboardButton(text="Полгода", callback_data="wd_period:6m"),
                InlineKeyboardButton(text="Год", callback_data="wd_period:year"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="wd_period:back")],
        ]
    )
