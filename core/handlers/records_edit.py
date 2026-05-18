"""Handlers for viewing and editing individual records from history."""

import html
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_AMOUNT, MAX_CATEGORY_LENGTH, RECORDS_PER_PAGE, TIMEZONE
from core.database.models import async_session
from core.database.requests import (
    SYSTEM_CATEGORIES,
    delete_record,
    get_accounts,
    get_history_data,
    get_record_by_id,
    get_records,
    update_record,
)
from core.keyboards import (
    history_record_select_keyboard,
    record_account_select_keyboard,
    record_delete_confirm_keyboard,
    record_detail_keyboard,
    record_edit_field_keyboard,
)
from core.utils import (
    format_record_card,
    log_exceptions,
    normalize_category,
    parse_edit_amount,
    parse_edit_date,
)

from .common import RecordEditStates, is_main_menu_button
from .history import build_history_page

router = Router()


# ==================== Helpers ====================


async def _show_record_card(
    callback: CallbackQuery, record_id: int, user_id: int
) -> bool:
    """Edit current message to show record card. Returns False if record not found."""
    async with async_session() as session:
        record = await get_record_by_id(session, record_id, user_id)
    if not record:
        await callback.message.edit_text("Запись не найдена или уже удалена.")
        return False
    await callback.message.edit_text(
        format_record_card(record),
        reply_markup=record_detail_keyboard(record_id),
        parse_mode="HTML",
    )
    return True


async def _return_to_history(
    callback: CallbackQuery, state: FSMContext, user_id: int
) -> None:
    """Reload history page from DB and display it. Falls back to period selection."""
    from core.keyboards import history_period_keyboard

    data = await state.get_data()
    period = data.get("history_period")
    if not period:
        await state.clear()
        await callback.message.edit_text(
            "За какой период показать историю?",
            reply_markup=history_period_keyboard(),
        )
        return

    page = data.get("history_page", 0)
    period_label = data.get("history_period_label", "")
    history_filter = data.get("history_filter", {})
    operation_filter = history_filter.get("operation")
    category_filter = history_filter.get("category")
    date_from = None
    date_to = None
    if period == "range":
        df = data.get("history_date_from")
        dt = data.get("history_date_to")
        if df and dt:
            date_from = datetime.fromisoformat(df)
            date_to = datetime.fromisoformat(dt)

    async with async_session() as session:
        offset = page * RECORDS_PER_PAGE
        total_count, income_sum, expense_sum, records = await get_history_data(
            session,
            user_id,
            period,
            date_from,
            date_to,
            limit=RECORDS_PER_PAGE,
            offset=offset,
            operation_filter=operation_filter,
            category_filter=category_filter,
        )

    if total_count == 0:
        await state.clear()
        await callback.message.edit_text("Записей не найдено за указанный период.")
        return

    total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
    # Clamp page in case edited record caused the page to become empty
    page = min(page, total_pages - 1)
    if page != data.get("history_page", 0):
        offset = page * RECORDS_PER_PAGE
        async with async_session() as session:
            records = await get_records(
                session,
                user_id,
                period,
                date_from,
                date_to,
                limit=RECORDS_PER_PAGE,
                offset=offset,
                operation_filter=operation_filter,
                category_filter=category_filter,
            )

    await state.update_data(
        history_page=page,
        history_total_pages=total_pages,
        history_total_count=total_count,
        history_income=str(income_sum),
        history_expense=str(expense_sum),
    )

    from .common import MenuStates

    text, kb = build_history_page(
        records,
        page,
        total_pages,
        income_sum,
        expense_sum,
        period=period,
        period_label=period_label,
        total_count=total_count,
        operation_filter=operation_filter,
        category_filter=category_filter,
    )
    await state.set_state(MenuStates.waiting_for_history_page)
    await callback.message.edit_text(
        text, reply_markup=kb.as_markup(), parse_mode="HTML"
    )


# ==================== Open record from history ====================


