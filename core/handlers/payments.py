"""Handlers for payment reminders: create, mark paid (recurring), edit, delete."""

import html
import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_PAYMENT_AMOUNT, MAX_PAYMENT_TITLE
from core.database.models import async_session
from core.database.requests import (
    add_record,
    check_and_alert_budget,
    create_payment,
    delete_payment,
    get_accounts,
    get_active_payments,
    get_payment,
    get_user_categories,
    mark_paid,
    update_payment,
)
from core.exceptions import PaymentAlreadyPaid, PaymentError, PaymentNotFound
from core.keyboards import (
    main_menu_keyboard,
    payment_amount_skip_keyboard,
    payment_cancel_keyboard,
    payment_category_keyboard,
    payment_confirm_record_keyboard,
    payment_delete_confirm_keyboard,
    payment_detail_keyboard,
    payment_edit_amount_skip_keyboard,
    payment_edit_menu_keyboard,
    payment_edit_period_keyboard,
    payment_pay_account_keyboard,
    payment_pay_amount_keyboard,
    payment_period_keyboard,
    payments_list_keyboard,
)
from core.utils import (
    clean_text,
    format_date_ru,
    format_money,
    format_payment_detail,
    format_payments_list,
    log_exceptions,
    parse_flex_date,
    today_msk,
)

from .common import PaymentStates, get_message, get_user_id_from_event, is_payments

router = Router()

_EMPTY_TEXT = "💳 <b>Платежи</b>\n\nУ тебя нет активных платежей."

_PAYMENT_ERROR_MESSAGES: list[tuple[type, str]] = [
    (PaymentNotFound, "Платёж не найден."),
    (PaymentAlreadyPaid, "Платёж уже отмечен оплаченным."),
]


def _human_error(error: Exception) -> str:
    for exc_type, msg in _PAYMENT_ERROR_MESSAGES:
        if isinstance(error, exc_type):
            return msg
    return "Не удалось выполнить операцию по платежу."


def _parse_amount(text: str) -> Decimal | None:
    """Parse a positive amount within limits. Returns None when invalid."""
    try:
        amount = Decimal(text.replace(",", ".").replace(" ", ""))
    except InvalidOperation, ValueError:
        return None
    if amount <= 0 or amount > Decimal(str(MAX_PAYMENT_AMOUNT)):
        return None
    return amount


async def _load_active(user_id: int) -> list:
    async with async_session() as session:
        return await get_active_payments(session, user_id)


async def _render_list(target: Message, user_id: int, edit: bool = False) -> None:
    """Render the payments overview (list + per-payment buttons)."""
    active = await _load_active(user_id)
    today = today_msk()
    text = format_payments_list(active, today) if active else _EMPTY_TEXT
    kb = payments_list_keyboard(active)
    if edit:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


async def _reply_with_list(
    callback: CallbackQuery, state: FSMContext, user_id: int, prefix: str
) -> None:
    active = await _load_active(user_id)
    today = today_msk()
    body = format_payments_list(active, today) if active else _EMPTY_TEXT
    kb = payments_list_keyboard(active)
    await get_message(callback).edit_text(
        f"{prefix}\n\n{body}", reply_markup=kb, parse_mode="HTML"
    )
    await state.set_state(PaymentStates.viewing_list)
    await callback.answer()


async def _show_card_cb(
    callback: CallbackQuery, state: FSMContext, user_id: int, payment_id: int
) -> None:
    try:
        async with async_session() as session:
            payment = await get_payment(session, payment_id, user_id)
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await get_message(callback).edit_text(
        format_payment_detail(payment, today_msk()),
        reply_markup=payment_detail_keyboard(payment_id),
        parse_mode="HTML",
    )
    await state.set_state(PaymentStates.viewing_detail)
    await callback.answer()


async def _show_card_msg(
    message: Message, state: FSMContext, user_id: int, payment_id: int
) -> None:
    async with async_session() as session:
        payment = await get_payment(session, payment_id, user_id)
    await message.answer(
        format_payment_detail(payment, today_msk()),
        reply_markup=payment_detail_keyboard(payment_id),
        parse_mode="HTML",
    )
    await state.set_state(PaymentStates.viewing_detail)


# ==================== Открытие раздела ====================


@router.message(F.func(is_payments))
@log_exceptions("Ошибка при открытии раздела платежей")
async def payments_entry(message: Message, state: FSMContext, **kwargs) -> None:
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _render_list(message, user_id, edit=False)
    await state.set_state(PaymentStates.viewing_list)


