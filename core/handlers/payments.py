"""Handlers for payment reminders: create, mark paid (recurring), edit, delete."""

import html
from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_PAYMENT_AMOUNT, MAX_PAYMENT_TITLE
from core.database.models import async_session
from core.database.requests import (
    create_payment,
    delete_payment,
    get_active_payments,
    get_payment,
    mark_paid,
    update_payment,
)
from core.exceptions import PaymentError, PaymentNotFound
from core.keyboards import (
    main_menu_keyboard,
    payment_amount_skip_keyboard,
    payment_cancel_keyboard,
    payment_delete_confirm_keyboard,
    payment_detail_keyboard,
    payment_edit_amount_skip_keyboard,
    payment_edit_menu_keyboard,
    payment_edit_period_keyboard,
    payment_period_keyboard,
    payments_list_keyboard,
)
from core.utils import (
    clean_text,
    format_date_ru,
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
    except (InvalidOperation, ValueError):
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
async def payment_title_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
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
async def payment_amount_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
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

    data = await state.get_data()
    title = data.get("pay_title")
    due_raw = data.get("pay_due")
    if not title or not due_raw:
        await _reply_with_list(
            callback, state, user_id, "⚠️ Сессия создания утеряна, начни заново."
        )
        return
    amount_raw = data.get("pay_amount")
    amount = Decimal(amount_raw) if amount_raw else None
    due_date = date.fromisoformat(due_raw)

    async with async_session() as session:
        await create_payment(session, user_id, title, amount, due_date, period)
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


@router.callback_query(F.data.startswith("pay:done:"))
@log_exceptions("Ошибка при отметке оплаты")
async def payment_done(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    payment_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            payment, next_due = await mark_paid(session, payment_id, user_id)
            title = payment.title
            await session.commit()
    except PaymentError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return

    if next_due:
        head = (
            f"✅ Оплачено: {html.escape(title)}.\n"
            f"Следующее напоминание — {format_date_ru(next_due)}"
        )
    else:
        head = f"✅ Платёж закрыт: {html.escape(title)}"
    await _reply_with_list(callback, state, user_id, head)


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
    else:
        await callback.answer("Неизвестное поле.")
        return
    await callback.answer()


@router.message(PaymentStates.editing_title)
@log_exceptions("Ошибка при изменении названия")
async def payment_editing_title(
    message: Message, state: FSMContext, **kwargs
) -> None:
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
async def payment_editing_amount(
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
