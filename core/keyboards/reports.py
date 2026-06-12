"""Keyboards for reports: year/month pickers, report type."""

from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.utils import RU_MONTHS

from .common import CANCEL_BUTTON


# Inline-клавиатура с доступными годами для отчёта.
# prefix разделяет флоу: "report" → report_year:, "bal" → bal_year: и т.д.
def get_years_keyboard(
    years: list[int], prefix: str = "report"
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=str(year), callback_data=f"{prefix}_year:{year}")]
        for year in sorted(years)
    ]
    buttons.append([CANCEL_BUTTON])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура с месяцами для выбранного года
def get_months_keyboard(
    year: int, months: list[int], prefix: str = "report"
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=RU_MONTHS[month],
                callback_data=f"{prefix}_month:{year}:{month}",
            )
        ]
        for month in sorted(months)
    ]
    buttons.append(
        [InlineKeyboardButton(text="← Назад", callback_data=f"{prefix}_back_years")]
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
def report_section_keyboard() -> InlineKeyboardMarkup:
    """Report-type submenu shown right after pressing «Отчёт»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 По категориям",
                    callback_data="report_section:categories",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📈 Структура по месяцам",
                    callback_data="report_section:structure",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Годовой отчёт",
                    callback_data="report_section:yearly",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Динамика баланса",
                    callback_data="report_section:balance",
                )
            ],
            [CANCEL_BUTTON],
        ]
    )


@lru_cache(maxsize=1)
def yearly_report_type_keyboard() -> InlineKeyboardMarkup:
    """Income/expense selection for the yearly report."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Расходы", callback_data="yr_type:expense"),
                InlineKeyboardButton(text="Доходы", callback_data="yr_type:income"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="report_section_back")],
        ]
    )


def yearly_report_year_keyboard(years: list[int]) -> InlineKeyboardMarkup:
    """Year picker for the yearly report + «За всё время»."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=str(year), callback_data=f"yr_year:{year}")]
        for year in sorted(years)
    ]
    rows.append(
        [InlineKeyboardButton(text="📊 За всё время", callback_data="yr_year:all")]
    )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="yr_back_type")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yearly_report_cats_keyboard(
    cats: list[str], selected: set[int]
) -> InlineKeyboardMarkup:
    """Multi-select toggle keyboard for categories (2 per row).

    callback_data carries the category INDEX (not the name) to stay within the
    64-byte limit — Cyrillic names would overflow. cats list lives in FSMContext.
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, name in enumerate(cats):
        short = name if len(name) <= 22 else name[:21] + "…"
        label = f"✅ {short}" if i in selected else short
        row.append(InlineKeyboardButton(text=label, callback_data=f"yr_cat:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Готово ✓", callback_data="yr_done")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="yr_back_year")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@lru_cache(maxsize=1)
def stacked_type_keyboard() -> InlineKeyboardMarkup:
    """Income/expense selection for the stacked (structure) report."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Доход", callback_data="stacked_type:income"),
                InlineKeyboardButton(
                    text="Расход", callback_data="stacked_type:expense"
                ),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="report_section_back")],
        ]
    )


def stacked_period_keyboard(op: str) -> InlineKeyboardMarkup:
    """Period selection for the stacked report. op = 'inc' | 'exp'."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="3 месяца", callback_data=f"stacked_build:{op}:3"
                ),
                InlineKeyboardButton(
                    text="6 месяцев", callback_data=f"stacked_build:{op}:6"
                ),
            ],
            [InlineKeyboardButton(text="Год", callback_data=f"stacked_build:{op}:12")],
            [InlineKeyboardButton(text="← Назад", callback_data="report_section_back")],
        ]
    )


def chart_period_keyboard(
    current_period: str, report_type: str, year: int, month: int
) -> InlineKeyboardMarkup:
    """Interactive period switcher under a category chart (Feature 1).

    current_period: 'month' | 'quarter' | 'year'. report_type: 'income' | 'expense'.
    """
    op = "inc" if report_type == "income" else "exp"
    rows: list[list[InlineKeyboardButton]] = []

    if current_period == "month":
        rows.append(
            [
                InlineKeyboardButton(
                    text="◀", callback_data=f"chart_nav:prev:{op}:{year}:{month}"
                ),
                InlineKeyboardButton(
                    text=f"{RU_MONTHS[month]} {year}", callback_data="chart_noop"
                ),
                InlineKeyboardButton(
                    text="▶", callback_data=f"chart_nav:next:{op}:{year}:{month}"
                ),
            ]
        )

    def lbl(period: str, text: str) -> str:
        return f"✅ {text}" if period == current_period else text

    rows.append(
        [
            InlineKeyboardButton(
                text=lbl("month", "Месяц"),
                callback_data=f"chart_period:month:{op}:{year}:{month}",
            ),
            InlineKeyboardButton(
                text=lbl("quarter", "Квартал"),
                callback_data=f"chart_period:quarter:{op}:{year}:{month}",
            ),
            InlineKeyboardButton(
                text=lbl("year", "Год"),
                callback_data=f"chart_period:year:{op}:{year}:{month}",
            ),
        ]
    )

    if current_period == "month":
        rows.append(
            [
                InlineKeyboardButton(
                    text="📊 Сравнить с прошлым месяцем",
                    callback_data=f"compare:{report_type}:{year}:{month}",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)
