"""Goal editing flow: name / amount / deadline."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_GOAL_AMOUNT, MAX_GOAL_NAME_LENGTH
from core.database.models import async_session
from core.database.requests import get_goal, get_owned_goal, update_goal
from core.keyboards import goal_edit_deadline_keyboard, goal_edit_menu_keyboard
from core.utils import clean_text, log_exceptions, parse_flex_date, today_msk

from ..common import GoalStates, get_message, get_user_id_from_event
from ._shared import _build_goal_detail_view

router = Router()


@router.callback_query(F.data.startswith("goal:edit:"))
@log_exceptions("Ошибка при открытии меню редактирования")
async def goal_edit_menu(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Shows edit submenu (name/amount/deadline)."""
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        goal = await get_owned_goal(session, goal_id, user_id)
        if not goal:
            await callback.answer(
                "Редактировать может только владелец семьи.", show_alert=True
            )
            return
        goal_name = goal.name

    await get_message(callback).edit_text(
        f"✏️ Редактирование цели <b>{html.escape(goal_name)}</b>\n\nЧто меняем?",
        reply_markup=goal_edit_menu_keyboard(goal_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("goal:edit_name:"))
@log_exceptions("Ошибка при начале редактирования имени")
async def goal_edit_name_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
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
        current_name = goal.name

    await state.update_data(edit_goal_id=goal_id)
    await get_message(callback).edit_text(
        f"Текущее имя: <code>{html.escape(current_name)}</code>\n\n"
        f"Введите новое название (до {MAX_GOAL_NAME_LENGTH} символов):",
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.editing_name)
    await callback.answer()


@router.message(GoalStates.editing_name)
@log_exceptions("Ошибка при сохранении нового имени")
async def goal_edit_name_entered(message: Message, state: FSMContext, **kwargs) -> None:
    name = clean_text(message.text or "")
    if not name or len(name) > MAX_GOAL_NAME_LENGTH:
        await message.answer(
            f"Название должно быть от 1 до {MAX_GOAL_NAME_LENGTH} символов. Попробуйте ещё раз:"
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    data = await state.get_data()
    goal_id = data.get("edit_goal_id")
    assert isinstance(goal_id, int)

    async with async_session() as session:
        await update_goal(session, goal_id, user_id, name=name)
        await session.commit()
        goal = await get_goal(session, goal_id, user_id)
        if goal:
            detail_text, kb = await _build_goal_detail_view(session, goal, user_id)
        else:
            detail_text, kb = "Цель не найдена.", None

    await message.answer(detail_text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(GoalStates.viewing_detail)


@router.callback_query(F.data.startswith("goal:edit_amount:"))
@log_exceptions("Ошибка при начале редактирования суммы")
async def goal_edit_amount_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
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
        current_amount = goal.target_amount

    await state.update_data(edit_goal_id=goal_id)
    current_str = f"{current_amount:,.0f}".replace(",", " ")
    max_str = f"{MAX_GOAL_AMOUNT:,}".replace(",", " ")
    await get_message(callback).edit_text(
        f"Текущая сумма: <code>{current_str}</code>\n\n"
        f"Введите новую целевую сумму (от 1 до {max_str}₽):",
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.editing_amount)
    await callback.answer()


@router.message(GoalStates.editing_amount)
@log_exceptions("Ошибка при сохранении новой суммы")
async def goal_edit_amount_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
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
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    data = await state.get_data()
    goal_id = data.get("edit_goal_id")
    assert isinstance(goal_id, int)

    async with async_session() as session:
        goal = await get_goal(session, goal_id, user_id)
        if not goal:
            await message.answer("Цель не найдена.")
            await state.clear()
            return
        if amount < goal.current_amount:
            current_str = f"{goal.current_amount:,.0f}".replace(",", " ")
            await message.answer(
                f"Новая сумма меньше уже накопленного ({current_str}₽). "
                "Сначала снимите излишек или укажите сумму ≥ накопленного:"
            )
            return
        await update_goal(session, goal_id, user_id, target_amount=amount)
        await session.commit()
        goal = await get_goal(session, goal_id, user_id)
        if goal:
            detail_text, kb = await _build_goal_detail_view(session, goal, user_id)
        else:
            detail_text, kb = "Цель не найдена.", None

    await message.answer(detail_text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(GoalStates.viewing_detail)


@router.callback_query(F.data.startswith("goal:edit_deadline:"))
@log_exceptions("Ошибка при начале редактирования дедлайна")
async def goal_edit_deadline_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
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
        current_deadline = goal.deadline

    await state.update_data(edit_goal_id=goal_id)
    if current_deadline:
        current_str = current_deadline.strftime("%d.%m.%Y")
        prompt = (
            f"Текущий дедлайн: <code>{current_str}</code>\n\n"
            "Введите новый в формате ДД.ММ.ГГ или уберите:"
        )
    else:
        prompt = "Сейчас без дедлайна.\n\nВведите дедлайн в формате ДД.ММ.ГГ:"
    await get_message(callback).edit_text(
        prompt,
        reply_markup=goal_edit_deadline_keyboard(goal_id),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.editing_deadline)
    await callback.answer()


@router.message(GoalStates.editing_deadline)
@log_exceptions("Ошибка при сохранении нового дедлайна")
async def goal_edit_deadline_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    text = (message.text or "").strip()
    deadline = parse_flex_date(text)
    if deadline is None:
        await message.answer("Неверный формат. Введите дату как ДД.ММ.ГГ:")
        return
    if deadline <= today_msk():
        await message.answer("Дедлайн должен быть в будущем. Введите дату ДД.ММ.ГГ:")
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    data = await state.get_data()
    goal_id = data.get("edit_goal_id")
    assert isinstance(goal_id, int)

    async with async_session() as session:
        await update_goal(session, goal_id, user_id, deadline=deadline)
        await session.commit()
        goal = await get_goal(session, goal_id, user_id)
        if goal:
            detail_text, kb = await _build_goal_detail_view(session, goal, user_id)
        else:
            detail_text, kb = "Цель не найдена.", None

    await message.answer(detail_text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(GoalStates.viewing_detail)


@router.callback_query(F.data.startswith("goal:clear_deadline:"))
@log_exceptions("Ошибка при удалении дедлайна")
async def goal_clear_deadline(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Removes deadline from goal."""
    goal_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        await update_goal(session, goal_id, user_id, clear_deadline=True)
        await session.commit()
        goal = await get_goal(session, goal_id, user_id)
        if goal:
            detail_text, kb = await _build_goal_detail_view(session, goal, user_id)
        else:
            detail_text, kb = "Цель не найдена.", None

    await get_message(callback).edit_text(
        detail_text,
        reply_markup=kb,
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_detail)
    await callback.answer()
