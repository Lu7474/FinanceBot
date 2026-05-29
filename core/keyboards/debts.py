"""Keyboards for debts & loans: menu, creation steps, card, archive."""

from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import format_money


def debts_menu_keyboard(has_active: bool, has_archive: bool) -> InlineKeyboardMarkup:
    """Main debts section menu under the list."""
    rows = [
        [InlineKeyboardButton(text="➕ Добавить долг", callback_data="debt:add")],
    ]
    if has_active:
        rows.append(
            [
                InlineKeyboardButton(text="💰 Погасить", callback_data="debt:pay_list"),
                InlineKeyboardButton(
                    text="📋 Карточка", callback_data="debt:view_list"
                ),
            ]
        )
    archive_row = []
    if has_archive:
        archive_row.append(
            InlineKeyboardButton(text="📦 Архив", callback_data="debt:archive")
        )
    archive_row.append(InlineKeyboardButton(text="← Назад", callback_data="debt:back"))
    rows.append(archive_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@lru_cache(maxsize=1)
def debt_reminder_open_keyboard() -> InlineKeyboardMarkup:
    """Single 'Open debts' button under daily reminder messages."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть долги", callback_data="debt:open")]
        ]
    )


@lru_cache(maxsize=1)
def debt_direction_keyboard() -> InlineKeyboardMarkup:
    """Step 1 of debt creation: choose direction."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Мне должны", callback_data="debt:dir:I"),
                InlineKeyboardButton(text="📤 Я должен", callback_data="debt:dir:O"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="debt:cancel")],
        ]
    )


def debt_select_keyboard(debts: list, action: str) -> InlineKeyboardMarkup:
    """List of active debts as buttons. action ∈ {'pay', 'view'} → debt:{action}:{id}."""
    builder = InlineKeyboardBuilder()
    for d in debts:
        arrow = "📥" if d.direction == "I" else "📤"
        name = d.person_name if len(d.person_name) <= 40 else d.person_name[:39] + "…"
        amount_str = format_money(float(d.remaining))
        builder.button(
            text=f"{arrow} {name} — {amount_str}",
            callback_data=f"debt:{action}:{d.id}",
        )
    builder.button(text="← Назад", callback_data="debt:open")
    builder.adjust(1)
    return builder.as_markup()


def debt_detail_keyboard(debt_id: int, is_archived: bool) -> InlineKeyboardMarkup:
    """Debt card actions. Archived: no 'Погасить'."""
    rows = []
    if not is_archived:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💰 Погасить", callback_data=f"debt:pay:{debt_id}"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"debt:del:{debt_id}")]
    )
    back_cb = "debt:archive" if is_archived else "debt:open"
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@lru_cache(maxsize=1)
def debt_due_date_keyboard() -> InlineKeyboardMarkup:
    """Step 5 of debt creation: skip due_date or cancel."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Без срока", callback_data="debt:dd_skip"),
                InlineKeyboardButton(text="Отмена", callback_data="debt:cancel"),
            ]
        ]
    )


def debt_skip_keyboard(skip_callback: str) -> InlineKeyboardMarkup:
    """Generic 'Skip + Cancel' keyboard for optional text fields."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Пропустить", callback_data=skip_callback),
                InlineKeyboardButton(text="Отмена", callback_data="debt:cancel"),
            ]
        ]
    )


def debt_delete_confirm_keyboard(
    debt_id: int, is_archived: bool
) -> InlineKeyboardMarkup:
    """Confirmation for hard debt deletion."""
    cancel_cb = (
        f"debt:view:{debt_id}" if not is_archived else f"debt:arch_view:{debt_id}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=f"debt:del_yes:{debt_id}",
                ),
                InlineKeyboardButton(text="Отмена", callback_data=cancel_cb),
            ]
        ]
    )


def debt_archive_keyboard(
    debts: list, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """Archive list with pagination. Each debt opens its (archived) card."""
    builder = InlineKeyboardBuilder()
    for d in debts:
        arrow = "📥" if d.direction == "I" else "📤"
        name = d.person_name if len(d.person_name) <= 40 else d.person_name[:39] + "…"
        amount_str = format_money(float(d.amount))
        builder.button(
            text=f"{arrow} {name} — {amount_str}",
            callback_data=f"debt:arch_view:{d.id}",
        )
    builder.adjust(1)

    if total_pages > 1:
        nav = InlineKeyboardBuilder()
        if page > 0:
            nav.button(text="◀", callback_data=f"debt:arch_page:{page - 1}")
        nav.button(text=f"{page + 1}/{total_pages}", callback_data="debt:noop")
        if page < total_pages - 1:
            nav.button(text="▶", callback_data=f"debt:arch_page:{page + 1}")
        nav.adjust(3)
        for row in nav.export():
            builder.row(*row)

    builder.row(InlineKeyboardButton(text="← Назад", callback_data="debt:open"))
    return builder.as_markup()