@router.callback_query(F.data == "pay:open")
@log_exceptions("Ошибка при обновлении списка платежей")
async def payments_open(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _render_list(get_message(callback), user_id, edit=True)
    await state.set_state(PaymentStates.viewing_list)
    await callback.answer()


@router.callback_query(F.data == "pay:back")
@log_exceptions("Ошибка при выходе из раздела платежей")
async def payments_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.clear()
    await get_message(callback).answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    await get_message(callback).delete()
    await callback.answer()


@router.callback_query(F.data == "pay:cancel")
@log_exceptions("Ошибка при отмене")
async def payments_cancel(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await state.set_data({})
    await _render_list(get_message(callback), user_id, edit=True)
    await state.set_state(PaymentStates.viewing_list)
    await callback.answer()


# ==================== Создание ====================


@router.callback_query(F.data == "pay:add")
@log_exceptions("Ошибка при начале создания платежа")
async def payment_add_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    await state.set_data({})
    await get_message(callback).edit_text(
        f"Введите название платежа (до {MAX_PAYMENT_TITLE} символов):\n"
        "Например: ОСАГО, Коммуналка, Налог.",
        reply_markup=payment_cancel_keyboard(),
    )
    await state.set_state(PaymentStates.waiting_title)
    await callback.answer()


@router.message(PaymentStates.waiting_title)
@log_exceptions("Ошибка при вводе названия платежа")
async def payment_title_entered(message: Message, state: FSMContext, **kwargs) -> None:
    title = clean_text(message.text or "")
    if not title or len(title) > MAX_PAYMENT_TITLE:
        await message.answer(
            f"Название должно быть от 1 до {MAX_PAYMENT_TITLE} символов. Ещё раз:"
        )
        return
    await state.update_data(pay_title=title)
    await message.answer(
        "Введите сумму (₽) или нажмите «Сумма не задана» (для плавающих платежей):",
        reply_markup=payment_amount_skip_keyboard(),
    )
    await state.set_state(PaymentStates.waiting_amount)


@router.message(PaymentStates.waiting_amount)
@log_exceptions("Ошибка при вводе суммы платежа")
async def payment_amount_entered(message: Message, state: FSMContext, **kwargs) -> None:
    amount = _parse_amount((message.text or "").strip())
    if amount is None:
        await message.answer(
            f"Некорректная сумма. Введи число от 1 до {MAX_PAYMENT_AMOUNT:,}₽ "
            "или нажми «Сумма не задана»:".replace(",", " ")
        )
        return
    await state.update_data(pay_amount=str(amount))
    await _ask_due_date(message)
    await state.set_state(PaymentStates.waiting_due_date)


@router.callback_query(F.data == "pay:amt_skip")
@log_exceptions("Ошибка при пропуске суммы")
async def payment_amount_skip(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    await state.update_data(pay_amount=None)
    await _ask_due_date(get_message(callback), edit=True)
    await state.set_state(PaymentStates.waiting_due_date)
    await callback.answer()


async def _ask_due_date(target: Message, edit: bool = False) -> None:
    text = "Укажите дату платежа (ДД.ММ.ГГ):"
    kb = payment_cancel_keyboard()
    if edit:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(PaymentStates.waiting_due_date)
@log_exceptions("Ошибка при вводе даты платежа")
async def payment_due_date_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    due_date = parse_flex_date((message.text or "").strip())
    if due_date is None:
        await message.answer("Неверный формат. Введи дату как ДД.ММ.ГГ:")
        return
    await state.update_data(pay_due=due_date.isoformat())
    await message.answer(
        "Как часто повторяется платёж?",
        reply_markup=payment_period_keyboard(),
    )
    await state.set_state(PaymentStates.waiting_period)


@router.callback_query(F.data.startswith("pay:period:"))
@log_exceptions("Ошибка при выборе периодичности")
async def payment_period_chosen(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    period = (callback.data or "").split(":")[2]
    if period not in ("none", "month", "year"):
        await callback.answer("Некорректный выбор.")
        return
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    await state.update_data(pay_period=period)
    async with async_session() as session:
        categories = await get_user_categories(session, user_id, cat_type="-")
    await get_message(callback).edit_text(
        "В какую категорию записывать расход при оплате?",
        reply_markup=payment_category_keyboard(categories),
    )
    await state.set_state(PaymentStates.waiting_category)
    await callback.answer()


async def _resolve_category_name(user_id: int, cat_id: int) -> str | None:
    """Maps a cat_select id to its name; 0 / unknown id → None (no category)."""
    if cat_id <= 0:
        return None
    async with async_session() as session:
        categories = await get_user_categories(session, user_id)
    for cat in categories:
        if cat.id == cat_id:
            return cat.name
    return None


@router.callback_query(F.data.startswith("pay:setcat:"), PaymentStates.waiting_category)
@log_exceptions("Ошибка при выборе категории платежа")
async def payment_create_category_chosen(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    cat_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    data = await state.get_data()
    title = data.get("pay_title")
    due_raw = data.get("pay_due")
    period = data.get("pay_period")
    if not title or not due_raw or not period:
        await _reply_with_list(
            callback, state, user_id, "⚠️ Сессия создания утеряна, начни заново."
        )
        return
    amount_raw = data.get("pay_amount")
    amount = Decimal(amount_raw) if amount_raw else None
    due_date = date.fromisoformat(due_raw)
    category = await _resolve_category_name(user_id, cat_id)

    async with async_session() as session:
        await create_payment(
            session, user_id, title, amount, due_date, period, category=category
        )
        await session.commit()

    head = f"✅ Платёж добавлен: {html.escape(title)}"
    await state.set_data({})
    await _reply_with_list(callback, state, user_id, head)


# ==================== Карточка ====================


@router.callback_query(F.data.startswith("pay:view:"))
@log_exceptions("Ошибка при открытии карточки платежа")
async def payment_view(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    payment_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _show_card_cb(callback, state, user_id, payment_id)


# ==================== Оплата ====================


def _paid_head(title: str, next_due: date | None) -> str:
    if next_due:
        return (
            f"✅ Оплачено: {html.escape(title)}.\n"
            f"Следующее напоминание — {format_date_ru(next_due)}"
        )
    return f"✅ Платёж закрыт: {html.escape(title)}"


@router.callback_query(F.data.startswith("pay:done:"))
@log_exceptions("Ошибка при отметке оплаты")
async def payment_done(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Entry of the pay flow: offer to write the expense to balance."""
    payment_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            payment = await get_payment(session, payment_id, user_id)
            title, amount = payment.title, payment.amount
            due = payment.due_date.isoformat()
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return

    await state.update_data(pay_id=payment_id, pay_due=due)
    if amount is not None:
        await get_message(callback).edit_text(
            f"💳 {html.escape(title)}\n"
            f"Записать расход <b>−{format_money(amount)}</b> в баланс?",
            reply_markup=payment_confirm_record_keyboard(payment_id, amount, due),
            parse_mode="HTML",
        )
        # Buttons only — keep a neutral state so stray text isn't captured.
        await state.set_state(PaymentStates.viewing_detail)
    else:
        await get_message(callback).edit_text(
            f"💳 {html.escape(title)}\n"
            "Введи фактическую сумму платежа (₽) — запишу расход в баланс:",
            reply_markup=payment_pay_amount_keyboard(payment_id, due),
            parse_mode="HTML",
        )
        await state.set_state(PaymentStates.waiting_pay_amount)
    await callback.answer()


@router.callback_query(F.data.startswith("pay:rec_no:"))
@log_exceptions("Ошибка при отметке оплаты")
async def payment_record_skip(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Mark paid without touching the balance (old behavior)."""
    parts = (callback.data or "").split(":")
    payment_id = int(parts[2])
    expected_due = date.fromisoformat(parts[3])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            payment, next_due = await mark_paid(
                session, payment_id, user_id, expected_due=expected_due
            )
            title = payment.title
            await session.commit()
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await state.set_data({})
    await _reply_with_list(callback, state, user_id, _paid_head(title, next_due))


@router.callback_query(F.data.startswith("pay:rec_yes:"))
@log_exceptions("Ошибка при записи платежа")
async def payment_record_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Confirmed the fixed amount — proceed to account selection."""
    parts = (callback.data or "").split(":")
    payment_id = int(parts[2])
    due = parts[3]
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            payment = await get_payment(session, payment_id, user_id)
            amount = payment.amount
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    if amount is None:
        # Amount was cleared meanwhile — fall back to manual input.
        await get_message(callback).edit_text(
            "Введи фактическую сумму платежа (₽):",
            reply_markup=payment_pay_amount_keyboard(payment_id, due),
        )
        await state.update_data(pay_id=payment_id, pay_due=due)
        await state.set_state(PaymentStates.waiting_pay_amount)
        await callback.answer()
        return
    await _proceed_to_account(callback, state, user_id, payment_id, amount, due)


@router.callback_query(F.data.startswith("pay:rec_amt:"))
@log_exceptions("Ошибка при вводе суммы оплаты")
async def payment_record_other_amount(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    parts = (callback.data or "").split(":")
    payment_id = int(parts[2])
    due = parts[3]
    await state.update_data(pay_id=payment_id, pay_due=due)
    await get_message(callback).edit_text(
        "Введи фактическую сумму платежа (₽):",
        reply_markup=payment_pay_amount_keyboard(payment_id, due),
    )
    await state.set_state(PaymentStates.waiting_pay_amount)
    await callback.answer()


@router.message(PaymentStates.waiting_pay_amount)
@log_exceptions("Ошибка при вводе суммы оплаты")
async def payment_pay_amount_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    amount = _parse_amount((message.text or "").strip())
    if amount is None:
        await message.answer(
            f"Некорректная сумма. Введи число от 1 до {MAX_PAYMENT_AMOUNT:,}₽:".replace(
                ",", " "
            )
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    data = await state.get_data()
    payment_id = data.get("pay_id")
    due = data.get("pay_due")
    if not user_id or not isinstance(payment_id, int) or not isinstance(due, str):
        await message.answer("⚠️ Сессия оплаты утеряна, открой платёж заново.")
        await state.set_state(None)
        return
    await _proceed_to_account(message, state, user_id, payment_id, amount, due)


async def _proceed_to_account(
    event: CallbackQuery | Message,
    state: FSMContext,
    user_id: int,
    payment_id: int,
    amount: Decimal,
    due: str,
) -> None:
    """Account step: 0 accounts → no account, 1 → auto, 2+ → ask."""
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if len(accounts) > 1:
        await state.update_data(pay_id=payment_id, pay_record_amount=str(amount))
        text = "💳 На какой счёт записать расход?"
        kb = payment_pay_account_keyboard(payment_id, accounts, due)
        if isinstance(event, Message):
            await event.answer(text, reply_markup=kb)
        else:
            await get_message(event).edit_text(text, reply_markup=kb)
            await event.answer()
        await state.set_state(PaymentStates.choosing_pay_account)
        return

    account = accounts[0] if accounts else None
    await _record_and_finish(
        event, state, user_id, payment_id, amount, account, date.fromisoformat(due)
    )


@router.callback_query(
    F.data.startswith("pay:acc:"), PaymentStates.choosing_pay_account
)
@log_exceptions("Ошибка при выборе счёта для платежа")
async def payment_account_chosen(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    parts = (callback.data or "").split(":")
    payment_id, account_id = int(parts[2]), int(parts[3])
    expected_due = date.fromisoformat(parts[4])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    data = await state.get_data()
    amount_raw = data.get("pay_record_amount")
    # pay_id guard: the stored amount belongs to the flow started last; a tap on
    # a stale account keyboard from another payment must not reuse it.
    if not amount_raw or data.get("pay_id") != payment_id:
        await callback.answer(
            "⚠️ Сессия оплаты устарела, открой платёж заново.", show_alert=True
        )
        return
    amount = Decimal(amount_raw)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
    account = next((a for a in accounts if a.id == account_id), None)
    if account is None:
        await callback.answer("Счёт не найден.", show_alert=True)
        return
    await _record_and_finish(
        callback, state, user_id, payment_id, amount, account, expected_due
    )


async def _record_and_finish(
    event: CallbackQuery | Message,
    state: FSMContext,
    user_id: int,
    payment_id: int,
    amount: Decimal,
    account,
    expected_due: date,
) -> None:
    """Writes the expense Record and marks the payment paid in one transaction.

    `expected_due` mismatch in mark_paid rolls the whole transaction back —
    a double tap can't create a second Record."""
    try:
        async with async_session() as session:
            payment = await get_payment(session, payment_id, user_id)
            record_category = payment.category or "не указано"
            await add_record(
                session,
                user_id,
                "-",
                amount,
                category=record_category,
                account_id=account.id if account else None,
            )
            payment, next_due = await mark_paid(
                session, payment_id, user_id, expected_due=expected_due
            )
            title = payment.title
            await session.commit()
    except PaymentError as e:
        if isinstance(event, Message):
            await event.answer(_human_error(e))
        else:
            await event.answer(_human_error(e), show_alert=True)
        return

    head = f"✅ Оплачено и записано: {html.escape(title)} −{format_money(amount)}"
    if account:
        head += f" → {html.escape(account.name)}"
    if next_due:
        head += f"\nСледующее напоминание — {format_date_ru(next_due)}"
    await state.set_data({})

    if isinstance(event, Message):
        active = await _load_active(user_id)
        today = today_msk()
        body = format_payments_list(active, today) if active else _EMPTY_TEXT
        await event.answer(
            f"{head}\n\n{body}",
            reply_markup=payments_list_keyboard(active),
            parse_mode="HTML",
        )
        await state.set_state(PaymentStates.viewing_list)
    else:
        await _reply_with_list(event, state, user_id, head)

    # Budget alerts: a payment expense counts toward the category budget the
    # same way a manual record does (see _send_budget_alerts in records.py).
    message = event if isinstance(event, Message) else get_message(event)
    try:
        async with async_session() as session:
            alerts = await check_and_alert_budget(
                session, user_id, record_category, amount
            )
            await session.commit()
        for alert_text in alerts:
            await message.answer(alert_text)
    except Exception:
        logging.exception("Budget alert error (payment flow)")


# ==================== Редактирование ====================


@router.callback_query(F.data.startswith("pay:edit_menu:"))
@log_exceptions("Ошибка при открытии меню изменения")
async def payment_edit_menu(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    payment_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            await get_payment(session, payment_id, user_id)
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await get_message(callback).edit_text(
        "Что изменить?",
        reply_markup=payment_edit_menu_keyboard(payment_id),
    )
    # Neutral state: the edit menu expects button taps, not text input. Prevents a
    # stale editing_* state from capturing a stray message.
    await state.set_state(PaymentStates.viewing_detail)
    await callback.answer()


@router.callback_query(F.data.startswith("pay:edit:"))
@log_exceptions("Ошибка при выборе поля для изменения")
async def payment_edit_field(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    parts = (callback.data or "").split(":")
    field = parts[2]
    payment_id = int(parts[3])
    await state.update_data(edit_payment_id=payment_id)

    if field == "title":
        await get_message(callback).edit_text(
            f"Введите новое название (до {MAX_PAYMENT_TITLE} символов):",
            reply_markup=payment_cancel_keyboard(),
        )
        await state.set_state(PaymentStates.editing_title)
    elif field == "amount":
        await get_message(callback).edit_text(
            "Введите новую сумму (₽) или нажмите «Убрать сумму»:",
            reply_markup=payment_edit_amount_skip_keyboard(payment_id),
        )
        await state.set_state(PaymentStates.editing_amount)
    elif field == "date":
        await get_message(callback).edit_text(
            "Введите новую дату платежа (ДД.ММ.ГГ):",
            reply_markup=payment_cancel_keyboard(),
        )
        await state.set_state(PaymentStates.editing_due_date)
    elif field == "period":
        await get_message(callback).edit_text(
            "Выберите периодичность:",
            reply_markup=payment_edit_period_keyboard(payment_id),
        )
        # Period is picked via buttons — no text input expected here.
        await state.set_state(PaymentStates.viewing_detail)
    elif field == "category":
        user_id = await get_user_id_from_event(callback, kwargs)
        if not user_id:
            await callback.answer("Ошибка.")
            return
        async with async_session() as session:
            categories = await get_user_categories(session, user_id, cat_type="-")
        await get_message(callback).edit_text(
            "Выберите категорию для записи расхода:",
            reply_markup=payment_category_keyboard(
                categories, back_cb=f"pay:edit_menu:{payment_id}"
            ),
        )
        await state.set_state(PaymentStates.editing_category)
    else:
        await callback.answer("Неизвестное поле.")
        return
    await callback.answer()


@router.message(PaymentStates.editing_title)
@log_exceptions("Ошибка при изменении названия")
async def payment_editing_title(message: Message, state: FSMContext, **kwargs) -> None:
    title = clean_text(message.text or "")
    if not title or len(title) > MAX_PAYMENT_TITLE:
        await message.answer(
            f"Название должно быть от 1 до {MAX_PAYMENT_TITLE} символов. Ещё раз:"
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    payment_id = (await state.get_data()).get("edit_payment_id")
    if not user_id or not isinstance(payment_id, int):
        await message.answer("⚠️ Сессия изменения утеряна, открой платёж заново.")
        return
    try:
        async with async_session() as session:
            await update_payment(session, payment_id, user_id, title=title)
            await session.commit()
    except PaymentError as e:
        await message.answer(_human_error(e))
        return
    await _show_card_msg(message, state, user_id, payment_id)


@router.message(PaymentStates.editing_amount)
@log_exceptions("Ошибка при изменении суммы")
async def payment_editing_amount(message: Message, state: FSMContext, **kwargs) -> None:
    amount = _parse_amount((message.text or "").strip())
    if amount is None:
        await message.answer(
            f"Некорректная сумма. Введи число от 1 до {MAX_PAYMENT_AMOUNT:,}₽:".replace(
                ",", " "
            )
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    payment_id = (await state.get_data()).get("edit_payment_id")
    if not user_id or not isinstance(payment_id, int):
        await message.answer("⚠️ Сессия изменения утеряна, открой платёж заново.")
        return
    try:
        async with async_session() as session:
            await update_payment(session, payment_id, user_id, amount=amount)
            await session.commit()
    except PaymentError as e:
        await message.answer(_human_error(e))
        return
    await _show_card_msg(message, state, user_id, payment_id)


@router.callback_query(F.data.startswith("pay:clear_amount:"))
@log_exceptions("Ошибка при удалении суммы")
async def payment_clear_amount(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    payment_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            await update_payment(session, payment_id, user_id, clear_amount=True)
            await session.commit()
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await _show_card_cb(callback, state, user_id, payment_id)


@router.message(PaymentStates.editing_due_date)
@log_exceptions("Ошибка при изменении даты")
async def payment_editing_due_date(
    message: Message, state: FSMContext, **kwargs
) -> None:
    due_date = parse_flex_date((message.text or "").strip())
    if due_date is None:
        await message.answer("Неверный формат. Введи дату как ДД.ММ.ГГ:")
        return
    user_id = await get_user_id_from_event(message, kwargs)
    payment_id = (await state.get_data()).get("edit_payment_id")
    if not user_id or not isinstance(payment_id, int):
        await message.answer("⚠️ Сессия изменения утеряна, открой платёж заново.")
        return
    try:
        async with async_session() as session:
            await update_payment(session, payment_id, user_id, due_date=due_date)
            await session.commit()
    except PaymentError as e:
        await message.answer(_human_error(e))
        return
    await _show_card_msg(message, state, user_id, payment_id)


@router.callback_query(F.data.startswith("pay:setcat:"), PaymentStates.editing_category)
@log_exceptions("Ошибка при изменении категории")
async def payment_edit_category_chosen(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    cat_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    payment_id = (await state.get_data()).get("edit_payment_id")
    if not user_id or not isinstance(payment_id, int):
        await callback.answer("⚠️ Сессия изменения утеряна, открой платёж заново.")
        return
    category = await _resolve_category_name(user_id, cat_id)
    try:
        async with async_session() as session:
            if category is None:
                await update_payment(session, payment_id, user_id, clear_category=True)
            else:
                await update_payment(session, payment_id, user_id, category=category)
            await session.commit()
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await _show_card_cb(callback, state, user_id, payment_id)


@router.callback_query(F.data.startswith("pay:setperiod:"))
@log_exceptions("Ошибка при изменении периодичности")
async def payment_set_period(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    parts = (callback.data or "").split(":")
    period = parts[2]
    payment_id = int(parts[3])
    if period not in ("none", "month", "year"):
        await callback.answer("Некорректный выбор.")
        return
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            await update_payment(session, payment_id, user_id, period=period)
            await session.commit()
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await _show_card_cb(callback, state, user_id, payment_id)


# ==================== Удаление ====================


@router.callback_query(F.data.startswith("pay:del:"))
@log_exceptions("Ошибка при запросе удаления платежа")
async def payment_delete_request(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    payment_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            await get_payment(session, payment_id, user_id)
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await get_message(callback).edit_text(
        "Удалить платёж? Действие необратимо.",
        reply_markup=payment_delete_confirm_keyboard(payment_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay:del_yes:"))
@log_exceptions("Ошибка при удалении платежа")
async def payment_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    payment_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            await delete_payment(session, payment_id, user_id)
            await session.commit()
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await _reply_with_list(callback, state, user_id, "🗑 Платёж удалён.")
