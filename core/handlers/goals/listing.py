"""Goal list, detail card, archive, and back-to-menu handlers."""

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.database.models import async_session
from core.database.requests import get_goal, get_goals
from core.keyboards import (
    goal_archive_list_keyboard,
    goal_empty_keyboard,
    goals_list_keyboard,
    main_menu_keyboard,
)
from core.utils import format_duration_short, format_goals_list, log_exceptions

from ..common import GoalStates, get_message, get_user_id_from_event, is_goals
from ._shared import _build_goal_detail_view, _load_goals_view

router = Router()


# ==================== Список целей ====================


@router.message(F.func(is_goals))
@log_exceptions("Ошибка при открытии целей")
async def show_goals(message: Message, state: FSMContext, **kwargs) -> None:
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return

    goals, archive_count = await _load_goals_view(user_id)

    if not goals and archive_count == 0:
        await message.answer(
            "🎯 У вас пока нет целей.", reply_markup=goal_empty_keyboard()
        )
    else:
        await message.answer(
            format_goals_list(goals)
            if goals
            else "🎯 <b>Мои цели</b>\n\nНет активных целей.",
            reply_markup=goals_list_keyboard(goals, archive_count),
            parse_mode="HTML",
        )
    await state.set_state(GoalStates.viewing_list)


@router.callback_query(F.data == "goal:list")
@log_exceptions("Ошибка при обновлении списка целей")
async def goal_list_callback(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    goals, archive_count = await _load_goals_view(user_id)

    if not goals and archive_count == 0:
        await get_message(callback).edit_text(
            "🎯 У вас пока нет целей.", reply_markup=goal_empty_keyboard()
        )
    else:
        await get_message(callback).edit_text(
            format_goals_list(goals)
            if goals
            else "🎯 <b>Мои цели</b>\n\nНет активных целей.",
            reply_markup=goals_list_keyboard(goals, archive_count),
            parse_mode="HTML",
        )
    await state.set_state(GoalStates.viewing_list)
    await callback.answer()


# ==================== Карточка цели ====================


@router.callback_query(F.data.startswith("goal:detail:"))
@log_exceptions("Ошибка при открытии карточки цели")
async def goal_detail(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        goal = await get_goal(session, goal_id, user_id)
        if not goal:
            await callback.answer("Цель не найдена.", show_alert=True)
            return
        detail_text, kb = await _build_goal_detail_view(session, goal, user_id)

    await get_message(callback).edit_text(
        detail_text,
        reply_markup=kb,
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_detail)
    await callback.answer()


# ==================== Архив ====================


@router.callback_query(F.data == "goal:archive")
@log_exceptions("Ошибка при открытии архива целей")
async def goal_archive(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Shows list of completed (archived) goals."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        all_goals = await get_goals(session, user_id, include_completed=True)
    completed = [g for g in all_goals if g.is_completed]

    if not completed:
        await callback.answer("В архиве пусто.", show_alert=True)
        return

    lines = ["📁 <b>Архив целей</b>\n"]
    for g in completed:
        lines.append(
            f"✅ <b>{html.escape(g.name)}</b> — {g.target_amount:,.0f}₽".replace(
                ",", " "
            )
        )
        if g.completed_at:
            closed_str = g.completed_at.strftime("%d.%m.%Y")
            duration_days = (g.completed_at.date() - g.created_at.date()).days
            lines.append(
                f"   📅 {closed_str} • за {format_duration_short(duration_days)}"
            )

    await get_message(callback).edit_text(
        "\n".join(lines),
        reply_markup=goal_archive_list_keyboard(completed),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_archive)
    await callback.answer()


@router.callback_query(F.data.startswith("goal:reactivate:"))
@log_exceptions("Ошибка при переоткрытии цели")
async def goal_reactivate(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Marks completed goal as active again."""
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        from sqlalchemy import update as _update

        from core.database.models import Goal

        await session.execute(
            _update(Goal)
            .where(Goal.id == goal_id, Goal.user_id == user_id)
            .values(is_completed=False, completed_at=None)
        )
        await session.commit()

    goals, archive_count = await _load_goals_view(user_id)
    await get_message(callback).edit_text(
        "↩️ Цель переоткрыта.\n\n"
        + (format_goals_list(goals) if goals else "🎯 <b>Мои цели</b>"),
        reply_markup=goals_list_keyboard(goals, archive_count),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_list)
    await callback.answer()


# ==================== Назад в меню ====================


@router.callback_query(F.data == "goal:back")
@log_exceptions("Ошибка при возврате из целей")
async def goal_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.clear()
    await get_message(callback).answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    await get_message(callback).delete()
    await callback.answer()
