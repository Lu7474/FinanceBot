"""Handlers for debts & loans: create, partial payment, delete, archive."""

import html
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_DEBT_AMOUNT, MAX_DEBT_PERSON_NAME, RECORDS_PER_PAGE
from core.database.models import async_session
from core.database.requests import (
    add_payment,
    count_closed_debts,
    create_debt,
    delete_debt,
    get_active_debts,
    get_closed_debts,
    get_debt,
    get_debt_payments,
)
from core.exceptions import (
    DebtAlreadyClosed,
    DebtError,
    DebtNotFound,
    PaymentExceedsRemaining,
)
from core.keyboards import (
    debt_archive_keyboard,
    debt_delete_confirm_keyboard,
    debt_detail_keyboard,
    debt_direction_keyboard,
    debt_due_date_keyboard,
    debt_select_keyboard,
    debt_skip_keyboard,
    debts_menu_keyboard,
    main_menu_keyboard,
)
from core.utils import (
    clean_text,
    format_date_ru,
    format_debt_detail,
    format_debts_list,
    format_money,
    log_exceptions,
    today_msk,
)

from .common import DebtStates, get_message, get_user_id_from_event, is_debts

router = Router()

_EMPTY_TEXT = "💸 <b>Долги и займы</b>\n\nУ тебя нет активных долгов."

_DEBT_ERROR_MESSAGES: list[tuple[type, str]] = [
    (DebtNotFound, "Долг не найден."),
    (DebtAlreadyClosed, "Долг уже закрыт."),
    (PaymentExceedsRemaining, "Сумма превышает остаток долга."),
]


def _human_error(error: Exception) -> str:
    for exc_type, msg in _DEBT_ERROR_MESSAGES:
        if isinstance(error, exc_type):
            return msg
    return "Не удалось выполнить операцию по долгу."


async def _load_overview(user_id: int) -> tuple[list, int]:
    """Returns (active_debts, archive_count)."""
    async with async_session() as session:
        active = await get_active_debts(session, user_id)
        archive_count = await count_closed_debts(session, user_id)
    return active, archive_count


async def _render_list(target: Message, user_id: int, edit: bool = False) -> None:
    """Render the main debts overview (list + menu kb)."""
    active, archive_count = await _load_overview(user_id)
    today = today_msk()
    if not active:
        text = _EMPTY_TEXT
    else:
        text = format_debts_list(active, today)
    kb = debts_menu_keyboard(
        has_active=bool(active),
        has_archive=archive_count > 0,
    )
    if edit:
        await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")


# ==================== Открытие раздела ====================


@router.message(F.func(is_debts))
@log_exceptions("Ошибка при открытии раздела долгов")
async def debts_entry(message: Message, state: FSMContext, **kwargs) -> None:
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _render_list(message, user_id, edit=False)
    await state.set_state(DebtStates.viewing_list)


