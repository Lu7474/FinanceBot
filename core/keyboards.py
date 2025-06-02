from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime


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
    now = datetime.utcnow()
    current_year = now.year
    current_month = now.month

    month_names = {
        1: "Январь",
        2: "Февраль",
        3: "Март",
        4: "Апрель",
        5: "Май",
        6: "Июнь",
        7: "Июль",
        8: "Август",
        9: "Сентябрь",
        10: "Октябрь",
        11: "Ноябрь",
        12: "Декабрь",
    }

    # Фильтруем будущие месяцы
    available_months = [
        month
        for month in sorted(months)
        if year < current_year or (year == current_year and month <= current_month)
    ]

    if not available_months:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Нет доступных месяцев", callback_data="no_months"
                    )
                ]
            ]
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=month_names[month],
                    callback_data=f"report_month:{year}:{month}",
                )
            ]
            for month in available_months
        ]
    )
    return kb