@router.callback_query(F.data == "hist_open_record")
@log_exceptions("Ошибка при открытии списка записей")
async def hist_open_record(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Show selectable list of records on the current history page."""
    data = await state.get_data()
    period = data.get("history_period")
    if not period:
        await callback.answer("Данные истории устарели.", show_alert=True)
        return

    page = data.get("history_page", 0)
    history_filter = data.get("history_filter", {})
    operation_filter = history_filter.get("operation")
    category_filter = history_filter.get("category")
    date_from = None
    date_to = None
    if period == "range":
        df = data.get("history_date_from")
        dt = data.get("history_date_to")
        if df and dt:
            date_from = datetime.fromisoformat(df)
            date_to = datetime.fromisoformat(dt)

    user_id = kwargs["user_id"]
    async with async_session() as session:
        offset = page * RECORDS_PER_PAGE
        records = await get_records(
            session,
            user_id,
            period,
            date_from,
            date_to,
            limit=RECORDS_PER_PAGE,
            offset=offset,
            operation_filter=operation_filter,
            category_filter=category_filter,
        )

    if not records:
        await callback.answer("Записей не найдено.", show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите запись:",
        reply_markup=history_record_select_keyboard(records),
    )
    await callback.answer()


@router.callback_query(F.data == "hist_back_from_select")
@log_exceptions("Ошибка при возврате из выбора записи")
async def hist_back_from_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Return from record select list back to history page."""
    await _return_to_history(callback, state, kwargs["user_id"])
    await callback.answer()


# ==================== View record card ====================


@router.callback_query(F.data.startswith("record:view:"))
@log_exceptions("Ошибка при просмотре карточки записи")
async def record_view(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Show record detail card."""
    try:
        record_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    user_id = kwargs["user_id"]

    await _show_record_card(callback, record_id, user_id)
    await state.update_data(edit_record_id=record_id)
    await callback.answer()


# ==================== Edit: field selection ====================


@router.callback_query(F.data.startswith("record:edit:"))
@log_exceptions("Ошибка при выборе поля редактирования")
async def record_edit(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Show field selection keyboard."""
    try:
        record_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    user_id = kwargs["user_id"]

    async with async_session() as session:
        record = await get_record_by_id(session, record_id, user_id)
        if not record:
            await callback.message.edit_text("Запись не найдена или уже удалена.")
            await callback.answer()
            return
        accounts = await get_accounts(session, user_id)

    has_accounts = len(accounts) > 0
    await callback.message.edit_text(
        "Что изменить?",
        reply_markup=record_edit_field_keyboard(record_id, has_accounts),
    )
    await callback.answer()


# ==================== Edit: field value input ====================


@router.callback_query(F.data.startswith("record:field:"))
@log_exceptions("Ошибка при выборе поля")
async def record_field_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle field selection: for account — show account list; for others — ask for text."""
    parts = callback.data.split(":")
    try:
        record_id = int(parts[2])
        field = parts[3]
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    user_id = kwargs["user_id"]

    async with async_session() as session:
        record = await get_record_by_id(session, record_id, user_id)
        if not record:
            await callback.message.edit_text("Запись не найдена или уже удалена.")
            await callback.answer()
            return

        if field == "account":
            accounts = await get_accounts(session, user_id)
            if not accounts:
                await callback.answer("У вас нет счетов.", show_alert=True)
                return
            await callback.message.edit_text(
                "Выберите новый счёт:",
                reply_markup=record_account_select_keyboard(record_id, accounts),
            )
            await callback.answer()
            return

        # Extract values inside session — raw для <code>, чтобы при копировании не было лишних символов
        cur_amount_raw = f"{float(record.amount):.0f}"
        cur_category = record.category
        cur_date_raw = record.created_at.strftime("%d.%m.%Y")

    prompts = {
        "amount": f"Текущая сумма: <code>{cur_amount_raw}</code>\n\nВведите новую сумму:",
        "category": f"Текущая категория: <code>{html.escape(cur_category)}</code>\n\nВведите новую категорию:",
        "date": f"Текущая дата: <code>{cur_date_raw}</code>\n\nВведите новую дату в формате ДД.ММ или ДД.ММ.ГГ:",
    }
    prompt = prompts.get(field)
    if not prompt:
        await callback.answer("Неизвестное поле.")
        return

    await state.update_data(edit_record_id=record_id, edit_field=field)
    await state.set_state(RecordEditStates.waiting_for_record_edit_value)
    await callback.message.edit_text(prompt, parse_mode="HTML")
    await callback.answer()


@router.message(
    RecordEditStates.waiting_for_record_edit_value,
    ~F.func(is_main_menu_button),
)
@log_exceptions("Ошибка при сохранении нового значения")
async def record_edit_value(message: Message, state: FSMContext, **kwargs) -> None:
    """Process text input for amount / category / date editing."""
    data = await state.get_data()
    record_id = data.get("edit_record_id")
    field = data.get("edit_field")

    if not record_id or not field:
        await state.clear()
        return

    user_id = kwargs["user_id"]

    text = message.text.strip() if message.text else ""

    if field == "amount":
        value = parse_edit_amount(text)
        if value is None:
            await message.answer(
                f"Некорректная сумма. Введите число больше 0 и не больше {MAX_AMOUNT:,}.\n"
                "Форматы: 1500, 1 500, 1500,50"
            )
            return
        if value > MAX_AMOUNT:
            await message.answer(f"Сумма не может превышать {MAX_AMOUNT:,}₽.")
            return
        update_fields = {"amount": value}

    elif field == "category":
        if not text:
            await message.answer("Категория не может быть пустой.")
            return
        if len(text) > MAX_CATEGORY_LENGTH:
            await message.answer(
                f"Категория не может быть длиннее {MAX_CATEGORY_LENGTH} символов."
            )
            return
        normalized = normalize_category(text)
        if normalized in SYSTEM_CATEGORIES:
            await message.answer("Эта категория зарезервирована системой.")
            return
        update_fields = {"category": normalized}

    elif field == "date":
        value = parse_edit_date(text, TIMEZONE)
        if value is None:
            await message.answer(
                "Некорректная дата или дата в будущем.\nФорматы: 15.01 или 15.01.25"
            )
            return
        update_fields = {"created_at": value}

    else:
        await state.clear()
        return

    no_change = False
    confirm_text = ""
    updated_card = ""

    async with async_session() as session:
        old_record = await get_record_by_id(session, record_id, user_id)
        if not old_record:
            await message.answer("Запись не найдена или уже удалена.")
            await state.clear()
            return

        # Capture old values before any commit
        old_amount = old_record.amount
        old_category = old_record.category
        old_date_str = old_record.created_at.strftime("%d.%m.%Y")

        # Detect no-op
        if field == "amount" and old_amount == update_fields["amount"]:
            no_change = True
        elif field == "category" and old_category == update_fields["category"]:
            no_change = True
        elif field == "date":
            new_date_str = update_fields["created_at"].strftime("%d.%m.%Y")
            no_change = old_date_str == new_date_str
        else:
            no_change = False

        if no_change:
            updated_card = format_record_card(old_record)
        else:
            updated = await update_record(session, record_id, user_id, **update_fields)
            if not updated:
                await message.answer("Не удалось сохранить изменение.")
                await state.clear()
                return
            updated_card = format_record_card(updated)
            new_amount = updated.amount
            new_category = updated.category
            new_date_str = updated.created_at.strftime("%d.%m.%Y")
            await session.commit()

    # Build confirmation message (all values captured inside session)
    if no_change:
        confirm_text = "Значение не изменилось."
    elif field == "amount":
        old_amt = f"{float(old_amount):,.0f}₽".replace(",", " ")
        new_amt = f"{float(new_amount):,.0f}₽".replace(",", " ")
        confirm_text = f"✅ Сохранено\nСумма: {old_amt} → {new_amt}"
    elif field == "category":
        confirm_text = (
            f"✅ Сохранено\n"
            f"Категория: {html.escape(old_category or '')} → {html.escape(new_category or '')}"
        )
    else:
        confirm_text = f"✅ Сохранено\nДата: {old_date_str} → {new_date_str}"

    from core.keyboards import record_detail_keyboard as _rdk

    await message.answer(
        confirm_text + "\n\n" + updated_card,
        reply_markup=_rdk(record_id),
        parse_mode="HTML",
    )

    # Keep history context, clear only edit fields
    await state.update_data(edit_record_id=None, edit_field=None)
    await state.set_state(None)


# ==================== Edit: account selection ====================


@router.callback_query(F.data.startswith("record:account:"))
@log_exceptions("Ошибка при смене счёта")
async def record_account_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Save new account_id for the record."""
    parts = callback.data.split(":")
    try:
        record_id = int(parts[2])
        account_id = int(parts[3])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    user_id = kwargs["user_id"]

    async with async_session() as session:
        old_record = await get_record_by_id(session, record_id, user_id)
        if not old_record:
            await callback.message.edit_text("Запись не найдена или уже удалена.")
            await callback.answer()
            return

        old_acc_name = old_record.account.name if old_record.account else "—"

        if old_record.account_id == account_id:
            await callback.message.edit_text(
                "Значение не изменилось.\n\n" + format_record_card(old_record),
                reply_markup=record_detail_keyboard(record_id),
                parse_mode="HTML",
            )
            await callback.answer()
            return

        updated = await update_record(
            session, record_id, user_id, account_id=account_id
        )
        if updated:
            await session.commit()

    if not updated:
        await callback.message.edit_text("Не удалось сохранить изменение.")
        await callback.answer()
        return

    new_acc_name = updated.account.name if updated.account else "—"
    confirm = (
        f"✅ Сохранено\nСчёт: {html.escape(old_acc_name)} → {html.escape(new_acc_name)}"
    )
    await callback.message.edit_text(
        confirm + "\n\n" + format_record_card(updated),
        reply_markup=record_detail_keyboard(record_id),
        parse_mode="HTML",
    )
    await callback.answer()


# ==================== Delete ====================


@router.callback_query(
    F.data.startswith("record:delete:") & ~F.data.startswith("record:delete_confirm:")
)
@log_exceptions("Ошибка при запросе удаления записи")
async def record_delete_ask(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Show delete confirmation dialog."""
    try:
        record_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    await callback.message.edit_text(
        f"Удалить запись #{record_id}?",
        reply_markup=record_delete_confirm_keyboard(record_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("record:delete_confirm:"))
@log_exceptions("Ошибка при удалении записи")
async def record_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Delete the record and return to history."""
    try:
        record_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    user_id = kwargs["user_id"]

    async with async_session() as session:
        deleted = await delete_record(session, user_id, record_id)
        await session.commit()

    if not deleted:
        await callback.message.edit_text("Запись не найдена или уже удалена.")
        await callback.answer()
        return

    await callback.answer("Запись удалена.")
    await _return_to_history(callback, state, kwargs["user_id"])


# ==================== Back to history ====================


@router.callback_query(F.data == "record:back_history")
@log_exceptions("Ошибка при возврате в историю")
async def record_back_history(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Return from record card back to the history page."""
    await _return_to_history(callback, state, kwargs["user_id"])
    await callback.answer()
