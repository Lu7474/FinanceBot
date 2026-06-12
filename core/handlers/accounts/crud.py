"""Account CRUD (create/rename/delete/set balance), main screen, free-to-spend."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_ACCOUNT_NAME_LENGTH, MAX_AMOUNT
from core.database.models import async_session
from core.database.requests import (
    MAX_ACCOUNTS_PER_USER,
    create_account,
    delete_account,
    get_account_balance,
    get_account_balances,
    get_account_record_count,
    get_accounts,
    get_free_to_spend,
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
    main_menu_keyboard,
)
from core.utils import clean_text, format_money, log_exceptions

from ..common import (
    AccountStates,
    get_message,
    get_user_id_from_event,
    is_accounts,
    is_main_menu_button,
)
from .common import _back_to_accounts, _build_accounts_text

router = Router()


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
        fts = await get_free_to_spend(session, user_id, balances=balances)

    await message.answer(
        _build_accounts_text(balances, free=fts.free),
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


# --- Сколько могу потратить (свободные деньги) ---


def _plural_payments(n: int) -> str:
    """Russian plural for «платёж» (1 платёж / 2 платежа / 5 платежей)."""
    if 11 <= n % 100 <= 14:
        return "платежей"
    last = n % 10
    if last == 1:
        return "платёж"
    if 2 <= last <= 4:
        return "платежа"
    return "платежей"


def _build_free_to_spend_text(fts) -> str:
    """Экран «Сколько могу потратить» с разбивкой — объясняет, откуда число."""
    head = f"💸 <b>Свободно:  {format_money(fts.free)}</b>"
    if fts.free < 0:
        head += "\n⚠️ Перерасход — обязательства больше доступных денег."

    lines = [head, "", "<b>Откуда число:</b>"]
    lines.append(f"  Баланс счетов:  {format_money(fts.total_balance)}")
    if fts.earmark > 0:
        lines.append(f"  − Отложено в цели:  {format_money(fts.earmark)}")
    if fts.upcoming_payments > 0:
        lines.append(
            f"  − Платежи до конца месяца:  {format_money(fts.upcoming_payments)}"
        )
    if fts.payments_no_amount > 0:
        n = fts.payments_no_amount
        lines.append(
            f"    (+ {n} {_plural_payments(n)} без точной суммы — не учтены)"
        )
    if fts.earmark == 0 and fts.upcoming_payments == 0:
        lines.append("\nНет отложенного в цели и платежей — весь баланс свободен.")
    else:
        lines.append("\nСвободно = баланс − отложенное в цели − платежи месяца.")
    return "\n".join(lines)


@router.callback_query(F.data == "acc_free")
@log_exceptions("Ошибка при расчёте свободных денег")
async def handle_acc_free(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает экран «Сколько могу потратить» с разбивкой."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer()
        return
    async with async_session() as session:
        fts = await get_free_to_spend(session, user_id)
    await get_message(callback).edit_text(
        _build_free_to_spend_text(fts),
        reply_markup=acc_back_keyboard(),
        parse_mode="HTML",
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
