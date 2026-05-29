"""Keyboards for history: period, filters, category filter, search, record list."""

from functools import lru_cache
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .common import CANCEL_BUTTON


# Inline-клавиатура для выбора периода истории (расширенная)
@lru_cache(maxsize=1)
def history_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="hist_period:day"),
                InlineKeyboardButton(
                    text="Вчера", callback_data="hist_period:yesterday"
                ),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data="hist_period:week"),
                InlineKeyboardButton(
                    text="30 дней", callback_data="hist_period:month30"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Этот месяц", callback_data="hist_period:month"
                ),
                InlineKeyboardButton(
                    text="Прошлый месяц", callback_data="hist_period:prev_month"
                ),
            ],
            [
                InlineKeyboardButton(text="Этот год", callback_data="hist_period:year"),
                InlineKeyboardButton(
                    text="Свой период", callback_data="hist_period:custom"
                ),
            ],
            [CANCEL_BUTTON],
        ]
    )


def history_filter_keyboard(
    active_operation: str | None = None,
    active_category: str | None = None,
) -> InlineKeyboardMarkup:
    """Filter keyboard: Все / Расходы / Доходы / По категории / Сбросить / Поиск."""
    kb = InlineKeyboardBuilder()
    all_text = "✓ Все" if active_operation is None else "Все"
    expense_text = "✓ Расходы" if active_operation == "-" else "Только расходы"
    income_text = "✓ Доходы" if active_operation == "+" else "Только доходы"
    kb.button(text=all_text, callback_data="hist_filter:all")
    kb.button(text=expense_text, callback_data="hist_filter:expense")
    kb.button(text=income_text, callback_data="hist_filter:income")
    cat_text = f"● {active_category} ▾" if active_category else "По категории ▾"
    kb.button(text=cat_text, callback_data="hist_filter:category")
    kb.button(text="Сбросить", callback_data="hist_filter:reset")
    kb.button(text="🔍 Поиск", callback_data="hist_search:start")
    kb.adjust(3, 3)
    return kb.as_markup()


def history_category_filter_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    """Grid of category buttons (max 15). Each: callback hist_cat_filter:{name}."""
    kb = InlineKeyboardBuilder()
    for cat in categories[:15]:
        kb.button(text=cat, callback_data=f"hist_cat_filter:{cat}")
    kb.button(text="◀ Назад", callback_data="hist_cat_filter_back")
    kb.adjust(2)
    return kb.as_markup()


def search_result_keyboard(page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Pagination + [🔍 Новый поиск] + [◀ К фильтрам]."""
    kb = InlineKeyboardBuilder()
    nav_count = 0
    if total_pages > 1:
        if page > 0:
            kb.button(text="◀ Назад", callback_data=f"search_page:{page - 1}")
            nav_count += 1
        kb.button(text=f"{page + 1}/{total_pages}", callback_data="search_page:noop")
        nav_count += 1
        if page < total_pages - 1:
            kb.button(text="Вперёд ▶", callback_data=f"search_page:{page + 1}")
            nav_count += 1
    kb.button(text="🔍 Новый поиск", callback_data="search_new")
    kb.button(text="◀ К фильтрам", callback_data="search_back")
    row_sizes = ([nav_count] if nav_count > 0 else []) + [2]
    kb.adjust(*row_sizes)
    return kb.as_markup()


def history_record_select_keyboard(records: List) -> InlineKeyboardMarkup:
    """Shows current history page records as selectable buttons."""
    builder = InlineKeyboardBuilder()
    for r in records:
        sign = "+" if r.operation == "+" else "-"
        date_str = r.created_at.strftime("%d.%m")
        cat = (r.category or "")[:15]
        if len(r.category or "") > 15:
            cat += "…"
        amount_str = f"{float(r.amount):,.0f}".replace(",", " ")
        builder.button(
            text=f"{date_str} {sign}{amount_str} {cat}",
            callback_data=f"record:view:{r.id}",
        )
    builder.button(text="← Назад", callback_data="hist_back_from_select")
    builder.adjust(1)
    return builder.as_markup()