@router.callback_query(F.data == "debt:open")
@log_exceptions("Ошибка при обновлении списка долгов")
async def debts_open(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _render_list(get_message(callback), user_id, edit=True)
    await state.set_state(DebtStates.viewing_list)
    await callback.answer()


@router.callback_query(F.data == "debt:back")
@log_exceptions("Ошибка при выходе из раздела долгов")
async def debts_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.clear()
    await get_message(callback).answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    await get_message(callback).delete()
    await callback.answer()


@router.callback_query(F.data == "debt:cancel")
@log_exceptions("Ошибка при отмене")
async def debts_cancel(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _render_list(get_message(callback), user_id, edit=True)
    await state.set_state(DebtStates.viewing_list)
    await callback.answer()


@router.callback_query(F.data == "debt:noop")
async def debts_noop(callback: CallbackQuery, **kwargs) -> None:
    await callback.answer()


# ==================== Создание ====================


@router.callback_query(F.data == "debt:add")
@log_exceptions("Ошибка при начале создания долга")
async def debt_add_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    await get_message(callback).edit_text(
        "Кто кому должен?",
        reply_markup=debt_direction_keyboard(),
    )
    await state.set_state(DebtStates.waiting_direction)
    await callback.answer()


@router.callback_query(F.data.startswith("debt:dir:"))
@log_exceptions("Ошибка при выборе направления долга")
async def debt_direction_chosen(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    direction = (callback.data or "").split(":")[2]
    if direction not in ("I", "O"):
        await callback.answer("Некорректный выбор.")
        return
    await state.update_data(debt_direction=direction)
    label = "должника" if direction == "I" else "кредитора"
    await get_message(callback).edit_text(
        f"Введите имя {label} (до {MAX_DEBT_PERSON_NAME} символов):",
    )
    await state.set_state(DebtStates.waiting_person)
    await callback.answer()


@router.message(DebtStates.waiting_person)
@log_exceptions("Ошибка при вводе имени")
async def debt_person_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    name = clean_text(message.text or "")
    if not name or len(name) > MAX_DEBT_PERSON_NAME:
        await message.answer(
            f"Имя должно быть от 1 до {MAX_DEBT_PERSON_NAME} символов. Попробуй ещё раз:"
        )
        return
    await state.update_data(debt_person=name)
    await message.answer("Введи сумму (₽):")
    await state.set_state(DebtStates.waiting_amount)


@router.message(DebtStates.waiting_amount)
@log_exceptions("Ошибка при вводе суммы")
async def debt_amount_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    text = (message.text or "").strip()
    try:
        amount = Decimal(text.replace(",", ".").replace(" ", ""))
        if amount <= 0 or amount > Decimal(str(MAX_DEBT_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введи число от 1 до {MAX_DEBT_AMOUNT:,}₽:".replace(
                ",", " "
            )
        )
        return
    await state.update_data(debt_amount=str(amount))
    await message.answer(
        "Добавь описание (за что), до 200 символов — или пропусти:",
        reply_markup=debt_skip_keyboard("debt:desc_skip"),
    )
    await state.set_state(DebtStates.waiting_description)


@router.message(DebtStates.waiting_description)
@log_exceptions("Ошибка при вводе описания")
async def debt_description_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    desc = clean_text(message.text or "")
    if len(desc) > 200:
        await message.answer("Описание должно быть до 200 символов. Попробуй ещё раз:")
        return
    await state.update_data(debt_description=desc or None)
    await _ask_due_date(message)
    await state.set_state(DebtStates.waiting_due_date)


@router.callback_query(F.data == "debt:desc_skip")
@log_exceptions("Ошибка при пропуске описания")
async def debt_description_skip(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    await state.update_data(debt_description=None)
    await _ask_due_date(get_message(callback), edit=True)
    await state.set_state(DebtStates.waiting_due_date)
    await callback.answer()


async def _ask_due_date(target: Message, edit: bool = False) -> None:
    text = "Укажи срок возврата (ДД.ММ.ГГГГ) или нажми «Без срока»:"
    kb = debt_due_date_keyboard()
    if edit:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.message(DebtStates.waiting_due_date)
@log_exceptions("Ошибка при вводе срока")
async def debt_due_date_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    text = (message.text or "").strip()
    try:
        due_date = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "Неверный формат. Введи дату как ДД.ММ.ГГГГ или нажми «Без срока»:"
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _finalize_create(message, state, due_date, user_id)


@router.callback_query(F.data == "debt:dd_skip")
@log_exceptions("Ошибка при создании долга без срока")
async def debt_due_date_skip(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _finalize_create(
        get_message(callback), state, None, user_id, callback=callback
    )


async def _finalize_create(
    message: Message,
    state: FSMContext,
    due_date,
    user_id: int,
    callback: CallbackQuery | None = None,
) -> None:
    data = await state.get_data()
    direction = data.get("debt_direction")
    person = data.get("debt_person", "")
    amount = Decimal(data.get("debt_amount", "0"))
    description = data.get("debt_description")

    if direction not in ("I", "O") or not person:
        # FSM lost — bail to list view.
        await _reply_with_list(
            message, state, user_id, callback,
            prefix="⚠️ Сессия создания утеряна, начни заново.",
        )
        return

    async with async_session() as session:
        await create_debt(
            session, user_id, direction, person, amount, description, due_date
        )
        await session.commit()

    if direction == "I":
        head = (
            f"✅ Долг добавлен: {html.escape(person)} должен тебе "
            f"{format_money(float(amount))}"
        )
    else:
        head = (
            f"✅ Долг добавлен: ты должен {html.escape(person)} "
            f"{format_money(float(amount))}"
        )
    if due_date:
        head += f" (срок: {format_date_ru(due_date)})"

    active, archive_count = await _load_overview(user_id)
    today = today_msk()
    body = format_debts_list(active, today) if active else _EMPTY_TEXT
    kb = debts_menu_keyboard(has_active=bool(active), has_archive=archive_count > 0)
    text = f"{head}\n\n{body}"

    if callback:
        await get_message(callback).edit_text(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

    await state.set_state(DebtStates.viewing_list)


# ==================== Выбор долга для просмотра / погашения ====================


async def _show_select(
    callback: CallbackQuery, user_id: int, action: str, prompt: str
) -> None:
    async with async_session() as session:
        debts = await get_active_debts(session, user_id)
    if not debts:
        await callback.answer("Нет активных долгов.", show_alert=True)
        return
    await get_message(callback).edit_text(
        prompt,
        reply_markup=debt_select_keyboard(debts, action),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "debt:view_list")
@log_exceptions("Ошибка при показе списка для карточки")
async def debt_view_list(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _show_select(callback, user_id, "view", "Выбери долг для просмотра:")


@router.callback_query(F.data == "debt:pay_list")
@log_exceptions("Ошибка при показе списка для погашения")
async def debt_pay_list(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _show_select(callback, user_id, "pay", "Выбери долг для погашения:")


@router.callback_query(F.data.startswith("debt:view:"))
@log_exceptions("Ошибка при открытии карточки долга")
async def debt_view_card(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    debt_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            debt = await get_debt(session, debt_id, user_id)
            payments = await get_debt_payments(session, debt_id)
    except DebtError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    today = today_msk()
    await get_message(callback).edit_text(
        format_debt_detail(debt, payments, today),
        reply_markup=debt_detail_keyboard(debt_id, is_archived=debt.is_closed),
        parse_mode="HTML",
    )
    await state.set_state(DebtStates.viewing_detail)
    await callback.answer()


@router.callback_query(F.data.startswith("debt:arch_view:"))
@log_exceptions("Ошибка при открытии карточки из архива")
async def debt_archive_card(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    debt_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            debt = await get_debt(session, debt_id, user_id)
            payments = await get_debt_payments(session, debt_id)
    except DebtError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    today = today_msk()
    await get_message(callback).edit_text(
        format_debt_detail(debt, payments, today),
        reply_markup=debt_detail_keyboard(debt_id, is_archived=True),
        parse_mode="HTML",
    )
    await state.set_state(DebtStates.viewing_detail)
    await callback.answer()


# ==================== Погашение ====================


@router.callback_query(F.data.startswith("debt:pay:"))
@log_exceptions("Ошибка при начале погашения")
async def debt_pay_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    debt_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            debt = await get_debt(session, debt_id, user_id)
    except DebtError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    if debt.is_closed:
        await callback.answer("Долг уже закрыт.", show_alert=True)
        return
    await state.update_data(pay_debt_id=debt_id)
    remaining_str = format_money(float(debt.remaining))
    await get_message(callback).edit_text(
        f"Погашение: <b>{html.escape(debt.person_name)}</b>\n"
        f"Осталось: {remaining_str}\n\nСколько оплачено (₽)?",
        parse_mode="HTML",
    )
    await state.set_state(DebtStates.waiting_payment_amount)
    await callback.answer()


@router.message(DebtStates.waiting_payment_amount)
@log_exceptions("Ошибка при вводе суммы погашения")
async def debt_payment_amount_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    text = (message.text or "").strip()
    try:
        amount = Decimal(text.replace(",", ".").replace(" ", ""))
        if amount <= 0 or amount > Decimal(str(MAX_DEBT_AMOUNT)):
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer(
            f"Некорректная сумма. Введи число от 1 до {MAX_DEBT_AMOUNT:,}₽:".replace(
                ",", " "
            )
        )
        return
    await state.update_data(pay_amount=str(amount))
    await message.answer(
        "Добавь заметку (необязательно), до 200 символов:",
        reply_markup=debt_skip_keyboard("debt:note_skip"),
    )
    await state.set_state(DebtStates.waiting_payment_note)


@router.message(DebtStates.waiting_payment_note)
@log_exceptions("Ошибка при вводе заметки погашения")
async def debt_payment_note_entered(
    message: Message, state: FSMContext, **kwargs
) -> None:
    note = clean_text(message.text or "")
    if len(note) > 200:
        await message.answer("Заметка должна быть до 200 символов. Попробуй ещё раз:")
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _execute_payment(message, state, note or None, user_id)


@router.callback_query(F.data == "debt:note_skip")
@log_exceptions("Ошибка при пропуске заметки")
async def debt_payment_note_skip(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _execute_payment(get_message(callback), state, None, user_id, callback=callback)


async def _execute_payment(
    message: Message,
    state: FSMContext,
    note: str | None,
    user_id: int,
    callback: CallbackQuery | None = None,
) -> None:
    data = await state.get_data()
    debt_id = data.get("pay_debt_id")
    if not isinstance(debt_id, int):
        # FSM context lost (bot restart, etc) — bail out cleanly.
        await _reply_with_list(
            message,
            state,
            user_id,
            callback,
            prefix="⚠️ Сессия погашения утеряна, начни заново.",
        )
        return
    amount = Decimal(data.get("pay_amount", "0"))

    try:
        async with async_session() as session:
            debt, just_closed = await add_payment(
                session, debt_id, user_id, amount, note
            )
            person = debt.person_name
            remaining = debt.remaining
            await session.commit()
    except DebtError as e:
        await _reply_with_list(
            message, state, user_id, callback, prefix=f"⚠️ {_human_error(e)}"
        )
        return

    if just_closed:
        head = f"🎉 Долг перед '{html.escape(person)}' полностью погашен!"
    else:
        head = (
            f"✅ Записано. Остаток долга: <b>{format_money(float(remaining))}</b>"
        )
    await _reply_with_list(message, state, user_id, callback, prefix=head)


async def _reply_with_list(
    message: Message,
    state: FSMContext,
    user_id: int,
    callback: CallbackQuery | None,
    prefix: str,
) -> None:
    active, archive_count = await _load_overview(user_id)
    today = today_msk()
    body = format_debts_list(active, today) if active else _EMPTY_TEXT
    kb = debts_menu_keyboard(has_active=bool(active), has_archive=archive_count > 0)
    text = f"{prefix}\n\n{body}"
    if callback:
        await get_message(callback).edit_text(text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(DebtStates.viewing_list)


# ==================== Удаление ====================


@router.callback_query(F.data.startswith("debt:del:"))
@log_exceptions("Ошибка при запросе удаления долга")
async def debt_delete_request(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    debt_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            debt = await get_debt(session, debt_id, user_id)
            is_archived = debt.is_closed
    except DebtError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return
    await get_message(callback).edit_text(
        "Удалить долг и всю историю платежей? Действие необратимо.",
        reply_markup=debt_delete_confirm_keyboard(debt_id, is_archived=is_archived),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("debt:del_yes:"))
@log_exceptions("Ошибка при удалении долга")
async def debt_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    debt_id = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    try:
        async with async_session() as session:
            await delete_debt(session, debt_id, user_id)
            await session.commit()
    except DebtError as e:
        await callback.answer(_human_error(e), show_alert=True)
        return

    active, archive_count = await _load_overview(user_id)
    today = today_msk()
    body = format_debts_list(active, today) if active else _EMPTY_TEXT
    kb = debts_menu_keyboard(has_active=bool(active), has_archive=archive_count > 0)
    await get_message(callback).edit_text(
        f"🗑 Долг удалён.\n\n{body}",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await state.set_state(DebtStates.viewing_list)
    await callback.answer()


# ==================== Архив ====================


async def _render_archive(
    callback: CallbackQuery, user_id: int, page: int
) -> bool:
    """Render the archive page. Returns False if archive is empty (caller bails)."""
    async with async_session() as session:
        total = await count_closed_debts(session, user_id)
        if total == 0:
            return False
        total_pages = max(1, (total + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE)
        page = max(0, min(page, total_pages - 1))
        debts = await get_closed_debts(
            session,
            user_id,
            limit=RECORDS_PER_PAGE,
            offset=page * RECORDS_PER_PAGE,
        )

    lines = [f"📦 <b>Архив долгов</b>  ({total})"]
    for d in debts:
        arrow = "📥" if d.direction == "I" else "📤"
        closed_str = format_date_ru(d.closed_at.date()) if d.closed_at else "—"
        lines.append(
            f"\n{arrow} <b>{html.escape(d.person_name)}</b> — "
            f"{format_money(float(d.amount))}\n  закрыт {closed_str}"
        )
    await get_message(callback).edit_text(
        "\n".join(lines),
        reply_markup=debt_archive_keyboard(debts, page, total_pages),
        parse_mode="HTML",
    )
    return True


@router.callback_query(F.data == "debt:archive")
@log_exceptions("Ошибка при открытии архива долгов")
async def debt_archive_open(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    rendered = await _render_archive(callback, user_id, page=0)
    if not rendered:
        await callback.answer("Архив пуст.", show_alert=True)
        return
    await state.set_state(DebtStates.viewing_archive)
    await callback.answer()


@router.callback_query(F.data.startswith("debt:arch_page:"))
@log_exceptions("Ошибка при пагинации архива")
async def debt_archive_page(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    page = int((callback.data or "").split(":")[2])
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    rendered = await _render_archive(callback, user_id, page=page)
    if not rendered:
        await callback.answer("Архив пуст.", show_alert=True)
        return
    await state.set_state(DebtStates.viewing_archive)
    await callback.answer()
