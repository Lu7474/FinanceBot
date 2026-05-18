"""Handlers for monthly budget management."""

import html
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from config import MAX_AMOUNT, TIMEZONE
from core.database.models import Record, async_session
from core.database.requests import (
    SYSTEM_CATEGORIES,
    delete_budget,
    get_budget_status,
    get_budgets,
    set_budget,
)
from core.keyboards import (
    budget_category_keyboard,
    budget_menu_keyboard,
)
from core.reports import format_budget_status
from core.utils import RU_MONTHS, log_exceptions

from .common import BudgetStates, get_user_id_from_event, is_budgets

router = Router()


async def _show_budget_status(target, user_id: int, state: FSMContext) -> None:
    """Renders current budget status with menu keyboard."""
    now = datetime.now(ZoneInfo(TIMEZONE))
    async with async_session() as session:
        budget_data = await get_budget_status(session, user_id, now.month, now.year)

    month_name = RU_MONTHS[now.month]
    header = f"📊 <b>Бюджеты на {month_name} {now.year}</b>\n\n"
    text = header + format_budget_status(budget_data)
    kb = budget_menu_keyboard()

    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

    await state.set_state(BudgetStates.choosing_action)


@router.message(F.func(is_budgets))
@log_exceptions("Ошибка при открытии бюджетов")
async def show_budgets(message: Message, state: FSMContext, **kwargs) -> None:
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return
    await _show_budget_status(message, user_id, state)


@router.callback_query(F.data == "budget_add", BudgetStates.choosing_action)
@log_exceptions("Ошибка при добавлении бюджета")
async def budget_add(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        budgets = await get_budgets(session, user_id)
        existing = {b.category for b in budgets}
        result = await session.execute(
            select(Record.category)
            .where(
                Record.user_id == user_id,
                Record.operation == "-",
                Record.category.not_in(SYSTEM_CATEGORIES),
            )
            .group_by(Record.category)
            .order_by(func.count(Record.id).desc())
        )
        all_categories = [row[0] for row in result.fetchall()]
        categories = [c for c in all_categories if c not in existing]

    if not categories:
        await callback.message.edit_text(
            "Все категории расходов уже имеют бюджет. Используйте «Изменить» для редактирования.",
            reply_markup=budget_menu_keyboard(),
        )
        await callback.answer()
        return

    await state.update_data(
        user_id=user_id, budget_action="set", budget_categories=categories
    )
    await callback.message.edit_text(
        "Выберите категорию для нового лимита:",
        reply_markup=budget_category_keyboard(categories),
    )
    await state.set_state(BudgetStates.choosing_category)
    await callback.answer()


@router.callback_query(F.data == "budget_edit", BudgetStates.choosing_action)
@log_exceptions("Ошибка при изменении бюджета")
async def budget_edit(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        budgets = await get_budgets(session, user_id)
        categories = [b.category for b in budgets]

    if not categories:
        await callback.message.edit_text(
            "Нет активных бюджетов для изменения. Сначала добавьте бюджет.",
            reply_markup=budget_menu_keyboard(),
        )
        await callback.answer()
        return

    await state.update_data(
        user_id=user_id, budget_action="set", budget_categories=categories
    )
    await callback.message.edit_text(
        "Выберите бюджет для изменения лимита:",
        reply_markup=budget_category_keyboard(categories),
    )
    await state.set_state(BudgetStates.choosing_category)
    await callback.answer()


@router.callback_query(F.data == "budget_delete", BudgetStates.choosing_action)
@log_exceptions("Ошибка при удалении бюджета")
async def budget_delete_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        budgets = await get_budgets(session, user_id)

    if not budgets:
        await callback.message.edit_text(
            "Нет активных бюджетов для удаления.",
            reply_markup=budget_menu_keyboard(),
        )
        await callback.answer()
        return

    categories = [b.category for b in budgets]
    await state.update_data(
        user_id=user_id, budget_action="delete", budget_categories=categories
    )
    await callback.message.edit_text(
        "Выберите бюджет для удаления:",
        reply_markup=budget_category_keyboard(categories),
    )
    await state.set_state(BudgetStates.choosing_category)
    await callback.answer()


@router.callback_query(F.data.startswith("budget_cat:"), BudgetStates.choosing_category)
@log_exceptions("Ошибка при обработке категории бюджета")
async def budget_category_selected(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    data = await state.get_data()
    action = data.get("budget_action")
    user_id = data.get("user_id")
    categories = data.get("budget_categories", [])
    try:
        idx = int(callback.data.split(":", 1)[1])
        category = categories[idx]
    except (ValueError, IndexError):
        await callback.answer("Ошибка: категория не найдена.")
        return

    if action == "delete":
        async with async_session() as session:
            budgets = await get_budgets(session, user_id)
            target_budget = next((b for b in budgets if b.category == category), None)
            deleted = False
            if target_budget:
                deleted = await delete_budget(session, target_budget.id, user_id)
            await session.commit()
        await callback.answer("Удалено." if deleted else "Бюджет не найден.")
        await _show_budget_status(callback, user_id, state)
    else:
        await state.update_data(chosen_category=category)
        # Если edit — покажем текущий лимит
        current_hint = ""
        if action == "edit":
            async with async_session() as session:
                budgets = await get_budgets(session, user_id)
                cur = next((b for b in budgets if b.category == category), None)
                if cur:
                    cur_raw = f"{cur.amount:.0f}"
                    current_hint = (
                        f"Текущий лимит: <code>{cur_raw}</code>\n\n"
                    )
        await callback.message.edit_text(
            f"{current_hint}Введите лимит для <b>{html.escape(category)}</b> на месяц (₽):",
            parse_mode="HTML",
        )
        await state.set_state(BudgetStates.entering_amount)
        await callback.answer()


@router.message(BudgetStates.entering_amount)
@log_exceptions("Ошибка при вводе суммы бюджета")
async def budget_amount_entered(message: Message, state: FSMContext, **kwargs) -> None:
    data = await state.get_data()
    user_id = data.get("user_id")
    category = data.get("chosen_category")

    try:
        amount = Decimal(message.text.strip().replace(",", ".").replace(" ", ""))
        if amount <= 0 or amount > Decimal(str(MAX_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введите число от 1 до {MAX_AMOUNT:,}₽:"
        )
        return

    async with async_session() as session:
        await set_budget(session, user_id, category, amount)
        await session.commit()

    await message.answer(
        f"✅ Лимит для <b>{html.escape(str(category))}</b> установлен: {amount:,.0f}₽/мес".replace(
            ",", " "
        ),
        parse_mode="HTML",
    )
    await _show_budget_status(message, user_id, state)


@router.callback_query(F.data == "budget_to_menu", BudgetStates.choosing_category)
@log_exceptions("Ошибка при возврате в меню бюджетов")
async def budget_back_to_menu(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    data = await state.get_data()
    user_id = data.get("user_id")
    await _show_budget_status(callback, user_id, state)
    await callback.answer()
