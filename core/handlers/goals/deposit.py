"""Goal deposit flow (+ quick deposit amounts)."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_GOAL_AMOUNT
from core.database.models import User, async_session
from core.database.requests import (
    deposit_goal,
    get_accounts,
    get_family_members,
    get_goal,
)
from core.keyboards import (
    goal_account_keyboard,
    goal_achievement_keyboard,
    goal_quick_amounts_keyboard,
    goals_list_keyboard,
)
from core.utils import log_exceptions, monthly_deposit_amount

from ..common import GoalStates, get_message, get_user_id_from_event
from ._shared import (
    _handle_goal_op_error,
    _load_goals_view,
    _notify_family_goal_move,
)

router = Router()


@router.callback_query(F.data.startswith("goal:deposit:"))
@log_exceptions("Ошибка при начале пополнения цели")
async def goal_deposit_start(
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
        if goal.is_completed:
            await callback.answer("Цель уже завершена.", show_alert=True)
            return
        accounts = await get_accounts(session, user_id)

    await state.update_data(deposit_goal_id=goal_id)

    if accounts:
        await get_message(callback).edit_text(
            f"Пополнение цели <b>{html.escape(goal.name)}</b>\n\nВыберите счёт списания:",
            reply_markup=goal_account_keyboard(accounts, "deposit_acc", goal_id),
            parse_mode="HTML",
        )
        await state.set_state(GoalStates.selecting_deposit_account)
    else:
        await state.update_data(deposit_account_id=None)
        remaining = float(goal.target_amount - goal.current_amount)
        monthly = monthly_deposit_amount(goal)
        progress = f"Прогресс: {goal.current_amount:,.0f} / {goal.target_amount:,.0f}₽".replace(
            ",", " "
        )
        await get_message(callback).edit_text(
            f"Пополнение цели <b>{html.escape(goal.name)}</b>\n{progress}\n\nСколько откладываем (₽)? Введите вручную или выберите быструю сумму:",
            reply_markup=goal_quick_amounts_keyboard(goal_id, "qd", remaining, monthly),
            parse_mode="HTML",
        )
        await state.set_state(GoalStates.entering_deposit_amount)
    await callback.answer()


@router.callback_query(F.data.startswith("goal:deposit_acc:"))
@log_exceptions("Ошибка при выборе счёта для пополнения")
async def goal_deposit_account_selected(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    parts = (callback.data or "").split(":")
    goal_id = int(parts[2])
    account_id = int(parts[3])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    await state.update_data(deposit_account_id=account_id if account_id != 0 else None)

    async with async_session() as session:
        goal = await get_goal(session, goal_id, user_id)
        if not goal:
            await callback.answer("Цель не найдена.", show_alert=True)
            return
        remaining = float(goal.target_amount - goal.current_amount)
        monthly = monthly_deposit_amount(goal)
        goal_name = goal.name
        progress = f"Прогресс: {goal.current_amount:,.0f} / {goal.target_amount:,.0f}₽".replace(
            ",", " "
        )

    await get_message(callback).edit_text(
        f"Пополнение цели <b>{html.escape(goal_name)}</b>\n{progress}\n\nСколько откладываем (₽)? Введите вручную или выберите быструю сумму:",
        reply_markup=goal_quick_amounts_keyboard(goal_id, "qd", remaining, monthly),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.entering_deposit_amount)
    await callback.answer()


@router.message(GoalStates.entering_deposit_amount)
@log_exceptions("Ошибка при вводе суммы пополнения")
async def goal_deposit_amount_entered(
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

    await state.update_data(deposit_amount=str(amount))
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить", callback_data="goal:deposit_skip_note"
                )
            ]
        ]
    )
    await message.answer("Добавить заметку к операции?", reply_markup=skip_kb)
    await state.set_state(GoalStates.entering_deposit_note)


@router.message(GoalStates.entering_deposit_note)
@log_exceptions("Ошибка при вводе заметки пополнения")
async def goal_deposit_note_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    note = (message.text or "").strip() or None
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _execute_deposit(message, state, note, user_id)


@router.callback_query(F.data == "goal:deposit_skip_note")
@log_exceptions("Ошибка при пропуске заметки пополнения")
async def goal_deposit_skip_note(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _execute_deposit(
        get_message(callback), state, None, user_id, callback=callback
    )


async def _execute_deposit(
    message: Message,
    state: FSMContext,
    note: str | None,
    user_db_id: int,
    callback: CallbackQuery | None = None,
) -> None:
    data = await state.get_data()
    goal_id = data.get("deposit_goal_id")
    assert isinstance(goal_id, int)
    amount = Decimal(data.get("deposit_amount", "0"))
    account_id = data.get("deposit_account_id")

    try:
        async with async_session() as session:
            await deposit_goal(session, goal_id, user_db_id, amount, note, account_id)
            goal = await get_goal(session, goal_id, user_db_id)
            achieved = bool(goal and goal.current_amount >= goal.target_amount)
            can_manage = bool(goal and goal.user_id == user_db_id)
            family_id = goal.family_id if goal else None
            goal_name = goal.name if goal else ""
            members: list[User] = []
            actor_name = "Кто-то"
            if family_id is not None:
                members = await get_family_members(session, family_id)
                actor = await session.get(User, user_db_id)
                actor_name = actor.name if actor and actor.name else "Кто-то"
            await session.commit()
    except ValueError as e:
        await _handle_goal_op_error(message, state, e, user_db_id, callback=callback)
        return

    if family_id is not None:
        await _notify_family_goal_move(
            message.bot, members, user_db_id, actor_name, goal_name, amount, "deposit"
        )

    text = f"✅ Добавлено <b>{amount:,.0f}₽</b> к цели.".replace(",", " ")
    if achieved and can_manage:
        text += (
            "\n\n🎉 <b>Цель достигнута!</b>\nЗакрыть её сейчас или оставить открытой?"
        )
        kb = goal_achievement_keyboard(goal_id)
        next_state = GoalStates.viewing_detail
    elif achieved:
        # Member hit the target on a shared goal — only the family owner can close it.
        text += "\n\n🎉 <b>Цель достигнута!</b>\nЗакрыть её может владелец семьи."
        goals, archive_count = await _load_goals_view(user_db_id)
        kb = goals_list_keyboard(goals, archive_count)
        next_state = GoalStates.viewing_list
    else:
        goals, archive_count = await _load_goals_view(user_db_id)
        kb = goals_list_keyboard(goals, archive_count)
        next_state = GoalStates.viewing_list

    if callback:
        await get_message(callback).edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    await state.set_state(next_state)


# ==================== Быстрые суммы ====================


@router.callback_query(F.data.startswith("goal:qd:"))
@log_exceptions("Ошибка при быстром пополнении")
async def goal_quick_deposit(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Quick deposit amount: jump straight to note step."""
    parts = (callback.data or "").split(":")
    goal_id = int(parts[2])
    amount = Decimal(parts[3])
    await state.update_data(deposit_goal_id=goal_id, deposit_amount=str(amount))
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить", callback_data="goal:deposit_skip_note"
                )
            ]
        ]
    )
    await get_message(callback).edit_text(
        f"Сумма: <b>{amount:,.0f}₽</b>\n\nДобавить заметку к операции?".replace(
            ",", " "
        ),
        reply_markup=skip_kb,
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.entering_deposit_note)
    await callback.answer()
