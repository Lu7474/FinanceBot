"""Handlers for financial goals."""

import html
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_GOAL_AMOUNT, MAX_GOAL_NAME_LENGTH
from core.database.models import async_session
from core.database.requests import (
    complete_goal,
    create_goal,
    delete_goal,
    deposit_goal,
    get_accounts,
    get_goal,
    get_goal_deposits,
    get_goals,
    update_goal,
    withdraw_goal,
)
from core.exceptions import (
    GoalCompleted,
    GoalNotFound,
    GoalNotFoundOrCompleted,
    InsufficientFundsInGoal,
)
from core.keyboards import (
    goal_account_keyboard,
    goal_achievement_keyboard,
    goal_archive_list_keyboard,
    goal_confirm_complete_keyboard,
    goal_confirm_delete_keyboard,
    goal_deadline_keyboard,
    goal_detail_keyboard,
    goal_edit_deadline_keyboard,
    goal_edit_menu_keyboard,
    goal_empty_keyboard,
    goal_quick_amounts_keyboard,
    goals_list_keyboard,
    main_menu_keyboard,
)
from core.utils import (
    clean_text,
    format_duration_short,
    format_goal_detail,
    format_goals_list,
    log_exceptions,
    monthly_deposit_amount,
    today_msk,
)

from .common import GoalStates, get_message, get_user_id_from_event, is_goals

router = Router()


async def _load_goals_view(user_db_id: int) -> tuple[list, int]:
    """Returns (active_goals, archive_count) for list views."""
    async with async_session() as session:
        active = await get_goals(session, user_db_id)
        all_goals = await get_goals(session, user_db_id, include_completed=True)
    return active, len(all_goals) - len(active)


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


# ==================== Создание цели ====================


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
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введите число от 1 до {MAX_GOAL_AMOUNT:,}₽:".replace(
                ",", " "
            )
        )
        return
    await state.update_data(goal_target=str(amount))
    await message.answer(
        "Введите дедлайн в формате ДД.ММ.ГГГГ или пропустите:",
        reply_markup=goal_deadline_keyboard(),
    )
    await state.set_state(GoalStates.entering_deadline)


