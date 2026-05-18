"""Handlers for account management, account history, and balance setting."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_ACCOUNT_NAME_LENGTH, MAX_AMOUNT, RECORDS_PER_PAGE
from core.database.models import async_session
from core.database.requests import (
    MAX_ACCOUNTS_PER_USER,
    create_account,
    create_transfer,
    delete_account,
    get_account_balance,
    get_account_balances,
    get_account_record_count,
    get_accounts,
    get_history_data,
    get_records,
    move_and_delete_account,
    rename_account,
    set_account_balance,
)
from core.keyboards import (
    acc_back_keyboard,
    account_delete_move_keyboard,
    account_manage_keyboard,
    accounts_menu_keyboard,
    confirm_account_delete_keyboard,
    history_period_keyboard,
    main_menu_keyboard,
)
from core.utils import log_exceptions

from .common import (
    AccountStates,
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
    await callback.message.edit_text(
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
    name = message.text.strip()
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

    await callback.message.edit_text(
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
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        acc = next((a for a in accounts if a.id == account_id), None)
        if not acc:
            await callback.answer("Счёт не найден.", show_alert=True)
            return
        current_name = acc.name

    await state.update_data(rename_account_id=account_id, acc_user_id=user_id)
    await callback.message.edit_text(
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
    new_name = message.text.strip()
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
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)

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

    await callback.message.edit_text(
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
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        account = next((a for a in accounts if a.id == account_id), None)
        if not account:
            await callback.answer("Счёт не найден.")
            return
        record_count = await get_account_record_count(session, account_id)

    targets = [a for a in accounts if a.id != account_id]

    if record_count > 0:
        await callback.message.edit_text(
            f"⚠️ Счёт <b>«{html.escape(account.name)}»</b> содержит {record_count} записей.\n"
            f"Куда перенести записи?",
            reply_markup=account_delete_move_keyboard(account_id, targets),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
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
        parts = callback.data.split(":")
        from_id = int(parts[1])
        to_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        ok = await move_and_delete_account(session, from_id, user_id, to_id)
        if not ok:
            await callback.answer("Не удалось удалить счёт.", show_alert=True)
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)
        await session.commit()

    await callback.message.edit_text(
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
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        ok = await delete_account(session, account_id, user_id)
        if not ok:
            await callback.answer("Счёт не найден или уже удалён.", show_alert=True)
            await state.clear()
            return
        balances = await get_account_balances(session, user_id)
        await session.commit()

    await callback.message.edit_text(
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
        await callback.message.edit_text(
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

    await callback.message.edit_text(
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
        from_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    from_acc = next((a for a in accounts if a.id == from_id), None)
    if not from_acc:
        await callback.answer("Счёт не найден.")
        return

    destinations = [a for a in accounts if a.id != from_id]
    await state.update_data(transfer_from_id=from_id, acc_user_id=user_id)
    await callback.message.edit_text(
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
        parts = callback.data.split(":")
        from_id = int(parts[1])
        to_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

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
    await callback.message.edit_text(
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
        amount = Decimal(message.text.strip().replace(",", "."))
        if amount <= 0 or amount > Decimal(str(MAX_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введите число от 0.01 до {MAX_AMOUNT:,}:".replace(
                ",", " "
            )
        )
        return

    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)
    from_id = data.get("transfer_from_id")
    to_id = data.get("transfer_to_id")

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


# --- История счёта ---


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
    await callback.message.edit_text(
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
        account_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.")
        return
    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

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
    await callback.message.edit_text(
        f"📋 <b>{html.escape(acc.name)}</b> — {balance_str}\n\nЗа какой период показать историю?",
        reply_markup=history_period_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(AccountStates.waiting_for_acc_hist_period)
    await callback.answer()


@router.callback_query(AccountStates.waiting_for_acc_hist_period)
@log_exceptions("Ошибка при получении истории счёта")
async def handle_acc_hist_period(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Загружает первую страницу истории выбранного счёта."""
    try:
        period = callback.data.split(":")[1]
    except (IndexError, AttributeError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    account_id = data.get("acc_hist_account_id")
    acc_name = data.get("acc_hist_account_name", "Счёт")
    acc_balance = data.get("acc_hist_balance", "")

    user_id = kwargs["user_id"]
    async with async_session() as session:
        total_count, income_sum, expense_sum, records = await get_history_data(
            session,
            user_id,
            period,
            limit=RECORDS_PER_PAGE,
            offset=0,
            account_id=account_id,
            include_transfers=True,
        )

    if total_count == 0:
        await callback.message.edit_text(
            f"📋 <b>{acc_name}</b>\nЗаписей за указанный период нет.",
            parse_mode="HTML",
        )
        await state.clear()
        await callback.answer()
        return

    total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
    await state.update_data(
        acc_hist_period=period,
        acc_hist_page=0,
        acc_hist_total_pages=total_pages,
        acc_hist_total_count=total_count,
        acc_hist_income=str(income_sum),
        acc_hist_expense=str(expense_sum),
    )

    header = f"📋 <b>{html.escape(acc_name)}</b> — {html.escape(acc_balance)}"
    text, kb = build_history_page(
        records,
        0,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        total_count=total_count,
        header=header,
    )
    if total_pages > 1:
        await callback.message.edit_text(
            text, reply_markup=kb.as_markup(), parse_mode="HTML"
        )
        await state.set_state(AccountStates.waiting_for_acc_hist_page)
    else:
        await callback.message.edit_text(text, parse_mode="HTML")
        await state.clear()
    await callback.answer()


@router.callback_query(
    AccountStates.waiting_for_acc_hist_page, F.data.startswith("hist_page:")
)
@log_exceptions("Ошибка при навигации по истории счёта")
async def handle_acc_hist_page(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Навигация по страницам истории счёта."""
    try:
        page_str = callback.data.split(":")[1]
        if page_str == "noop":
            await callback.answer()
            return
        new_page = int(page_str)
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    account_id = data.get("acc_hist_account_id")
    acc_name = data.get("acc_hist_account_name", "Счёт")
    acc_balance = data.get("acc_hist_balance", "")
    period = data.get("acc_hist_period")
    total_pages = data.get("acc_hist_total_pages", 1)
    total_count = data.get("acc_hist_total_count", 0)
    income_sum = Decimal(data.get("acc_hist_income", "0"))
    expense_sum = Decimal(data.get("acc_hist_expense", "0"))

    if new_page < 0 or new_page >= total_pages:
        await callback.answer("Страница не существует.")
        return

    user_id = kwargs["user_id"]
    async with async_session() as session:
        records = await get_records(
            session,
            user_id,
            period,
            limit=RECORDS_PER_PAGE,
            offset=new_page * RECORDS_PER_PAGE,
            account_id=account_id,
            include_transfers=True,
        )

    await state.update_data(acc_hist_page=new_page)
    header = f"📋 <b>{html.escape(acc_name)}</b> — {html.escape(acc_balance)}"
    text, kb = build_history_page(
        records,
        new_page,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        total_count=total_count,
        header=header,
    )
    await callback.message.edit_text(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
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
    await callback.message.edit_text(
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
        account_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Некорректные данные.")
        return
    data = await state.get_data()
    user_id = data.get("acc_user_id") or await get_user_id_from_event(callback, kwargs)

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        acc = next((a for a in accounts if a.id == account_id), None)
        if not acc:
            await callback.answer("Счёт не найден.")
            return
        current = await get_account_balance(session, account_id, user_id)

    cur_str = f"{current:,.0f}".replace(",", " ")
    await state.update_data(set_balance_account_id=account_id, acc_user_id=user_id)
    await callback.message.edit_text(
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
        desired = Decimal(message.text.strip().replace(",", "."))
        if desired < 0 or desired > Decimal(str(MAX_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введите число от 0 до {MAX_AMOUNT:,}:".replace(
                ",", " "
            )
        )
        return

    data = await state.get_data()
    account_id = data.get("set_balance_account_id")
    user_id = data.get("acc_user_id") or await get_user_id_from_event(message, kwargs)

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
