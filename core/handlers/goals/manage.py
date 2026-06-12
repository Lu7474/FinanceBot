"""Owner-only goal management: complete and delete."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from core.database.models import async_session
from core.database.requests import complete_goal, delete_goal, get_owned_goal
from core.keyboards import (
    goal_confirm_complete_keyboard,
    goal_confirm_delete_keyboard,
    goal_empty_keyboard,
    goals_list_keyboard,
)
from core.utils import format_goals_list, log_exceptions

from ..common import GoalStates, get_message, get_user_id_from_event
from ._shared import _load_goals_view

router = Router()


@router.callback_query(F.data.startswith("goal:complete:"))
@log_exceptions("Ошибка при запросе завершения цели")
async def goal_complete(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    async with async_session() as session:
        if not await get_owned_goal(session, goal_id, user_id):
            await callback.answer(
                "Завершить может только владелец семьи.", show_alert=True
            )
            return
    await get_message(callback).edit_text(
        "Отметить цель как завершённую? Это действие нельзя отменить.",
        reply_markup=goal_confirm_complete_keyboard(goal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("goal:complete_confirm:"))
@log_exceptions("Ошибка при завершении цели")
async def goal_complete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        await complete_goal(session, goal_id, user_id)
        await session.commit()

    goals, archive_count = await _load_goals_view(user_id)

    if not goals and archive_count == 0:
        kb = goal_empty_keyboard()
    else:
        kb = goals_list_keyboard(goals, archive_count)

    await get_message(callback).edit_text(
        "✅ Цель завершена!",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_list)
    await callback.answer()


@router.callback_query(F.data.startswith("goal:delete:"))
@log_exceptions("Ошибка при запросе удаления цели")
async def goal_delete(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    async with async_session() as session:
        if not await get_owned_goal(session, goal_id, user_id):
            await callback.answer(
                "Удалить может только владелец семьи.", show_alert=True
            )
            return
    await get_message(callback).edit_text(
        "Удалить цель и все связанные операции? Это действие нельзя отменить.",
        reply_markup=goal_confirm_delete_keyboard(goal_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("goal:delete_confirm:"))
@log_exceptions("Ошибка при удалении цели")
async def goal_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        await delete_goal(session, goal_id, user_id)
        await session.commit()

    goals, archive_count = await _load_goals_view(user_id)

    if not goals and archive_count == 0:
        await get_message(callback).edit_text(
            "🗑 Цель удалена.\n\n🎯 У вас пока нет целей.",
            reply_markup=goal_empty_keyboard(),
        )
    else:
        body = (
            format_goals_list(goals)
            if goals
            else "🎯 <b>Мои цели</b>\n\nНет активных целей."
        )
        await get_message(callback).edit_text(
            "🗑 Цель удалена.\n\n" + body,
            reply_markup=goals_list_keyboard(goals, archive_count),
            parse_mode="HTML",
        )

    await state.set_state(GoalStates.viewing_list)
    await callback.answer()