@router.message(GoalStates.entering_deadline)
@log_exceptions("Ошибка при вводе дедлайна цели")
async def goal_deadline_entered(message: Message, state: FSMContext, **kwargs) -> None:
    text = (message.text or "").strip()
    try:
        deadline = datetime.strptime(text, "%d.%m.%Y").date()
        if deadline <= today_msk():
            await message.answer(
                "Дедлайн должен быть в будущем. Введите дату ДД.ММ.ГГГГ:"
            )
            return
    except ValueError:
        await message.answer(
            "Неверный формат. Введите дату как ДД.ММ.ГГГГ или нажмите «Без дедлайна»:"
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    await _create_goal_and_confirm(message, state, deadline, user_id)


@router.callback_query(F.data == "goal:no_deadline")
@log_exceptions("Ошибка при создании цели без дедлайна")
async def goal_no_deadline(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    await _create_goal_and_confirm(
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


async def _create_goal_and_confirm(
    message: Message,
    state: FSMContext,
    deadline,
    user_db_id: int | None,
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
        await create_goal(session, user_db_id, name, target, deadline)
        await session.commit()

    deadline_str = f" до {deadline.strftime('%d.%m.%Y')}" if deadline else ""
    text = (
        f"✅ Цель <b>{html.escape(name)}</b> создана!\n"
        f"Сумма: {target:,.0f}₽{deadline_str}".replace(",", " ")
    )

    goals, archive_count = await _load_goals_view(user_db_id)
    kb = goals_list_keyboard(goals, archive_count)

    if callback:
        await get_message(callback).edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    await state.set_state(GoalStates.viewing_list)


# ==================== Карточка цели ====================


@router.callback_query(F.data.startswith("goal:detail:"))
@log_exceptions("Ошибка при открытии карточки цели")
async def goal_detail(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    goal_id = int(callback.data.split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        goal = await get_goal(session, goal_id, user_id)
        if not goal:
            await callback.answer("Цель не найдена.", show_alert=True)
            return
        deposits = await get_goal_deposits(session, goal_id)

    await get_message(callback).edit_text(
        format_goal_detail(goal, deposits),
        reply_markup=goal_detail_keyboard(goal_id, goal.is_completed),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_detail)
    await callback.answer()


# ==================== Пополнение цели ====================


@router.callback_query(F.data.startswith("goal:deposit:"))
@log_exceptions("Ошибка при начале пополнения цели")
async def goal_deposit_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    goal_id = int(callback.data.split(":")[2])
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
    parts = callback.data.split(":")
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
    except (InvalidOperation, ValueError):
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
    await _execute_deposit(get_message(callback), state, None, user_id, callback=callback)


async def _execute_deposit(
    message: Message,
    state: FSMContext,
    note: str | None,
    user_db_id: int,
    callback: CallbackQuery | None = None,
) -> None:
    data = await state.get_data()
    goal_id = data.get("deposit_goal_id")
    amount = Decimal(data.get("deposit_amount", "0"))
    account_id = data.get("deposit_account_id")

    try:
        async with async_session() as session:
            await deposit_goal(session, goal_id, user_db_id, amount, note, account_id)
            goal = await get_goal(session, goal_id, user_db_id)
            achieved = bool(goal and goal.current_amount >= goal.target_amount)
            await session.commit()
    except ValueError as e:
        await _handle_goal_op_error(message, state, e, user_db_id, callback=callback)
        return

    text = f"✅ Добавлено <b>{amount:,.0f}₽</b> к цели.".replace(",", " ")
    if achieved:
        text += (
            "\n\n🎉 <b>Цель достигнута!</b>\nЗакрыть её сейчас или оставить открытой?"
        )
        kb = goal_achievement_keyboard(goal_id)
        next_state = GoalStates.viewing_detail
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


# ==================== Снятие с цели ====================


@router.callback_query(F.data.startswith("goal:withdraw:"))
@log_exceptions("Ошибка при начале снятия с цели")
async def goal_withdraw_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    goal_id = int(callback.data.split(":")[2])
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
    parts = callback.data.split(":")
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
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return

    try:
        amount = Decimal(text.replace(",", ".").replace(" ", ""))
        if amount <= 0 or amount > Decimal(str(MAX_GOAL_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
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
    await _execute_withdraw(get_message(callback), state, None, user_id, callback=callback)


async def _execute_withdraw(
    message: Message,
    state: FSMContext,
    note: str | None,
    user_db_id: int,
    callback: CallbackQuery | None = None,
) -> None:
    data = await state.get_data()
    goal_id = data.get("withdraw_goal_id")
    amount = Decimal(data.get("withdraw_amount", "0"))
    account_id = data.get("withdraw_account_id")

    try:
        async with async_session() as session:
            await withdraw_goal(session, goal_id, user_db_id, amount, note, account_id)
            await session.commit()
    except ValueError as e:
        await _handle_goal_op_error(message, state, e, user_db_id, callback=callback)
        return

    text = f"📤 Снято <b>{amount:,.0f}₽</b> с цели.".replace(",", " ")

    goals, archive_count = await _load_goals_view(user_db_id)
    kb = goals_list_keyboard(goals, archive_count)

    if callback:
        await get_message(callback).edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb)

    await state.set_state(GoalStates.viewing_list)


# ==================== Завершение / Удаление ====================


@router.callback_query(F.data.startswith("goal:complete:"))
@log_exceptions("Ошибка при запросе завершения цели")
async def goal_complete(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    goal_id = int(callback.data.split(":")[2])
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
    goal_id = int(callback.data.split(":")[2])
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
    goal_id = int(callback.data.split(":")[2])
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
    goal_id = int(callback.data.split(":")[2])
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


# ==================== Быстрые суммы ====================


@router.callback_query(F.data.startswith("goal:qd:"))
@log_exceptions("Ошибка при быстром пополнении")
async def goal_quick_deposit(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Quick deposit amount: jump straight to note step."""
    parts = callback.data.split(":")
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


@router.callback_query(F.data.startswith("goal:qw:"))
@log_exceptions("Ошибка при быстром снятии")
async def goal_quick_withdraw(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Quick withdraw amount: jump straight to note step."""
    parts = callback.data.split(":")
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
    goal_id = int(callback.data.split(":")[2])
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


# ==================== Редактирование цели ====================


@router.callback_query(F.data.startswith("goal:edit:"))
@log_exceptions("Ошибка при открытии меню редактирования")
async def goal_edit_menu(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Shows edit submenu (name/amount/deadline)."""
    goal_id = int(callback.data.split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        goal = await get_goal(session, goal_id, user_id)
        if not goal:
            await callback.answer("Цель не найдена.", show_alert=True)
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
    goal_id = int(callback.data.split(":")[2])
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

    async with async_session() as session:
        await update_goal(session, goal_id, user_id, name=name)
        await session.commit()
        goal = await get_goal(session, goal_id, user_id)
        deposits = await get_goal_deposits(session, goal_id) if goal else []
        is_completed = goal.is_completed if goal else False
        detail_text = format_goal_detail(goal, deposits) if goal else "Цель не найдена."

    await message.answer(
        detail_text,
        reply_markup=goal_detail_keyboard(goal_id, is_completed),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_detail)


@router.callback_query(F.data.startswith("goal:edit_amount:"))
@log_exceptions("Ошибка при начале редактирования суммы")
async def goal_edit_amount_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    goal_id = int(callback.data.split(":")[2])
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
    except (InvalidOperation, ValueError):
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
        deposits = await get_goal_deposits(session, goal_id) if goal else []
        is_completed = goal.is_completed if goal else False
        detail_text = format_goal_detail(goal, deposits) if goal else "Цель не найдена."

    await message.answer(
        detail_text,
        reply_markup=goal_detail_keyboard(goal_id, is_completed),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_detail)


@router.callback_query(F.data.startswith("goal:edit_deadline:"))
@log_exceptions("Ошибка при начале редактирования дедлайна")
async def goal_edit_deadline_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    goal_id = int(callback.data.split(":")[2])
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
            "Введите новый в формате ДД.ММ.ГГГГ или уберите:"
        )
    else:
        prompt = "Сейчас без дедлайна.\n\nВведите дедлайн в формате ДД.ММ.ГГГГ:"
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
    try:
        deadline = datetime.strptime(text, "%d.%m.%Y").date()
        if deadline <= today_msk():
            await message.answer(
                "Дедлайн должен быть в будущем. Введите дату ДД.ММ.ГГГГ:"
            )
            return
    except ValueError:
        await message.answer("Неверный формат. Введите дату как ДД.ММ.ГГГГ:")
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    data = await state.get_data()
    goal_id = data.get("edit_goal_id")

    async with async_session() as session:
        await update_goal(session, goal_id, user_id, deadline=deadline)
        await session.commit()
        goal = await get_goal(session, goal_id, user_id)
        deposits = await get_goal_deposits(session, goal_id) if goal else []
        is_completed = goal.is_completed if goal else False
        detail_text = format_goal_detail(goal, deposits) if goal else "Цель не найдена."

    await message.answer(
        detail_text,
        reply_markup=goal_detail_keyboard(goal_id, is_completed),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_detail)


@router.callback_query(F.data.startswith("goal:clear_deadline:"))
@log_exceptions("Ошибка при удалении дедлайна")
async def goal_clear_deadline(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Removes deadline from goal."""
    goal_id = int(callback.data.split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        await update_goal(session, goal_id, user_id, clear_deadline=True)
        await session.commit()
        goal = await get_goal(session, goal_id, user_id)
        deposits = await get_goal_deposits(session, goal_id) if goal else []
        is_completed = goal.is_completed if goal else False
        detail_text = format_goal_detail(goal, deposits) if goal else "Цель не найдена."

    await get_message(callback).edit_text(
        detail_text,
        reply_markup=goal_detail_keyboard(goal_id, is_completed),
        parse_mode="HTML",
    )
    await state.set_state(GoalStates.viewing_detail)
    await callback.answer()


# ==================== Назад в меню ====================


@router.callback_query(F.data == "goal:back")
@log_exceptions("Ошибка при возврате из целей")
async def goal_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.clear()
    await get_message(callback).answer("Главное меню:", reply_markup=main_menu_keyboard())
    await get_message(callback).delete()
    await callback.answer()
