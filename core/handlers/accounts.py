"""Handlers for account management, account history, and balance setting."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from config import MAX_ACCOUNT_NAME_LENGTH, MAX_AMOUNT, RECORDS_PER_PAGE
from core.database.models import Account, Record, async_session
from core.database.requests import (
    MAX_ACCOUNTS_PER_USER,
    cancel_transfer,
    count_transfers,
    create_account,
    create_transfer,
    delete_account,
    get_account_balance,
    get_account_balances,
    get_account_record_count,
    get_accounts,
    get_history_data,
    get_transfer,
    get_transfers,
    move_and_delete_account,
    rename_account,
    set_account_balance,
)
from core.keyboards import (
    acc_back_keyboard,
    account_delete_move_keyboard,
    account_history_keyboard,
    account_manage_keyboard,
    accounts_menu_keyboard,
    confirm_account_delete_keyboard,
    confirm_transfer_cancel_keyboard,
    history_period_keyboard,
    main_menu_keyboard,
    transfer_card_keyboard,
    transfers_list_keyboard,
)
from core.utils import clean_text, log_exceptions

from .common import (
    AccountStates,
    get_message,
    get_user_id_from_event,
    is_accounts,
    is_main_menu_button,
)
from .history import build_history_page

router = Router()


def _build_accounts_text(balances: list[tuple]) -> str:
    """Формирует текст с балансами по счетам."""
    if not balances:
        return "💳 <b>Мои счета</b>\n\nСчетов нет. Нажмите ➕ Создать."

    lines = ["💳 <b>Мои счета</b>\n"]
    total = Decimal("0")
    for acc, balance in balances:
        sign = "-" if balance < 0 else ""
        formatted = f"{sign}{abs(balance):,.0f}₽".replace(",", " ")
        lines.append(f"<b>{html.escape(acc.name)}</b>  —  {formatted}")
        total += balance

    sign = "-" if total < 0 else ""
    total_str = f"{sign}{abs(total):,.0f}₽".replace(",", " ")
    lines.append(f"\n<b>Всего:  {total_str}</b>")
    return "\n".join(lines)


@router.message(StateFilter("*"), F.func(is_accounts))
@log_exceptions("Ошибка при отображении счетов")
async def handle_accounts(message: Message, state: FSMContext, **kwargs) -> None:
    """Показывает балансы по счетам и меню управления."""
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return

    async with async_session() as session:
        balances = await get_account_balances(session, user_id)

    await message.answer(
        _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )


# --- Создать счёт ---


@router.callback_query(F.data == "acc_create")
@log_exceptions("Ошибка при создании счёта")
async def handle_acc_create(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Запрашивает название нового счёта."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if len(accounts) >= MAX_ACCOUNTS_PER_USER:
        await callback.answer(
            f"Нельзя создать более {MAX_ACCOUNTS_PER_USER} счетов.", show_alert=True
        )
        return

    await state.update_data(acc_user_id=user_id)
    await get_message(callback).edit_text(
        f"Введите название нового счёта (до {MAX_ACCOUNT_NAME_LENGTH} символов):",
        reply_markup=acc_back_keyboard(),
    )
    await state.set_state(AccountStates.waiting_for_account_name)
    await callback.answer()


@router.message(AccountStates.waiting_for_account_name, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении названия счёта")
async def handle_new_account_name(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Создаёт новый счёт с введённым названием."""
    name = clean_text(message.text or "")
    if not name:
        await message.answer("Название не может быть пустым. Введите снова:")
        return
    if len(name) > MAX_ACCOUNT_NAME_LENGTH:
        await message.answer(
            f"Название слишком длинное. Максимум {MAX_ACCOUNT_NAME_LENGTH} символов. Введите название снова:"
        )
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        acc = await create_account(session, user_id, name)
        if acc is None:
            accounts = await get_accounts(session, user_id)
            if len(accounts) >= MAX_ACCOUNTS_PER_USER:
                await message.answer(
                    f"Нельзя создать более {MAX_ACCOUNTS_PER_USER} счетов.",
                    reply_markup=main_menu_keyboard(),
                )
            else:
                await message.answer(
                    f"Счёт с названием «{name}» уже существует. Введите другое название:"
                )
                return
        else:
            balances = await get_account_balances(session, user_id)
            await session.commit()
            await message.answer(
                f"✅ Счёт «{html.escape(acc.name)}» создан!\n\n"
                + _build_accounts_text(balances),
                reply_markup=accounts_menu_keyboard(),
                parse_mode="HTML",
            )

    await state.clear()


