"""Shared helpers for goal handlers: list views, detail card, error reporting, family notifications."""

import html
from decimal import Decimal

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from core.database.models import User, async_session
from core.database.requests import (
    get_goal_contributions,
    get_goal_deposits,
    get_goal_monthly_pace,
    get_goals,
)
from core.exceptions import (
    GoalCompleted,
    GoalNotFound,
    GoalNotFoundOrCompleted,
    InsufficientFundsInGoal,
)
from core.keyboards import (
    goal_detail_keyboard,
    goal_empty_keyboard,
    goals_list_keyboard,
)
from core.utils import format_goal_detail

from ..common import GoalStates, get_message


async def _load_goals_view(user_db_id: int) -> tuple[list, int]:
    """Returns (active_goals, archive_count) for list views."""
    async with async_session() as session:
        active = await get_goals(session, user_db_id)
        all_goals = await get_goals(session, user_db_id, include_completed=True)
    return active, len(all_goals) - len(active)


async def _build_goal_detail_view(
    session, goal, user_db_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Builds (text, keyboard) for a goal card. For shared goals adds contributions
    and hides management buttons from non-owner members."""
    deposits = await get_goal_deposits(session, goal.id)
    pace = await get_goal_monthly_pace(session, goal.id, goal.created_at)
    contributions = (
        await get_goal_contributions(session, goal.id)
        if goal.family_id is not None
        else None
    )
    can_manage = goal.user_id == user_db_id
    text = format_goal_detail(goal, deposits, pace[0] if pace else None, contributions)
    kb = goal_detail_keyboard(goal.id, goal.is_completed, can_manage)
    return text, kb


_GOAL_ERROR_MESSAGES: list[tuple[type, str]] = [
    (GoalNotFoundOrCompleted, "Цель не найдена или уже закрыта."),
    (GoalCompleted, "Цель уже закрыта."),
    (GoalNotFound, "Цель не найдена."),
    (InsufficientFundsInGoal, "Недостаточно средств в цели."),
]


async def _handle_goal_op_error(
    message: Message,
    state: FSMContext,
    error: ValueError,
    user_db_id: int,
    callback: CallbackQuery | None = None,
) -> None:
    """Reports a deposit/withdraw failure to the user and returns to goals list."""
    user_text = next(
        (msg for exc_type, msg in _GOAL_ERROR_MESSAGES if isinstance(error, exc_type)),
        "Не удалось выполнить операцию по цели.",
    )
    goals, archive_count = await _load_goals_view(user_db_id)
    kb = (
        goal_empty_keyboard()
        if not goals and archive_count == 0
        else goals_list_keyboard(goals, archive_count)
    )
    if callback:
        await get_message(callback).edit_text(
            f"⚠️ {user_text}", parse_mode="HTML", reply_markup=kb
        )
        await callback.answer()
    else:
        await message.answer(f"⚠️ {user_text}", parse_mode="HTML", reply_markup=kb)
    await state.set_state(GoalStates.viewing_list)


async def _notify_family_goal_move(
    bot,
    members: list[User],
    actor_id: int,
    actor_name: str,
    goal_name: str,
    amount: Decimal,
    kind: str,
) -> None:
    """Notify other family members about a deposit/withdrawal on a shared goal.

    kind: "deposit" | "withdraw". Best-effort: a member who blocked the bot is skipped.
    """
    sum_txt = f"{amount:,.0f}".replace(",", " ")
    name = html.escape(actor_name or "Кто-то")
    goal = html.escape(goal_name)
    if kind == "deposit":
        text = f"💰 <b>{name}</b> внёс <b>{sum_txt}₽</b> в общую цель «{goal}»."
    else:
        text = f"📤 <b>{name}</b> снял <b>{sum_txt}₽</b> из общей цели «{goal}»."
    for m in members:
        if m.id == actor_id:
            continue
        try:
            await bot.send_message(m.tg_id, text, parse_mode="HTML")
        except Exception:
            continue  # member blocked bot / chat unavailable — skip
