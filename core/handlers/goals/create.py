"""Goal creation flow: name → amount → deadline → scope (personal/family)."""

import html
from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_GOAL_AMOUNT, MAX_GOAL_NAME_LENGTH
from core.database.models import async_session
from core.database.requests import create_goal, get_family
from core.keyboards import (
    goal_deadline_keyboard,
    goal_empty_keyboard,
    goal_scope_keyboard,
    goals_list_keyboard,
)
from core.utils import (
    clean_text,
    format_goals_list,
    log_exceptions,
    parse_flex_date,
    today_msk,
)

from ..common import GoalStates, get_message, get_user_id_from_event
from ._shared import _load_goals_view

router = Router()


@router.callback_query(F.data == "goal:new")
@log_exceptions("Ошибка при создании цели")
async def goal_new_start(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await get_message(callback).edit_text("Введите название цели (до 100 символов):")
    await state.set_state(GoalStates.entering_name)
    await callback.answer()


@router.message(GoalStates.entering_name)
@log_exceptions("Ошибка при вводе названия цели")
async def goal_name_entered(message: Message, state: FSMContext, **kwargs) -> None:
    name = clean_text(message.text or "")
    if not name or len(name) > MAX_GOAL_NAME_LENGTH:
        await message.answer(
            f"Название должно быть от 1 до {MAX_GOAL_NAME_LENGTH} символов. Попробуйте ещё раз:"
        )
        return
    await state.update_data(goal_name=name)
    await message.answer(
        f"Цель: <b>{html.escape(name)}</b>\n\nВведите целевую сумму (₽):",
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.entering_amount)


@router.message(GoalStates.entering_amount)
@log_exceptions("Ошибка при вводе суммы цели")
async def goal_amount_entered(message: Message, state: FSMContext, **kwargs) -> None:
    text = (message.text or "").strip()
    try:
        amount = Decimal(text.replace(",", ".").replace(" ", ""))
        if amount <= 0 or amount > Decimal(str(MAX_GOAL_AMOUNT)):
            raise ValueError
    except InvalidOperation, ValueError:
        await message.answer(
            f"Некорректная сумма. Введите число от 1 до {MAX_GOAL_AMOUNT:,}₽:".replace(
                ",", " "
            )
        )
        return
    await state.update_data(goal_target=str(amount))
    await message.answer(
        "Введите дедлайн в формате ДД.ММ.ГГ или пропустите:",
        reply_markup=goal_deadline_keyboard(),
    )
    await state.set_state(GoalStates.entering_deadline)


@router.message(GoalStates.entering_deadline)
@log_exceptions("Ошибка при вводе дедлайна цели")
async def goal_deadline_entered(message: Message, state: FSMContext, **kwargs) -> None:
    text = (message.text or "").strip()
    deadline = parse_flex_date(text)
    if deadline is None:
        await message.answer(
            "Неверный формат. Введите дату как ДД.ММ.ГГ или нажмите «Без дедлайна»:"
        )
        return
    if deadline <= today_msk():
        await message.answer("Дедлайн должен быть в будущем. Введите дату ДД.ММ.ГГ:")
        return
    user_id = await get_user_id_from_event(message, kwargs)
    await _prompt_scope_or_create(message, state, deadline, user_id)


@router.callback_query(F.data == "goal:no_deadline")
@log_exceptions("Ошибка при создании цели без дедлайна")
async def goal_no_deadline(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    await _prompt_scope_or_create(
        get_message(callback), state, None, user_id, callback=callback
    )


@router.callback_query(F.data == "goal:cancel")
@log_exceptions("Ошибка при отмене создания цели")
async def goal_cancel(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.clear()
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


async def _prompt_scope_or_create(
    message: Message,
    state: FSMContext,
    deadline,
    user_db_id: int | None,
    callback: CallbackQuery | None = None,
) -> None:
    """Family owner → ask личная/семейная. Otherwise create personal goal directly."""
    if not user_db_id:
        if callback:
            await callback.answer("Ошибка.")
        else:
            await message.answer("Ошибка.")
        return

    async with async_session() as session:
        family = await get_family(session, user_db_id)
    is_owner = bool(family and family.owner_id == user_db_id)

    if not is_owner:
        await _create_goal_and_confirm(
            message, state, deadline, user_db_id, None, callback=callback
        )
        return

    await state.update_data(
        goal_deadline_iso=deadline.isoformat() if deadline else None
    )
    prompt = "Цель личная или общая для семьи?"
    if callback:
        await get_message(callback).edit_text(
            prompt, reply_markup=goal_scope_keyboard()
        )
        await callback.answer()
    else:
        await message.answer(prompt, reply_markup=goal_scope_keyboard())
    await state.set_state(GoalStates.choosing_scope)


@router.callback_query(F.data == "goal:scope:personal", GoalStates.choosing_scope)
@log_exceptions("Ошибка при выборе типа цели")
async def goal_scope_personal(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    data = await state.get_data()
    iso = data.get("goal_deadline_iso")
    deadline = date.fromisoformat(iso) if iso else None
    await _create_goal_and_confirm(
        get_message(callback), state, deadline, user_id, None, callback=callback
    )


@router.callback_query(F.data == "goal:scope:family", GoalStates.choosing_scope)
@log_exceptions("Ошибка при создании семейной цели")
async def goal_scope_family(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    async with async_session() as session:
        family = await get_family(session, user_id)
    if not family or family.owner_id != user_id:
        await callback.answer(
            "Только владелец семьи может создать общую цель.", show_alert=True
        )
        return
    data = await state.get_data()
    iso = data.get("goal_deadline_iso")
    deadline = date.fromisoformat(iso) if iso else None
    await _create_goal_and_confirm(
        get_message(callback), state, deadline, user_id, family.id, callback=callback
    )


async def _create_goal_and_confirm(
    message: Message,
    state: FSMContext,
    deadline,
    user_db_id: int | None,
    family_id: int | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    if not user_db_id:
        if callback:
            await callback.answer("Ошибка.")
        else:
            await message.answer("Ошибка.")
        return

    data = await state.get_data()
    name = data.get("goal_name", "")
    target = Decimal(data.get("goal_target", "0"))

    async with async_session() as session:
        await create_goal(session, user_db_id, name, target, deadline, family_id)
        await session.commit()

    deadline_str = f" до {deadline.strftime('%d.%m.%Y')}" if deadline else ""
    scope_str = "\n👨‍👩‍👧 Общая для семьи" if family_id else ""
    text = (
        f"✅ Цель <b>{html.escape(name)}</b> создана!\n"
        f"Сумма: {target:,.0f}₽{deadline_str}{scope_str}".replace(",", " ")
    )

    goals, archive_count = await _load_goals_view(user_db_id)
    kb = goals_list_keyboard(goals, archive_count)

    if callback:
        await get_message(callback).edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    await state.set_state(GoalStates.viewing_list)
