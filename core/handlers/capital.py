"""Capital section: live assets/liabilities (manual + virtual) and snapshot history."""

import html
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from config import MAX_AMOUNT, TIMEZONE
from core.database.models import SavingsItem, SavingsSnapshot, WealthItem, async_session
from core.database.requests import (
    add_wealth_item,
    collect_capital_items,
    create_snapshot_from_wealth,
    delete_snapshot,
    delete_snapshot_item,
    delete_wealth_item,
    get_account_balances,
    get_active_debts,
    get_latest_snapshot,
    get_snapshot,
    get_snapshot_by_id,
    get_snapshots_dates,
    get_wealth_items,
    update_snapshot_item,
    update_wealth_item,
)
from core.keyboards import (
    capital_back_keyboard,
    capital_confirm_delete_all_keyboard,
    capital_confirm_snapshot_keyboard,
    capital_history_keyboard,
    capital_menu_keyboard,
    capital_snapshot_back_keyboard,
    capital_snapshot_items_keyboard,
    capital_type_keyboard,
    capital_wealth_items_keyboard,
    main_menu_keyboard,
)
from core.utils import (
    clean_text,
    format_capital,
    format_capital_snapshot,
    format_money,
    log_exceptions,
)

from .common import (
    CapitalStates,
    get_message,
    get_user_id_from_event,
    is_capital,
    is_main_menu_button,
)

router = Router()


