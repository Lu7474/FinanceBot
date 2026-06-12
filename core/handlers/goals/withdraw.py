"""Goal withdraw flow (+ quick withdraw amounts)."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_GOAL_AMOUNT
from core.database.models import User, async_session
from core.database.requests import (
    get_accounts,
    get_family_members,
    get_goal,
    withdraw_goal,
)
from core.keyboards import (
    goal_account_keyboard,
    goal_quick_amounts_keyboard,
    goals_list_keyboard,
)
from core.utils import log_exceptions

from ..common import GoalStates, get_message, get_user_id_from_event
from ._shared import (
    _handle_goal_op_error,
    _load_goals_view,
    _notify_family_goal_move,
)

router = Router()


@router.callback_query(F.data.startswith("goal:withdraw:"))
@log_exceptions("Ошибка при начале снятия с цели")
async def goal_withdraw_start(
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
        if goal.current_amount <= 0:
            await callback.answer("Нечего снимать — цель пустая.", show_alert=True)
            return
        accounts = await get_accounts(session, user_id)

    await state.update_data(withdraw_goal_id=goal_id)

    if accounts:
        await get_message(callback).edit_text(
            f"Снятие с цели <b>{html.escape(goal.name)}</b>\nДоступно: {goal.current_amount:,.0f}₽\n\nВыберите счёт зачисления:".replace(
                ",", " "
            ),
            reply_markup=goal_account_keyboard(accounts, "withdraw_acc", goal_id),
            parse_mode="HTML",
        )
        await state.set_state(GoalStates.selecting_withdraw_account)
    else:
        await state.update_data(withdraw_account_id=None)
        available = float(goal.current_amount)
        await get_message(callback).edit_text(
            f"Снятие с цели <b>{html.escape(goal.name)}</b>\nДоступно: {goal.current_amount:,.0f}₽\n\nСколько снять (₽)? Введите вручную или выберите быструю сумму:".replace(
                ",", " "
            ),
            reply_markup=goal_quick_amounts_keyboard(goal_id, "qw", available),
            parse_mode="HTML",
        )
        await state.set_state(GoalStates.entering_withdraw_amount)
    await callback.answer()


@router.callback_query(F.data.startswith("goal:withdraw_acc:"))
@log_exceptions("Ошибка при выборе счёта для снятия")
async def goal_withdraw_account_selected(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    parts = (callback.data or "").split(":")
    goal_id = int(parts[2])
    account_id = int(parts[3])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    await state.update_data(withdraw_account_id=account_id if account_id != 0 else None)

    async with async_session() as session:
        goal = await get_goal(session, goal_id, user_id)
        if not goal:
            await callback.answer("Цель не найдена.", show_alert=True)
            return
        available = float(goal.current_amount)
        goal_name = goal.name
        current_amount = goal.current_amount

    await get_message(callback).edit_text(
        f"Снятие с цели <b>{html.escape(goal_name)}</b>\nДоступно: {current_amount:,.0f}₽\n\nСколько снять (₽)? Введите вручную или выберите быструю сумму:".replace(
            ",", " "
        ),
        reply_markup=goal_quick_amounts_keyboard(goal_id, "qw", available),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.entering_withdraw_amount)
    await callback.answer()


@router.message(GoalStates.entering_withdraw_amount)
@log_exceptions("Ошибка при вводе суммы снятия")
async def goal_withdraw_amount_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    text = (message.text or "").strip()
    data = await state.get_data()
    goal_id = data.get("withdraw_goal_id")
    assert isinstance(goal_id, int)
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return

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

    async with async_session() as session:
        goal = await get_goal(session, goal_id, user_id)
        if not goal:
            await message.answer("Цель не найдена.")
            await state.clear()
            return
        if amount > goal.current_amount:
            available = goal.current_amount
            await message.answer(
                f"Недостаточно средств в цели. Доступно: {available:,.0f}₽:".replace(
                    ",", " "
                )
            )
            return

    await state.update_data(withdraw_amount=str(amount))
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить", callback_data="goal:withdraw_skip_note"
                )
            ]
        ]
    )
    await message.answer("Добавить заметку к операции?", reply_markup=skip_kb)
    await state.set_state(GoalStates.entering_withdraw_note)


@router.message(GoalStates.entering_withdraw_note)
@log_exceptions("Ошибка при вводе заметки снятия")
async def goal_withdraw_note_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    note = (message.text or "").strip() or None
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _execute_withdraw(message, state, note, user_id)


@router.callback_query(F.data == "goal:withdraw_skip_note")
@log_exceptions("Ошибка при пропуске заметки снятия")
async def goal_withdraw_skip_note(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _execute_withdraw(
        get_message(callback), state, None, user_id, callback=callback
    )


async def _execute_withdraw(
    message: Message,
    state: FSMContext,
    note: str | None,
    user_db_id: int,
    callback: CallbackQuery | None = None,
) -> None:
    data = await state.get_data()
    goal_id = data.get("withdraw_goal_id")
    assert isinstance(goal_id, int)
    amount = Decimal(data.get("withdraw_amount", "0"))
    account_id = data.get("withdraw_account_id")

    try:
        async with async_session() as session:
            await withdraw_goal(session, goal_id, user_db_id, amount, note, account_id)
            goal = await get_goal(session, goal_id, user_db_id)
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
            message.bot, members, user_db_id, actor_name, goal_name, amount, "withdraw"
        )

    text = f"📤 Снято <b>{amount:,.0f}₽</b> с цели.".replace(",", " ")

    goals, archive_count = await _load_goals_view(user_db_id)
    kb = goals_list_keyboard(goals, archive_count)

    if callback:
        await get_message(callback).edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    await state.set_state(GoalStates.viewing_list)


# ==================== Быстрые суммы ====================


@router.callback_query(F.data.startswith("goal:qw:"))
@log_exceptions("Ошибка при быстром снятии")
async def goal_quick_withdraw(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Quick withdraw amount: jump straight to note step."""
    parts = (callback.data or "").split(":")
    goal_id = int(parts[2])
    amount = Decimal(parts[3])
    await state.update_data(withdraw_goal_id=goal_id, withdraw_amount=str(amount))
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить", callback_data="goal:withdraw_skip_note"
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
    await state.set_state(GoalStates.entering_withdraw_note)
    await callback.answer()
