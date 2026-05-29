"""Keyboards for goals: list, card, edit, deposit/withdraw, deadline, confirms."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import format_money, goal_emoji, is_goal_overdue


def goal_empty_keyboard() -> InlineKeyboardMarkup:
    """Empty state: single 'Create first goal' button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Создать первую цель", callback_data="goal:new"
                )
            ]
        ]
    )


def goals_list_keyboard(goals: list, archive_count: int = 0) -> InlineKeyboardMarkup:
    """List of active goals + New goal + optional Archive. No back button (use reply menu)."""
    builder = InlineKeyboardBuilder()
    for goal in goals:
        if goal.current_amount >= goal.target_amount:
            emoji = "✅"
        elif is_goal_overdue(goal):
            emoji = "⚠️"
        else:
            emoji = goal_emoji(goal.name)
        # Усечение для лимита Telegram (64 char на текст кнопки)
        name = goal.name if len(goal.name) <= 55 else goal.name[:54] + "…"
        builder.button(text=f"{emoji} {name}", callback_data=f"goal:detail:{goal.id}")
    builder.button(text="➕ Новая цель", callback_data="goal:new")
    if archive_count > 0:
        builder.button(text=f"📁 Архив ({archive_count})", callback_data="goal:archive")
    # Goals 1-per-row; system buttons share last row (2 if archive shown, else 1)
    rows = [1] * len(goals) + [2 if archive_count > 0 else 1]
    builder.adjust(*rows)
    return builder.as_markup()


def goal_archive_list_keyboard(goals: list) -> InlineKeyboardMarkup:
    """List of completed (archived) goals + Back to active."""
    builder = InlineKeyboardBuilder()
    for goal in goals:
        name = goal.name if len(goal.name) <= 55 else goal.name[:54] + "…"
        builder.button(text=f"✅ {name}", callback_data=f"goal:detail:{goal.id}")
    builder.button(text="← К активным", callback_data="goal:list")
    builder.adjust(1)
    return builder.as_markup()


def goal_detail_keyboard(goal_id: int, is_completed: bool) -> InlineKeyboardMarkup:
    """Goal card actions. For completed goals: reactivate + delete + back."""
    rows = []
    if not is_completed:
        rows.append(
            [
                InlineKeyboardButton(
                    text="💰 Пополнить", callback_data=f"goal:deposit:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="📤 Снять", callback_data=f"goal:withdraw:{goal_id}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать", callback_data=f"goal:edit:{goal_id}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Завершить", callback_data=f"goal:complete:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"goal:delete:{goal_id}"
                ),
            ]
        )
        rows.append([InlineKeyboardButton(text="← Назад", callback_data="goal:list")])
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩️ Переоткрыть", callback_data=f"goal:reactivate:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"goal:delete:{goal_id}"
                ),
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="← В архив", callback_data="goal:archive")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def goal_edit_menu_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Submenu: choose which field to edit."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Имя", callback_data=f"goal:edit_name:{goal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Сумму", callback_data=f"goal:edit_amount:{goal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Дедлайн", callback_data=f"goal:edit_deadline:{goal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад", callback_data=f"goal:detail:{goal_id}"
                )
            ],
        ]
    )


def goal_edit_deadline_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Edit deadline: clear-deadline option + cancel back to detail."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Убрать дедлайн",
                    callback_data=f"goal:clear_deadline:{goal_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"goal:detail:{goal_id}"
                )
            ],
        ]
    )


def goal_quick_amounts_keyboard(
    goal_id: int,
    action: str,
    remaining: float | None,
    monthly: float | None = None,
) -> InlineKeyboardMarkup:
    """Quick amount buttons for deposit (action='qd') or withdraw (action='qw').

    Deposit: 10% / 25% / 50% of remaining + monthly + full remaining.
    Withdraw: 10% / 25% / 50% / 100% of current_amount (passed as remaining).
    """
    builder = InlineKeyboardBuilder()
    if remaining and remaining > 0:
        for pct in (10, 25, 50):
            amount = int(remaining * pct / 100)
            if amount > 0:
                builder.button(
                    text=f"{pct}% ({format_money(amount)})",
                    callback_data=f"goal:{action}:{goal_id}:{amount}",
                )
        if action == "qd" and monthly and monthly > 0:
            m = int(monthly)
            builder.button(
                text=f"Ежемес. ({format_money(m)})",
                callback_data=f"goal:{action}:{goal_id}:{m}",
            )
        full = int(remaining)
        label = "Остаток" if action == "qd" else "Всё"
        builder.button(
            text=f"{label} ({format_money(full)})",
            callback_data=f"goal:{action}:{goal_id}:{full}",
        )
    builder.button(text="Отмена", callback_data=f"goal:detail:{goal_id}")
    builder.adjust(2)
    return builder.as_markup()


def goal_deadline_keyboard() -> InlineKeyboardMarkup:
    """Skip deadline or cancel."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без дедлайна", callback_data="goal:no_deadline"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="goal:cancel"),
            ]
        ]
    )


def goal_achievement_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Nudge shown right after deposit makes goal achieved: close it or keep open."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Завершить цель",
                    callback_data=f"goal:complete_confirm:{goal_id}",
                )
            ],
            [InlineKeyboardButton(text="📋 К списку целей", callback_data="goal:list")],
        ]
    )


def goal_confirm_complete_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Confirmation for goal completion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, завершить",
                    callback_data=f"goal:complete_confirm:{goal_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"goal:detail:{goal_id}"
                ),
            ]
        ]
    )


def goal_confirm_delete_keyboard(goal_id: int) -> InlineKeyboardMarkup:
    """Confirmation for goal deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"goal:delete_confirm:{goal_id}"
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"goal:detail:{goal_id}"
                ),
            ]
        ]
    )


def goal_account_keyboard(
    accounts: list, action: str, goal_id: int
) -> InlineKeyboardMarkup:
    """Account selection for deposit/withdraw. action: 'deposit_acc' | 'withdraw_acc'."""
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(text=acc.name, callback_data=f"goal:{action}:{goal_id}:{acc.id}")
    builder.button(text="Пропустить", callback_data=f"goal:{action}:{goal_id}:0")
    builder.adjust(2)
    return builder.as_markup()