def _today() -> date_type:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def _parse_amount(raw: str) -> Decimal | None:
    """Parses a money string; returns None if invalid or out of [0, MAX_AMOUNT]."""
    try:
        amount = Decimal((raw or "").strip().replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None
    if amount < 0 or amount > MAX_AMOUNT:
        return None
    return amount


async def _build_capital_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Returns (text, keyboard) for the live capital view."""
    async with async_session() as session:
        wealth = await get_wealth_items(session, user_id)
        debts = await get_active_debts(session, user_id)
        balances = await get_account_balances(session, user_id)
        last = await get_latest_snapshot(session, user_id)
    text = format_capital(wealth, debts, balances, last)
    return text, capital_menu_keyboard(bool(wealth))


async def _build_history_view(
    user_id: int, target_date: date_type | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    """Returns (text, keyboard) for the snapshot history view."""
    async with async_session() as session:
        all_dates = await get_snapshots_dates(session, user_id)
        if not all_dates:
            return (
                "📸 <b>История капитала</b>\n\nСнимков пока нет. "
                "Нажмите «📸 Снимок», чтобы зафиксировать текущее состояние.",
                capital_history_keyboard(),
            )

        if target_date is None or target_date not in all_dates:
            target_date = all_dates[-1]

        idx = all_dates.index(target_date)
        snapshot = await get_snapshot(session, user_id, target_date)
        prev_date = all_dates[idx - 1] if idx > 0 else None
        next_date = all_dates[idx + 1] if idx < len(all_dates) - 1 else None
        prev_snapshot = (
            await get_snapshot(session, user_id, prev_date) if prev_date else None
        )

    items = snapshot.items if snapshot else []
    prev_items = prev_snapshot.items if prev_snapshot else None
    text = format_capital_snapshot(items, prev_items, target_date)
    keyboard = capital_history_keyboard(
        prev_date=prev_date,
        next_date=next_date,
        snapshot_id=snapshot.id if snapshot else None,
    )
    return text, keyboard


# ========================= ENTRY POINT & NAV =========================


@router.message(StateFilter("*"), F.func(is_capital))
@log_exceptions("Ошибка при открытии капитала")
async def handle_capital(message: Message, state: FSMContext, **kwargs) -> None:
    """Открывает раздел «Капитал»."""
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start.")
        return
    assert isinstance(user_id, int)
    text, keyboard = await _build_capital_view(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "cap_close")
async def cb_cap_close(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Возврат в главное меню (как в долгах/целях/платежах)."""
    await state.clear()
    await get_message(callback).answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    await get_message(callback).delete()
    await callback.answer()


@router.callback_query(F.data.in_({"cap_back", "cap_to_capital"}))
@log_exceptions("Ошибка при возврате к капиталу")
async def cb_cap_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Возврат к живому экрану капитала (отмена текущего шага)."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    assert isinstance(user_id, int)
    text, keyboard = await _build_capital_view(user_id)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# ========================= ADD MANUAL ITEM =========================


@router.callback_query(F.data == "cap_add")
async def cb_cap_add(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Начинает добавление актива/пассива."""
    await state.clear()
    await state.set_state(CapitalStates.choosing_type)
    await get_message(callback).edit_text(
        "➕ <b>Добавить запись</b>\n\nВыберите тип:",
        reply_markup=capital_type_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(CapitalStates.choosing_type, F.data.startswith("cap_type:"))
async def cb_cap_type(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Запоминает тип и просит ввести название."""
    type_ = (callback.data or "").split(":")[1]
    await state.set_state(CapitalStates.entering_name)
    await state.update_data(type_=type_)
    type_label = "💚 Актив" if type_ == "A" else "🔴 Пассив"
    await get_message(callback).edit_text(
        f"{type_label}\n\nВведите название:",
        reply_markup=capital_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(CapitalStates.entering_name, ~F.func(is_main_menu_button))
async def msg_cap_name(message: Message, state: FSMContext, **kwargs) -> None:
    name = clean_text(message.text or "")
    if not name or len(name) > 100:
        await message.answer("Название от 1 до 100 символов.")
        return
    await state.set_state(CapitalStates.entering_amount)
    await state.update_data(name=name)
    await message.answer(
        f"<b>{html.escape(name)}</b>\n\nВведите сумму:",
        reply_markup=capital_back_keyboard(),
        parse_mode="HTML",
    )


@router.message(CapitalStates.entering_amount, ~F.func(is_main_menu_button))
async def msg_cap_amount(message: Message, state: FSMContext, **kwargs) -> None:
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(f"Введите число от 0 до {format_money(MAX_AMOUNT)}.")
        return
    await state.set_state(CapitalStates.entering_note)
    await state.update_data(amount=str(amount))
    await message.answer(
        "Добавить заметку? (необязательно)",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить", callback_data="cap_skip_note")],
                [InlineKeyboardButton(text="← Назад", callback_data="cap_back")],
            ]
        ),
    )


@router.callback_query(CapitalStates.entering_note, F.data == "cap_skip_note")
@log_exceptions("Ошибка при сохранении записи")
async def cb_cap_skip_note(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Сохраняет запись без заметки."""
    data = await state.get_data()
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        item = await add_wealth_item(
            session, user_id, data["type_"], data["name"], Decimal(data["amount"]), None
        )
        if item:
            await session.commit()
    await state.clear()
    if not item:
        await callback.answer("Ошибка при сохранении.", show_alert=True)
        return
    text, keyboard = await _build_capital_view(user_id)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Сохранено ✅")


@router.message(CapitalStates.entering_note, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении записи")
async def msg_cap_note(message: Message, state: FSMContext, **kwargs) -> None:
    """Сохраняет запись с заметкой."""
    note = clean_text(message.text or "")[:200]
    data = await state.get_data()
    user_id = await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        item = await add_wealth_item(
            session, user_id, data["type_"], data["name"], Decimal(data["amount"]), note
        )
        if item:
            await session.commit()
    await state.clear()
    if not item:
        await message.answer("Ошибка при сохранении.")
        return
    text, keyboard = await _build_capital_view(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ========================= EDIT MANUAL ITEM =========================


@router.callback_query(F.data == "cap_wealth_edit")
@log_exceptions("Ошибка при редактировании")
async def cb_cap_wealth_edit(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Список ручных активов/пассивов для выбора редактирования суммы."""
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        items = await get_wealth_items(session, user_id)
    if not items:
        await callback.answer(
            "Нет ручных записей. Счета и долги меняются в своих разделах.",
            show_alert=True,
        )
        return
    await get_message(callback).edit_text(
        "✏️ <b>Выберите запись для редактирования суммы:</b>",
        reply_markup=capital_wealth_items_keyboard(items, "edit"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cap_wealth_edit_item:"))
@log_exceptions("Ошибка при выборе записи")
async def cb_cap_wealth_edit_item(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Запрашивает новую сумму для выбранного актива/пассива."""
    item_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        result = await session.execute(
            select(WealthItem).where(
                WealthItem.id == item_id, WealthItem.user_id == user_id
            )
        )
        item = result.scalar_one_or_none()
    if not item:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    await state.clear()
    await state.set_state(CapitalStates.editing_amount)
    await state.update_data(item_id=item_id)
    type_label = "💚 Актив" if item.type == "A" else "🔴 Пассив"
    cur_raw = f"{float(item.amount):.0f}"
    await get_message(callback).edit_text(
        f"{type_label} <b>{html.escape(item.name)}</b>\n"
        f"Текущая сумма: <code>{cur_raw}</code>\n\n"
        f"Введите новую сумму:",
        reply_markup=capital_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(CapitalStates.editing_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при обновлении суммы")
async def msg_cap_edit_amount(message: Message, state: FSMContext, **kwargs) -> None:
    """Сохраняет новую сумму ручного актива/пассива."""
    data = await state.get_data()
    item_id = data.get("item_id")
    assert isinstance(item_id, int)
    user_id = await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(f"Введите число от 0 до {format_money(MAX_AMOUNT)}.")
        return
    async with async_session() as session:
        ok = await update_wealth_item(session, item_id, user_id, amount=amount)
        if ok:
            await session.commit()
    await state.clear()
    if not ok:
        await message.answer("Ошибка при обновлении.")
        return
    text, keyboard = await _build_capital_view(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ========================= DELETE MANUAL ITEM =========================


@router.callback_query(F.data == "cap_wealth_delete")
@log_exceptions("Ошибка при удалении")
async def cb_cap_wealth_delete(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Список ручных записей для удаления."""
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        items = await get_wealth_items(session, user_id)
    if not items:
        await callback.answer(
            "Нет ручных записей. Счета и долги меняются в своих разделах.",
            show_alert=True,
        )
        return
    await get_message(callback).edit_text(
        "🗑 <b>Выберите запись для удаления:</b>",
        reply_markup=capital_wealth_items_keyboard(items, "delete"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cap_wealth_delete_item:"))
@log_exceptions("Ошибка при удалении записи")
async def cb_cap_wealth_delete_item(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет выбранный ручной актив/пассив."""
    item_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        ok = await delete_wealth_item(session, item_id, user_id)
        if ok:
            await session.commit()
    if not ok:
        await callback.answer("Ошибка при удалении.", show_alert=True)
        return
    text, keyboard = await _build_capital_view(user_id)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Удалено.")


# ========================= SNAPSHOT =========================


@router.callback_query(F.data == "cap_snapshot")
@log_exceptions("Ошибка при создании снимка")
async def cb_cap_snapshot(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Фиксирует текущий капитал снимком; если на сегодня есть — спрашивает перезапись."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    today = _today()
    async with async_session() as session:
        existing = await get_snapshot(session, user_id, today)
        items = await collect_capital_items(session, user_id)

    if not items:
        await callback.answer("Капитал пуст — нечего фиксировать.", show_alert=True)
        return

    if existing:
        await get_message(callback).edit_text(
            "📸 Снимок за <b>сегодня</b> уже есть.\n\n"
            "Перезаписать его текущим состоянием капитала?",
            reply_markup=capital_confirm_snapshot_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    async with async_session() as session:
        snap = await create_snapshot_from_wealth(session, user_id, today)
        if snap:
            await session.commit()
    if not snap:
        await callback.answer("Ошибка при сохранении.", show_alert=True)
        return
    text, keyboard = await _build_history_view(user_id, today)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Снимок сохранён ✅")


@router.callback_query(F.data == "cap_snapshot_confirm")
@log_exceptions("Ошибка при перезаписи снимка")
async def cb_cap_snapshot_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Перезаписывает снимок за сегодня."""
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    today = _today()
    async with async_session() as session:
        snap = await create_snapshot_from_wealth(session, user_id, today)
        if snap:
            await session.commit()
    if not snap:
        await callback.answer("Ошибка при сохранении.", show_alert=True)
        return
    text, keyboard = await _build_history_view(user_id, today)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Снимок перезаписан ✅")


# ========================= HISTORY NAVIGATION =========================


@router.callback_query(F.data == "cap_history")
@log_exceptions("Ошибка при открытии истории")
async def cb_cap_history(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Открывает историю снимков (последний)."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    text, keyboard = await _build_history_view(user_id)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cap_date:"))
@log_exceptions("Ошибка навигации")
async def cb_cap_date(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Переключает снимок на другую дату."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    target_date = date_type.fromisoformat((callback.data or "").split(":")[1])
    text, keyboard = await _build_history_view(user_id, target_date)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cap_snap:"))
@log_exceptions("Ошибка при возврате к снимку")
async def cb_cap_snap(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Возврат к просмотру снимка по id (из списков edit/delete)."""
    await state.clear()
    snapshot_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        snap = await get_snapshot_by_id(session, snapshot_id, user_id)
    if not snap:
        await callback.answer("Снимок не найден.", show_alert=True)
        return
    text, keyboard = await _build_history_view(user_id, snap.date)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# ========================= EDIT SNAPSHOT ROW =========================


@router.callback_query(F.data.startswith("cap_edit:"))
@log_exceptions("Ошибка при редактировании снимка")
async def cb_cap_edit(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Список строк снимка для редактирования суммы."""
    snapshot_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        snapshot = await get_snapshot_by_id(session, snapshot_id, user_id)
    if not snapshot or not snapshot.items:
        await callback.answer("Снимок не найден или пуст.", show_alert=True)
        return
    await get_message(callback).edit_text(
        "✏️ <b>Выберите строку для редактирования:</b>",
        reply_markup=capital_snapshot_items_keyboard(snapshot.items, "edit", snapshot_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cap_edit_item:"))
@log_exceptions("Ошибка при выборе строки")
async def cb_cap_edit_item(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Запрашивает новую сумму для строки снимка."""
    item_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        result = await session.execute(
            select(SavingsItem, SavingsSnapshot.date, SavingsSnapshot.user_id)
            .join(SavingsSnapshot, SavingsItem.snapshot_id == SavingsSnapshot.id)
            .where(SavingsItem.id == item_id)
        )
        row = result.one_or_none()
    if not row or row.user_id != user_id:
        await callback.answer("Строка не найдена.", show_alert=True)
        return
    item, snap_date, _ = row
    await state.clear()
    await state.set_state(CapitalStates.editing_snapshot_amount)
    await state.update_data(item_id=item_id, snapshot_date=snap_date.isoformat())
    cur_raw = f"{float(item.amount):.0f}"
    await get_message(callback).edit_text(
        f"✏️ <b>{html.escape(item.name)}</b>\n"
        f"Текущая сумма: <code>{cur_raw}</code>\n\n"
        f"Введите новую сумму:",
        reply_markup=capital_snapshot_back_keyboard(item.snapshot_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(CapitalStates.editing_snapshot_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении суммы строки")
async def msg_cap_edit_snapshot_amount(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Сохраняет новую сумму строки снимка."""
    data = await state.get_data()
    item_id = data.get("item_id")
    assert isinstance(item_id, int)
    snapshot_date_str = data.get("snapshot_date")
    assert isinstance(snapshot_date_str, str)
    user_id = await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(f"Введите число от 0 до {format_money(MAX_AMOUNT)}.")
        return
    async with async_session() as session:
        ok = await update_snapshot_item(session, item_id, user_id, amount)
        if ok:
            await session.commit()
    await state.clear()
    if not ok:
        await message.answer("Ошибка при обновлении.")
        return
    target_date = date_type.fromisoformat(snapshot_date_str)
    text, keyboard = await _build_history_view(user_id, target_date)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ========================= DELETE SNAPSHOT ROW / ALL =========================


@router.callback_query(F.data.startswith("cap_delete:"))
@log_exceptions("Ошибка при удалении строки снимка")
async def cb_cap_delete(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Список строк снимка + опция удалить весь снимок."""
    snapshot_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        snapshot = await get_snapshot_by_id(session, snapshot_id, user_id)
    if not snapshot:
        await callback.answer("Снимок не найден.", show_alert=True)
        return
    await get_message(callback).edit_text(
        "🗑 <b>Что удалить?</b>\n\nВыберите строку или удалите весь снимок:",
        reply_markup=capital_snapshot_items_keyboard(
            snapshot.items, "delete", snapshot_id
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cap_delete_item:"))
@log_exceptions("Ошибка при удалении строки")
async def cb_cap_delete_item(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет конкретную строку снимка."""
    item_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        snap_date = await delete_snapshot_item(session, item_id, user_id)
        if snap_date is not None:
            await session.commit()
    if snap_date is None:
        await callback.answer("Ошибка при удалении.", show_alert=True)
        return
    text, keyboard = await _build_history_view(user_id, snap_date)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Строка удалена.")


@router.callback_query(F.data.startswith("cap_delete_all:"))
@log_exceptions("Ошибка при удалении снимка")
async def cb_cap_delete_all(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Спрашивает подтверждение перед удалением всего снимка."""
    snapshot_id = int((callback.data or "").split(":")[1])
    await get_message(callback).edit_text(
        "🗑 <b>Удалить весь снимок?</b>\n\nЭто действие нельзя отменить.",
        reply_markup=capital_confirm_delete_all_keyboard(snapshot_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cap_delete_all_confirm:"))
@log_exceptions("Ошибка при удалении снимка")
async def cb_cap_delete_all_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет весь снимок (после подтверждения)."""
    snapshot_id = int((callback.data or "").split(":")[1])
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        ok = await delete_snapshot(session, snapshot_id, user_id)
        if ok:
            await session.commit()
    if not ok:
        await callback.answer("Ошибка при удалении.", show_alert=True)
        return
    text, keyboard = await _build_history_view(user_id)
    await get_message(callback).edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer("Снимок удалён.")
