"""Keyboards for the family budget section."""

from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Member palette (max 5 members). Index = position in the family (owner first).
MEMBER_MARKERS = ["🔵", "🟢", "🟠", "🟣", "🟡"]


def member_marker(index: int) -> str:
    """Returns the colour marker for a member by their order in the family."""
    return MEMBER_MARKERS[index % len(MEMBER_MARKERS)]


@lru_cache(maxsize=1)
def family_join_or_create_keyboard() -> InlineKeyboardMarkup:
    """Shown when the user has no family yet."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Создать семью", callback_data="fam:create"
                ),
                InlineKeyboardButton(
                    text="🔑 Присоединиться", callback_data="fam:join"
                ),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="fam:back")],
        ]
    )


def family_menu_keyboard(is_owner: bool) -> InlineKeyboardMarkup:
    """Summary screen actions. Owner sees management, member sees leave."""
    rows = [
        [
            InlineKeyboardButton(
                text="📋 Общая история", callback_data="fam:history"
            ),
            InlineKeyboardButton(text="📊 Общий отчёт", callback_data="fam:report"),
        ],
    ]
    if is_owner:
        rows.append(
            [InlineKeyboardButton(text="⚙️ Управление", callback_data="fam:manage")]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="🚪 Покинуть семью", callback_data="fam:leave")]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="fam:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def family_history_filter_keyboard(
    members: list,
    page: int = 0,
    total_pages: int = 1,
    active_user_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Pagination row + per-member filter buttons ([Все] + each member)."""
    builder = InlineKeyboardBuilder()

    nav_count = 0
    if total_pages > 1:
        if page > 0:
            builder.button(text="◀", callback_data=f"fam:hist_page:{page - 1}")
            nav_count += 1
        builder.button(text=f"{page + 1}/{total_pages}", callback_data="fam:noop")
        nav_count += 1
        if page < total_pages - 1:
            builder.button(text="▶", callback_data=f"fam:hist_page:{page + 1}")
            nav_count += 1

    all_text = "✓ Все" if active_user_id is None else "Все"
    builder.button(text=all_text, callback_data="fam:hist_filter:all")
    filter_count = 1
    for idx, m in enumerate(members):
        marker = member_marker(idx)
        prefix = "✓ " if active_user_id == m.id else ""
        name = (m.name or "—")[:12]
        builder.button(
            text=f"{prefix}{marker} {name}",
            callback_data=f"fam:hist_filter:{m.id}",
        )
        filter_count += 1

    builder.button(text="← Назад", callback_data="fam:menu")

    row_sizes = ([nav_count] if nav_count else []) + [3] * (filter_count // 3)
    rest = filter_count % 3
    if rest:
        row_sizes.append(rest)
    row_sizes.append(1)  # back button
    builder.adjust(*row_sizes)
    return builder.as_markup()


def family_manage_keyboard(members: list, owner_user_id: int) -> InlineKeyboardMarkup:
    """Owner management screen: regenerate code, kick members, rename, dissolve."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить код", callback_data="fam:regen")
    builder.adjust(1)

    for m in members:
        if m.id == owner_user_id:
            continue
        name = (m.name or "—")[:20]
        builder.row(
            InlineKeyboardButton(
                text=f"❌ Удалить {name}", callback_data=f"fam:kick:{m.id}"
            )
        )

    builder.row(
        InlineKeyboardButton(text="✏️ Переименовать", callback_data="fam:rename"),
        InlineKeyboardButton(text="💥 Расформировать", callback_data="fam:dissolve"),
    )
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="fam:menu"))
    return builder.as_markup()


def family_confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    """Yes/Cancel confirmation. action ∈ {'leave', 'dissolve'}."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да", callback_data=f"fam:{action}_yes"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="fam:menu"),
            ]
        ]
    )


def family_kick_confirm_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Confirmation before removing a member."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"fam:kick_yes:{target_user_id}",
                ),
                InlineKeyboardButton(text="Отмена", callback_data="fam:manage"),
            ]
        ]
    )


@lru_cache(maxsize=1)
def family_report_type_keyboard() -> InlineKeyboardMarkup:
    """Choose income/expense for the family report."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📈 Доходы", callback_data="fam:rep_type:income"
                ),
                InlineKeyboardButton(
                    text="📉 Расходы", callback_data="fam:rep_type:expense"
                ),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="fam:menu")],
        ]
    )


def family_report_period_keyboard(op: str, active: str) -> InlineKeyboardMarkup:
    """Period switch under the family report chart. op ∈ {'inc','exp'}."""
    periods = [("month", "Этот месяц"), ("quarter", "3 месяца"), ("year", "Год")]
    builder = InlineKeyboardBuilder()
    for key, label in periods:
        text = f"✓ {label}" if key == active else label
        builder.button(text=text, callback_data=f"fam:rep_period:{op}:{key}")
    builder.button(text="← Назад", callback_data="fam:menu")
    builder.adjust(3, 1)
    return builder.as_markup()