# --- Переименовать счёт ---


@router.callback_query(F.data == "acc_rename")
@log_exceptions("Ошибка при переименовании счёта")
async def handle_acc_rename(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список счетов для выбора переименования."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        await callback.answer("Нет счетов для переименования.", show_alert=True)
        return

    await get_message(callback).edit_text(
        "Выберите счёт для переименования:",
        reply_markup=account_manage_keyboard(accounts, "rename_select"),
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_rename_select:"))
@log_exceptions("Ошибка при выборе счёта для переименования")
async def handle_acc_rename_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет выбранный счёт и запрашивает новое название."""
    try:
        account_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        acc = next((a for a in accounts if a.id == account_id), None)
        if not acc:
            await callback.answer("Счёт не найден.", show_alert=True)
            return
        current_name = acc.name

    await state.update_data(rename_account_id=account_id, acc_user_id=user_id)
    await get_message(callback).edit_text(
        f"Текущее название: <code>{html.escape(current_name)}</code>\n\n"
        f"Введите новое название счёта (до {MAX_ACCOUNT_NAME_LENGTH} символов):",
        parse_mode="HTML",
        reply_markup=acc_back_keyboard(),
    )
    await state.set_state(AccountStates.waiting_for_rename_name)
    await callback.answer()


@router.message(AccountStates.waiting_for_rename_name, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при применении нового названия")
async def handle_rename_name(message: Message, state: FSMContext, **kwargs) -> None:
    """Переименовывает счёт."""
    new_name = clean_text(message.text or "")
    if not new_name:
        await message.answer("Название не может быть пустым. Введите снова:")
        return
    if len(new_name) > MAX_ACCOUNT_NAME_LENGTH:
        await message.answer(
            f"Название слишком длинное. Максимум {MAX_ACCOUNT_NAME_LENGTH} символов. Введите название снова:"
        )
        return

    data = await state.get_data()
    account_id = data.get("rename_account_id")
    assert isinstance(account_id, int)
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        ok = await rename_account(session, account_id, user_id, new_name)
        if not ok:
            await message.answer(
                f"Счёт с названием «{new_name}» уже существует. Введите другое название:"
            )
            return

        balances = await get_account_balances(session, user_id)
        await session.commit()

    await message.answer(
        f"✅ Счёт переименован в «{html.escape(new_name)}»!\n\n"
        + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()


# --- Удалить счёт ---


@router.callback_query(F.data == "acc_delete")
@log_exceptions("Ошибка при удалении счёта")
async def handle_acc_delete(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список счетов для удаления."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        await callback.answer("Нет счетов для удаления.", show_alert=True)
        return

    if len(accounts) == 1:
        await callback.answer("Нельзя удалить последний счёт.", show_alert=True)
        return

    await get_message(callback).edit_text(
        "Выберите счёт для удаления:",
        reply_markup=account_manage_keyboard(accounts, "delete_select"),
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete_select:"))
@log_exceptions("Ошибка при выборе счёта для удаления")
async def handle_acc_delete_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Если есть записи — предлагает выбрать счёт для переноса. Иначе — простое подтверждение."""
    try:
        account_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        account = next((a for a in accounts if a.id == account_id), None)
        if not account:
            await callback.answer("Счёт не найден.")
            return
        record_count = await get_account_record_count(session, account_id)

    targets = [a for a in accounts if a.id != account_id]

    if record_count > 0:
        await get_message(callback).edit_text(
            f"⚠️ Счёт <b>«{html.escape(account.name)}»</b> содержит {record_count} записей.\n"
            f"Куда перенести записи?",
            reply_markup=account_delete_move_keyboard(account_id, targets),
            parse_mode="HTML",
        )
    else:
        await get_message(callback).edit_text(
            f"Удалить счёт <b>«{html.escape(account.name)}»</b>?",
            reply_markup=confirm_account_delete_keyboard(account_id),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete_move:"))
@log_exceptions("Ошибка при переносе записей и удалении счёта")
async def handle_acc_delete_move(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Переносит записи на выбранный счёт и удаляет исходный."""
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
        ok = await move_and_delete_account(session, from_id, user_id, to_id)
        if not ok:
            await callback.answer("Не удалось удалить счёт.", show_alert=True)
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)
        await session.commit()

    await get_message(callback).edit_text(
        "✅ Записи перенесены, счёт удалён.\n\n" + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete_confirm:"))
@log_exceptions("Ошибка при подтверждении удаления счёта")
async def handle_acc_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет счёт и обновляет список."""
    try:
        account_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        ok = await delete_account(session, account_id, user_id)
        if not ok:
            await callback.answer("Счёт не найден или уже удалён.", show_alert=True)
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)
        await session.commit()

    await get_message(callback).edit_text(
        "✅ Счёт удалён.\n\n" + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "acc_delete_cancel")
async def handle_acc_delete_cancel(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Legacy: отмена удаления счёта — возврат к списку балансов (для старых сообщений)."""
    await _back_to_accounts(callback, state, kwargs)


@router.callback_query(F.data == "acc_back")
@log_exceptions("Ошибка при возврате к списку счетов")
async def handle_acc_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Возврат в раздел Счета из любого подсценария."""
    await _back_to_accounts(callback, state, kwargs)


async def _back_to_accounts(
    callback: CallbackQuery, state: FSMContext, kwargs: dict
) -> None:
    """Рендерит балансы + меню Счетов, чистит state."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if user_id:
        async with async_session() as session:
            balances = await get_account_balances(session, user_id)
        await get_message(callback).edit_text(
            _build_accounts_text(balances),
            reply_markup=accounts_menu_keyboard(),
            parse_mode="HTML",
        )
    await state.clear()
    await callback.answer()


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


# --- История счёта ---


async def _annotate_transfer_direction(
    session, records: list, account_id: int | None
) -> None:
    """Дописывает направление переводам в истории счёта.

    Для записей с transfer_id подменяет category на «Перевод → {назначение}»
    (расход) или «Перевод ← {источник}» (доход). Правка только в памяти, на
    рендер — в БД не пишется. Легаси-переводы без transfer_id и переводы на
    удалённый счёт остаются как «Перевод».
    """
    tids = list({r.transfer_id for r in records if r.transfer_id})
    if not tids:
        return
    # NB: `account_id != account_id` в SQL даёт NULL (не TRUE) для записей с
    # account_id IS NULL — поэтому пара на удалённый счёт сюда не попадёт и
    # перевод останется без подписи направления. Это ожидаемо.
    rows = await session.execute(
        select(Record.transfer_id, Account.name)
        .outerjoin(Account, Record.account_id == Account.id)
        .where(Record.transfer_id.in_(tids), Record.account_id != account_id)
    )
    other = {tid: name for tid, name in rows.fetchall() if name}
    for r in records:
        if r.transfer_id in other:
            arrow = "→" if r.operation == "-" else "←"
            r.category = f"Перевод {arrow} {other[r.transfer_id]}"
    # Detach: правка category — только для рендера, исключаем случайный flush в БД.
    session.expunge_all()


@router.callback_query(F.data == "acc_history")
@log_exceptions("Ошибка при открытии истории счёта")
async def handle_acc_history(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список счетов для выбора истории."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        return
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
    await get_message(callback).edit_text(
        "📋 <b>История по счёту</b>\n\nВыберите счёт:",
        reply_markup=account_manage_keyboard(accounts, "history_select"),
        parse_mode="HTML",
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_history_select:"))
@log_exceptions("Ошибка при выборе счёта для истории")
async def handle_acc_history_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет выбранный счёт и показывает выбор периода."""
    try:
        account_id = int((callback.data or "").split(":")[1])
    except ValueError, IndexError:
        await callback.answer("Некорректные данные.")
        return
    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        acc = next((a for a in accounts if a.id == account_id), None)
        if not acc:
            await callback.answer("Счёт не найден.")
            return
        balance = await get_account_balance(session, account_id, user_id)

    balance_str = f"{balance:,.0f} ₽".replace(",", " ")
    await state.update_data(
        acc_hist_account_id=account_id,
        acc_hist_account_name=acc.name,
        acc_hist_balance=balance_str,
        acc_user_id=user_id,
    )
    await get_message(callback).edit_text(
        f"📋 <b>{html.escape(acc.name)}</b> — {balance_str}\n\nЗа какой период показать историю?",
        reply_markup=history_period_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(AccountStates.waiting_for_acc_hist_period)
    await callback.answer()


_HIST_FILTER_OP = {"all": None, "expense": "-", "income": "+"}


async def _render_acc_history(
    callback: CallbackQuery,
    state: FSMContext,
    user_id: int,
    period: str,
    page: int,
    op_filter: str | None,
) -> None:
    """Рендерит страницу истории счёта: текст + своя клавиатура (пагинация,
    фильтр по типу, выход). Источник истины по счёту берётся из state."""
    data = await state.get_data()
    account_id = data.get("acc_hist_account_id")
    if not isinstance(account_id, int):
        await callback.answer(
            "Данные устарели. Откройте историю заново.", show_alert=True
        )
        return
    acc_name = data.get("acc_hist_account_name", "Счёт")
    acc_balance = data.get("acc_hist_balance", "")
    header = f"📋 <b>{html.escape(acc_name)}</b> — {html.escape(acc_balance)}"

    async with async_session() as session:
        total_count, income_sum, expense_sum, records = await get_history_data(
            session,
            user_id,
            period,
            limit=RECORDS_PER_PAGE,
            offset=page * RECORDS_PER_PAGE,
            account_id=account_id,
            include_transfers=True,
            operation_filter=op_filter,
        )
        await _annotate_transfer_direction(session, records, account_id)

    if total_count == 0:
        # Пусто — но оставляем клавиатуру, чтобы можно было сменить фильтр/выйти.
        note = {"-": " (фильтр: Расходы)", "+": " (фильтр: Доходы)"}.get(
            op_filter or "", ""
        )
        text = f"{header}\n\nЗаписей за период нет{note}."
        total_pages = 1
        page = 0
    else:
        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        text, _ = build_history_page(
            records,
            page,
            total_pages,
            income_sum,
            expense_sum,
            period=period,
            total_count=total_count,
            header=header,
            operation_filter=op_filter,
        )

    await state.update_data(
        acc_hist_period=period,
        acc_hist_page=page,
        acc_hist_total_pages=total_pages,
        acc_hist_filter=op_filter or "",
    )
    await state.set_state(AccountStates.waiting_for_acc_hist_page)
    await get_message(callback).edit_text(
        text,
        reply_markup=account_history_keyboard(account_id, page, total_pages, op_filter),
        parse_mode="HTML",
    )


@router.callback_query(AccountStates.waiting_for_acc_hist_period)
@log_exceptions("Ошибка при получении истории счёта")
async def handle_acc_hist_period(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Выбран период — показываем первую страницу истории счёта (без фильтра)."""
    try:
        period = (callback.data or "").split(":")[1]
    except IndexError, AttributeError:
        await callback.answer("Некорректные данные.")
        return

    await _render_acc_history(
        callback, state, kwargs["user_id"], period, page=0, op_filter=None
    )
    await callback.answer()


@router.callback_query(
    AccountStates.waiting_for_acc_hist_page, F.data.startswith("acc_hist_page:")
)
@log_exceptions("Ошибка при навигации по истории счёта")
async def handle_acc_hist_page(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Навигация по страницам истории счёта."""
    try:
        page_str = (callback.data or "").split(":")[1]
        if page_str == "noop":
            await callback.answer()
            return
        new_page = int(page_str)
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    period = data.get("acc_hist_period")
    if not isinstance(period, str):
        await callback.answer(
            "Данные устарели. Откройте историю заново.", show_alert=True
        )
        return
    total_pages = data.get("acc_hist_total_pages", 1)
    op_filter = data.get("acc_hist_filter") or None

    if new_page < 0 or new_page >= total_pages:
        await callback.answer("Страница не существует.")
        return

    await _render_acc_history(
        callback, state, kwargs["user_id"], period, new_page, op_filter
    )
    await callback.answer()


@router.callback_query(
    AccountStates.waiting_for_acc_hist_page, F.data.startswith("acc_hist_filter:")
)
@log_exceptions("Ошибка при фильтрации истории счёта")
async def handle_acc_hist_filter(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Фильтр истории счёта по типу операции (all/expense/income)."""
    ftype = (callback.data or "").split(":")[1]
    if ftype not in _HIST_FILTER_OP:
        await callback.answer()
        return
    new_op = _HIST_FILTER_OP[ftype]

    data = await state.get_data()
    current_op = data.get("acc_hist_filter") or None
    if new_op == current_op:
        await callback.answer()
        return
    period = data.get("acc_hist_period")
    if not isinstance(period, str):
        await callback.answer(
            "Данные устарели. Откройте историю заново.", show_alert=True
        )
        return

    await _render_acc_history(
        callback, state, kwargs["user_id"], period, page=0, op_filter=new_op
    )
    await callback.answer()


# --- Установка баланса счёта ---


@router.callback_query(F.data == "acc_set_balance")
@log_exceptions("Ошибка при установке баланса")
async def handle_acc_set_balance(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список счетов для выбора."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        return
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
    await get_message(callback).edit_text(
        "💰 <b>Установить баланс</b>\n\nВыберите счёт:",
        reply_markup=account_manage_keyboard(accounts, "set_balance_select"),
        parse_mode="HTML",
    )
    await state.update_data(acc_user_id=user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_set_balance_select:"))
@log_exceptions("Ошибка при выборе счёта для установки баланса")
async def handle_acc_set_balance_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Запрашивает желаемый баланс для выбранного счёта."""
    try:
        account_id = int((callback.data or "").split(":")[1])
    except ValueError, IndexError:
        await callback.answer("Некорректные данные.")
        return
    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        acc = next((a for a in accounts if a.id == account_id), None)
        if not acc:
            await callback.answer("Счёт не найден.")
            return
        current = await get_account_balance(session, account_id, user_id)

    cur_str = f"{current:,.0f}".replace(",", " ")
    await state.update_data(set_balance_account_id=account_id, acc_user_id=user_id)
    await get_message(callback).edit_text(
        f"💰 <b>{html.escape(acc.name)}</b>\n"
        f"Текущий баланс: <code>{cur_str}</code>\n\n"
        "Введите новый баланс:",
        parse_mode="HTML",
        reply_markup=acc_back_keyboard(),
    )
    await state.set_state(AccountStates.waiting_for_set_balance)
    await callback.answer()


@router.message(AccountStates.waiting_for_set_balance, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении баланса")
async def handle_set_balance_amount(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Сохраняет желаемый баланс через balance_offset."""
    try:
        desired = Decimal((message.text or "").strip().replace(",", "."))
        if desired < 0 or desired > Decimal(str(MAX_AMOUNT)):
            raise ValueError
    except InvalidOperation, ValueError:
        await message.answer(
            f"Некорректная сумма. Введите число от 0 до {MAX_AMOUNT:,}:".replace(
                ",", " "
            )
        )
        return

    data = await state.get_data()
    account_id = data.get("set_balance_account_id")
    assert isinstance(account_id, int)
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        ok = await set_account_balance(session, account_id, desired, user_id)
        if not ok:
            await message.answer(
                "Не удалось установить баланс.", reply_markup=main_menu_keyboard()
            )
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)
        accounts = await get_accounts(session, user_id)
        acc_name = next((a.name for a in accounts if a.id == account_id), "—")
        await session.commit()

    await state.clear()
    desired_str = f"{desired:,.0f} ₽".replace(",", " ")
    await message.answer(
        f"✅ Баланс <b>{html.escape(acc_name)}</b> установлен: <b>{desired_str}</b>\n\n"
        + _build_accounts_text(balances),
        reply_markup=accounts_menu_keyboard(),
        parse_mode="HTML",
    )
