"""Transfers between accounts: create flow, list, view and cancel."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_AMOUNT, RECORDS_PER_PAGE
from core.database.models import async_session
from core.database.requests import (
    cancel_transfer,
    count_transfers,
    create_transfer,
    get_account_balance,
    get_account_balances,
    get_accounts,
    get_transfer,
    get_transfers,
)
from core.keyboards import (
    acc_back_keyboard,
    account_manage_keyboard,
    accounts_menu_keyboard,
    confirm_transfer_cancel_keyboard,
    main_menu_keyboard,
    transfer_card_keyboard,
    transfers_list_keyboard,
)
from core.utils import log_exceptions

from ..common import (
    AccountStates,
    get_message,
    get_user_id_from_event,
    is_main_menu_button,
)
from .common import _build_accounts_text

router = Router()


# --- Перевод между счетами ---


@router.callback_query(F.data == "acc_transfer")
@log_exceptions("Ошибка при переводе")
async def handle_acc_transfer(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список счетов для выбора источника перевода."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if len(accounts) < 2:
        await callback.answer("Нужно минимум 2 счёта для перевода.", show_alert=True)
        return

    await get_message(callback).edit_text(
        "↔️ <b>Перевод</b>\n\nВыберите счёт-источник:",
        reply_markup=account_manage_keyboard(accounts, "transfer_from"),
        parse_mode="HTML",
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_transfer_from:"))
@log_exceptions("Ошибка при выборе счёта-источника")
async def handle_acc_transfer_from(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список счетов-назначения (исключая источник)."""
    try:
        from_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    from_acc = next((a for a in accounts if a.id == from_id), None)
    if not from_acc:
        await callback.answer("Счёт не найден.")
        return

    destinations = [a for a in accounts if a.id != from_id]
    await state.update_data(transfer_from_id=from_id, acc_user_id=user_id)
    await get_message(callback).edit_text(
        f"↔️ <b>Перевод с «{html.escape(from_acc.name)}»</b>\n\nВыберите счёт-назначение:",
        reply_markup=account_manage_keyboard(destinations, f"transfer_to:{from_id}"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acc_transfer_to:"))
@log_exceptions("Ошибка при выборе счёта-назначения")
async def handle_acc_transfer_to(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет счёта перевода и запрашивает сумму."""
    try:
        parts = (callback.data or "").split(":")
        from_id = int(parts[1])
        to_id = int(parts[2])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    from_acc = next((a for a in accounts if a.id == from_id), None)
    to_acc = next((a for a in accounts if a.id == to_id), None)
    if not from_acc or not to_acc:
        await callback.answer("Счёт не найден.")
        return

    await state.update_data(
        transfer_from_id=from_id, transfer_to_id=to_id, acc_user_id=user_id
    )
    await get_message(callback).edit_text(
        f"↔️ <b>{html.escape(from_acc.name)} → {html.escape(to_acc.name)}</b>\n\nВведите сумму перевода:",
        parse_mode="HTML",
        reply_markup=acc_back_keyboard(),
    )
    await state.set_state(AccountStates.waiting_for_transfer_amount)
    await callback.answer()


@router.message(AccountStates.waiting_for_transfer_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при выполнении перевода")
async def handle_transfer_amount(message: Message, state: FSMContext, **kwargs) -> None:
    """Выполняет перевод между счетами."""
    try:
        amount = Decimal((message.text or "").strip().replace(",", "."))
        if amount <= 0 or amount > Decimal(str(MAX_AMOUNT)):
            raise ValueError
    except InvalidOperation, ValueError:
        await message.answer(
            f"Некорректная сумма. Введите число от 0.01 до {MAX_AMOUNT:,}:".replace(
                ",", " "
            )
        )
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)
    from_id = data.get("transfer_from_id")
    assert isinstance(from_id, int)
    to_id = data.get("transfer_to_id")
    assert isinstance(to_id, int)

    async with async_session() as session:
        balance = await get_account_balance(session, from_id, user_id)
        if amount > balance:
            await message.answer(
                f"Недостаточно средств. Баланс счёта: {balance:,.0f} ₽".replace(
                    ",", " "
                )
            )
            return

        ok = await create_transfer(session, user_id, from_id, to_id, amount)
        if not ok:
            await message.answer(
                "Не удалось выполнить перевод.", reply_markup=main_menu_keyboard()
            )
            await state.clear()
            return

        accounts = await get_accounts(session, user_id)
        from_name = next((a.name for a in accounts if a.id == from_id), "—")
        to_name = next((a.name for a in accounts if a.id == to_id), "—")
        balances = await get_account_balances(session, user_id)
        await session.commit()

    amount_str = f"{amount:,.0f}₽".replace(",", " ")
    await message.answer(
        f"✅ Перевод выполнен!\n{html.escape(from_name)} → {html.escape(to_name)}: <b>{amount_str}</b>\n\n"
        + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()


# --- Переводы: история и отмена ---


def _format_amount(amount: Decimal) -> str:
    """5000 → '5 000 ₽'."""
    return f"{amount:,.0f} ₽".replace(",", " ")


async def _render_transfers_list(
    callback: CallbackQuery, state: FSMContext, user_id: int, page: int
) -> None:
    """Рендерит страницу списка переводов."""
    async with async_session() as session:
        total = await count_transfers(session, user_id)
        if total == 0:
            await get_message(callback).edit_text(
                "🔁 <b>Переводы</b>\n\nПереводов пока нет.",
                reply_markup=acc_back_keyboard(),
                parse_mode="HTML",
            )
            await state.set_state(AccountStates.waiting_for_transfers_page)
            await state.update_data(acc_user_id=user_id, tr_total_pages=0)
            return

        total_pages = (total + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        transfers = await get_transfers(
            session, user_id, limit=RECORDS_PER_PAGE, offset=page * RECORDS_PER_PAGE
        )

    await state.set_state(AccountStates.waiting_for_transfers_page)
    await state.update_data(
        acc_user_id=user_id, tr_page=page, tr_total_pages=total_pages
    )
    await get_message(callback).edit_text(
        f"🔁 <b>Переводы</b> ({total})\n\nТап по переводу — открыть и отменить.",
        reply_markup=transfers_list_keyboard(transfers, page, total_pages),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "acc_transfers")
@log_exceptions("Ошибка при открытии переводов")
async def handle_acc_transfers(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Открывает список переводов (первая страница)."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _render_transfers_list(callback, state, user_id, 0)
    await callback.answer()


@router.callback_query(
    AccountStates.waiting_for_transfers_page, F.data.startswith("acc_tr_page:")
)
@log_exceptions("Ошибка при навигации по переводам")
async def handle_acc_tr_page(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Навигация по страницам списка переводов."""
    try:
        page_str = (callback.data or "").split(":")[1]
        if page_str == "noop":
            await callback.answer()
            return
        new_page = int(page_str)
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _render_transfers_list(callback, state, user_id, new_page)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_tr_view:"))
@log_exceptions("Ошибка при открытии перевода")
async def handle_acc_tr_view(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает карточку перевода с кнопкой отмены."""
    try:
        transfer_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        transfer = await get_transfer(session, user_id, transfer_id)

    if not transfer:
        await callback.answer("Перевод не найден.", show_alert=True)
        await _render_transfers_list(callback, state, user_id, 0)
        return

    date_str = transfer["date"].strftime("%d.%m.%Y %H:%M")
    from_name = html.escape(transfer["from_name"] or "(удалён)")
    to_name = html.escape(transfer["to_name"] or "(удалён)")
    await get_message(callback).edit_text(
        f"🔁 <b>Перевод</b>\n\n"
        f"📅 {date_str}\n"
        f"{from_name} → {to_name}\n"
        f"Сумма: <b>{_format_amount(transfer['amount'])}</b>",
        reply_markup=transfer_card_keyboard(transfer_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acc_tr_cancel:"))
@log_exceptions("Ошибка при запросе отмены перевода")
async def handle_acc_tr_cancel(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Запрашивает подтверждение отмены перевода."""
    try:
        transfer_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        transfer = await get_transfer(session, user_id, transfer_id)

    if not transfer:
        await callback.answer("Перевод не найден.", show_alert=True)
        await _render_transfers_list(callback, state, user_id, 0)
        return

    from_name = html.escape(transfer["from_name"] or "(удалён)")
    to_name = html.escape(transfer["to_name"] or "(удалён)")
    await get_message(callback).edit_text(
        f"Отменить перевод {from_name} → "
        f"{to_name} на <b>{_format_amount(transfer['amount'])}</b>?\n\n"
        "Балансы счетов вернутся к прежним.",
        reply_markup=confirm_transfer_cancel_keyboard(transfer_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("acc_tr_del:"))
@log_exceptions("Ошибка при отмене перевода")
async def handle_acc_tr_del(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет обе записи перевода и возвращает к списку."""
    try:
        transfer_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        ok = await cancel_transfer(session, user_id, transfer_id)
        if ok:
            await session.commit()

    if not ok:
        await callback.answer("Перевод уже отменён или не найден.", show_alert=True)
    else:
        await callback.answer("Перевод отменён.")
    await _render_transfers_list(callback, state, user_id, 0)
