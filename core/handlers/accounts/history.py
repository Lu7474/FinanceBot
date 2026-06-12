"""Per-account transaction history with period/type filters and pagination."""

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select

from config import MAX_MESSAGE_LENGTH, RECORDS_PER_PAGE
from core.database.models import Account, Record, async_session
from core.database.requests import (
    get_account_balance,
    get_accounts,
    get_history_data,
)
from core.keyboards import (
    account_history_keyboard,
    account_manage_keyboard,
    history_period_keyboard,
)
from core.utils import log_exceptions

from ..common import AccountStates, get_message, get_user_id_from_event
from ..history import build_history_page

router = Router()


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

    if len(text) > MAX_MESSAGE_LENGTH - 100:
        text = text[: MAX_MESSAGE_LENGTH - 150] + "\n\n... (сообщение обрезано)"

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
