"""Handlers for savings snapshots."""

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
from core.database.models import SavingsItem, SavingsSnapshot, async_session
from core.database.requests import (
    add_snapshot_item,
    delete_snapshot,
    delete_snapshot_item,
    get_account_balances,
    get_latest_snapshot,
    get_snapshot,
    get_snapshot_by_id,
    get_snapshots_dates,
    update_snapshot_item,
    upsert_snapshot,
)
from core.keyboards import (
    savings_confirm_keyboard,
    savings_items_keyboard,
    savings_view_keyboard,
)
from core.utils import (
    clean_text,
    format_money,
    format_snapshot,
    log_exceptions,
)

from .common import (
    SavingsStates,
    get_message,
    get_user_id_from_event,
    is_main_menu_button,
    is_savings,
)

router = Router()

_CANCEL_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Отмена", callback_data="sav_cancel_action")],
    ]
)


def _today() -> date_type:
    return datetime.now(ZoneInfo(TIMEZONE)).date()


async def _build_savings_view(
    user_id: int, target_date: date_type | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    """Returns (text, keyboard) for the main savings view."""
    async with async_session() as session:
        all_dates = await get_snapshots_dates(session, user_id)

        if not all_dates:
            return (
                "💰 <b>Накопления</b>\n\nСнимков пока нет. Нажмите «Добавить запись».",
                savings_view_keyboard(),
            )

        if target_date is None or target_date not in all_dates:
            target_date = all_dates[-1]

        idx = all_dates.index(target_date)
        snapshot = await get_snapshot(session, user_id, target_date)

        prev_date = all_dates[idx - 1] if idx > 0 else None
        next_date = all_dates[idx + 1] if idx < len(all_dates) - 1 else None

        prev_snapshot = None
        if prev_date:
            prev_snapshot = await get_snapshot(session, user_id, prev_date)

    items = snapshot.items if snapshot else []
    prev_items = prev_snapshot.items if prev_snapshot else None
    text = format_snapshot(items, prev_items, target_date)
    keyboard = savings_view_keyboard(
        prev_date=prev_date,
        next_date=next_date,
        snapshot_id=snapshot.id if snapshot else None,
    )
    return text, keyboard


def _build_confirm_text(entered: list[dict]) -> str:
    """Builds confirm-screen text from list of {name, amount} dicts."""
    lines = ["📋 <b>Проверьте снимок перед сохранением:</b>\n"]
    total = Decimal("0")
    for item in entered:
        amount = Decimal(item["amount"])
        lines.append(
            f"• {html.escape(item['name'])}: <b>{format_money(float(amount))}</b>"
        )
        total += amount
    lines.append(f"\n<b>Итого: {format_money(float(total))}</b>")
    return "\n".join(lines)


# ========================= ENTRY POINT =========================


@router.message(StateFilter("*"), F.func(is_savings))
@log_exceptions("Ошибка при открытии накоплений")
async def handle_savings(message: Message, state: FSMContext, **kwargs) -> None:
    """Открывает раздел накоплений."""
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start.")
        return
    assert isinstance(user_id, int)
    text, keyboard = await _build_savings_view(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ========================= NAVIGATION =========================


@router.callback_query(F.data.startswith("sav_date:"))
@log_exceptions("Ошибка навигации")
async def cb_sav_date(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Переключает снимок на другую дату."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    assert isinstance(user_id, int)
    date_str = (callback.data or "").split(":")[1]
    target_date = date_type.fromisoformat(date_str)
    text, keyboard = await _build_savings_view(user_id, target_date)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "sav_back")
async def cb_sav_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Возврат в главное меню."""
    await state.clear()
    await get_message(callback).delete()
    await callback.answer()


@router.callback_query(F.data == "sav_cancel_action")
@log_exceptions("Ошибка при отмене")
async def cb_sav_cancel_action(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Отмена текущего действия.

    Если пользователь добавляет поле в середине создания снимка (entered уже есть),
    возвращает к экрану подтверждения, а не сбрасывает всё.
    """
    data = await state.get_data()
    entered: list = data.get("entered", [])
    mode: str = data.get("mode", "")

    if entered and mode == "create":
        await state.set_state(SavingsStates.confirming_snapshot)
        await state.update_data(pending_name=None)
        await get_message(callback).edit_text(
            _build_confirm_text(entered),
            reply_markup=savings_confirm_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    assert isinstance(user_id, int)
    text, keyboard = await _build_savings_view(user_id)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


# ========================= ADD SNAPSHOT =========================


@router.callback_query(F.data == "sav_add")
@log_exceptions("Ошибка при добавлении снимка")
async def cb_sav_add(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Начинает процесс создания нового снимка."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    assert isinstance(user_id, int)

    today = _today()
    async with async_session() as session:
        existing = await get_snapshot(session, user_id, today)
        latest = await get_latest_snapshot(session, user_id)
        balances = await get_account_balances(session, user_id)

    if existing:
        await callback.answer(
            "Снимок за сегодня уже есть. Используйте ✏️ Изменить или ➕ Добавить поле.",
            show_alert=True,
        )
        return

    await state.set_state(SavingsStates.choosing_names_source)
    await state.update_data(
        target_date=today.isoformat(),
        last_names=[
            {"name": i.name, "prev_amount": str(i.amount)} for i in latest.items
        ]
        if latest and latest.items
        else [],
    )

    buttons = []
    if balances:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="📊 Подтянуть из Счетов", callback_data="sav_from_accounts"
                ),
            ]
        )
    if latest and latest.items:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🕘 Использовать прошлые названия",
                    callback_data="sav_use_last",
                ),
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="✍️ Ввести вручную", callback_data="sav_new_names"
            ),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(text="Отмена", callback_data="sav_cancel_action"),
        ]
    )

    await get_message(callback).edit_text(
        "➕ <b>Новый снимок</b>\n\nКак заполнить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(SavingsStates.choosing_names_source, F.data == "sav_use_last")
@log_exceptions("Ошибка при старте ввода по шаблону")
async def cb_sav_use_last(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Начинает итеративный ввод сумм по шаблону из прошлого снимка."""
    data = await state.get_data()
    last_names = data.get("last_names", [])
    if not last_names:
        await callback.answer("Нет шаблона.", show_alert=True)
        return

    await state.set_state(SavingsStates.entering_amounts)
    await state.update_data(templates=last_names, index=0, entered=[])

    first = last_names[0]
    prev_str = (
        f" (предыдущее: <b>{format_money(float(first['prev_amount']))}</b>)"
        if first.get("prev_amount")
        else ""
    )
    await get_message(callback).edit_text(
        f"<b>{html.escape(first['name'])}</b>: введите сумму{prev_str}",
        reply_markup=_CANCEL_KB,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(
    SavingsStates.choosing_names_source, F.data == "sav_from_accounts"
)
@log_exceptions("Ошибка при подтягивании счетов")
async def cb_sav_from_accounts(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Заполняет снимок текущими балансами счетов как стартовой точкой."""
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        balances = await get_account_balances(session, user_id)

    entered = [
        {"name": acc.name, "amount": str(max(Decimal("0"), balance))}
        for acc, balance in balances
    ]

    await state.set_state(SavingsStates.confirming_snapshot)
    await state.update_data(entered=entered)
    await get_message(callback).edit_text(
        _build_confirm_text(entered),
        reply_markup=savings_confirm_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(SavingsStates.choosing_names_source, F.data == "sav_new_names")
async def cb_sav_new_names(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Переход к ручному вводу новых названий."""
    await state.set_state(SavingsStates.entering_new_field_name)
    await state.update_data(mode="create", items=[], pending_name=None)
    await get_message(callback).edit_text(
        "✍️ <b>Введите название первого счёта/кошелька:</b>",
        reply_markup=_CANCEL_KB,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SavingsStates.entering_amounts, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при вводе суммы")
async def msg_savings_enter_amount(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Принимает сумму для текущего поля шаблонного режима."""
    data = await state.get_data()
    templates: list = data.get("templates", [])
    index: int = data.get("index", 0)
    entered: list = data.get("entered", [])

    try:
        amount = Decimal(
            (message.text or "").strip().replace(" ", "").replace(",", ".")
        )
        if amount < 0:
            await message.answer("Сумма не может быть отрицательной.")
            return
        if amount > MAX_AMOUNT:
            await message.answer(f"Максимальная сумма: {format_money(MAX_AMOUNT)}.")
            return
    except InvalidOperation:
        await message.answer("Введите число (например: 30000 или 30000.50).")
        return

    current = templates[index]
    entered.append({"name": current["name"], "amount": str(amount)})
    index += 1

    if index < len(templates):
        nxt = templates[index]
        prev_str = (
            f" (предыдущее: <b>{format_money(float(nxt['prev_amount']))}</b>)"
            if nxt.get("prev_amount")
            else ""
        )
        await state.update_data(index=index, entered=entered)
        await message.answer(
            f"<b>{html.escape(nxt['name'])}</b>: введите сумму{prev_str}",
            reply_markup=_CANCEL_KB,
            parse_mode="HTML",
        )
    else:
        await state.set_state(SavingsStates.confirming_snapshot)
        await state.update_data(index=index, entered=entered)
        await message.answer(
            _build_confirm_text(entered),
            reply_markup=savings_confirm_keyboard(),
            parse_mode="HTML",
        )


# ========================= NEW FIELD (create or add) =========================


@router.message(SavingsStates.entering_new_field_name, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при вводе названия поля")
async def msg_savings_field_name(message: Message, state: FSMContext, **kwargs) -> None:
    """Принимает название нового поля."""
    name = clean_text(message.text or "")
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    if len(name) > 50:
        await message.answer("Название слишком длинное (максимум 50 символов).")
        return

    await state.set_state(SavingsStates.entering_new_field_amount)
    await state.update_data(pending_name=name)
    await message.answer(
        f"<b>{html.escape(name)}</b>: введите сумму",
        reply_markup=_CANCEL_KB,
        parse_mode="HTML",
    )


@router.message(SavingsStates.entering_new_field_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при вводе суммы поля")
async def msg_savings_field_amount(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Принимает сумму нового поля. mode='create' → confirm; mode='add' → save directly."""
    data = await state.get_data()
    mode: str = data.get("mode", "create")
    pending_name: str = data.get("pending_name", "")
    user_id = await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)

    try:
        amount = Decimal(
            (message.text or "").strip().replace(" ", "").replace(",", ".")
        )
        if amount < 0:
            await message.answer("Сумма не может быть отрицательной.")
            return
        if amount > MAX_AMOUNT:
            await message.answer(f"Максимальная сумма: {format_money(MAX_AMOUNT)}.")
            return
    except InvalidOperation:
        await message.answer("Введите число (например: 30000 или 30000.50).")
        return

    if mode == "create":
        entered: list = data.get("entered", data.get("items", []))
        entered.append({"name": pending_name, "amount": str(amount)})
        await state.set_state(SavingsStates.confirming_snapshot)
        await state.update_data(entered=entered, pending_name=None)
        await message.answer(
            _build_confirm_text(entered),
            reply_markup=savings_confirm_keyboard(),
            parse_mode="HTML",
        )
    elif mode == "add":
        snapshot_id = data.get("snapshot_id")
        assert isinstance(snapshot_id, int)
        snapshot_date_str = data.get("snapshot_date")
        assert isinstance(snapshot_date_str, str)
        async with async_session() as session:
            item = await add_snapshot_item(
                session, snapshot_id, user_id, pending_name, amount
            )
            if item:
                await session.commit()
        await state.clear()
        if not item:
            await message.answer("Ошибка при добавлении поля.")
            return
        target_date = date_type.fromisoformat(snapshot_date_str)
        text, keyboard = await _build_savings_view(user_id, target_date)
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ========================= CONFIRM SAVE =========================


@router.callback_query(SavingsStates.confirming_snapshot, F.data == "sav_confirm_save")
@log_exceptions("Ошибка при сохранении снимка")
async def cb_sav_confirm_save(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет снимок из экрана подтверждения."""
    data = await state.get_data()
    entered: list = data.get("entered", [])
    target_date_str: str = data.get("target_date", _today().isoformat())
    target_date = date_type.fromisoformat(target_date_str)
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    assert isinstance(user_id, int)

    items = [(item["name"], Decimal(item["amount"])) for item in entered]
    async with async_session() as session:
        snapshot = await upsert_snapshot(session, user_id, target_date, items)
        if snapshot:
            await session.commit()

    await state.clear()
    if not snapshot:
        await callback.answer("Ошибка при сохранении.", show_alert=True)
        return

    text, keyboard = await _build_savings_view(user_id, target_date)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer("Снимок сохранён ✅")


@router.callback_query(
    SavingsStates.confirming_snapshot, F.data == "sav_confirm_add_field"
)
async def cb_sav_confirm_add_field(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Добавляет ещё одно поле к снимку перед сохранением."""
    await state.set_state(SavingsStates.entering_new_field_name)
    await state.update_data(mode="create", pending_name=None)
    await get_message(callback).edit_text(
        "✍️ <b>Введите название нового поля:</b>",
        reply_markup=_CANCEL_KB,
        parse_mode="HTML",
    )
    await callback.answer()


# ========================= ADD FIELD TO EXISTING SNAPSHOT =========================


@router.callback_query(F.data.startswith("sav_add_field:"))
@log_exceptions("Ошибка при добавлении поля к снимку")
async def cb_sav_add_field(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Начинает добавление нового поля к уже сохранённому снимку."""
    snapshot_id_str = (callback.data or "").split(":")[1]
    if snapshot_id_str == "None":
        await callback.answer("Нет активного снимка.", show_alert=True)
        return

    snapshot_id = int(snapshot_id_str)
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        snap = await session.get(SavingsSnapshot, snapshot_id)

    if not snap or snap.user_id != user_id:
        await callback.answer("Снимок не найден.", show_alert=True)
        return

    await state.clear()
    await state.set_state(SavingsStates.entering_new_field_name)
    await state.update_data(
        mode="add",
        snapshot_id=snapshot_id,
        snapshot_date=snap.date.isoformat(),
        pending_name=None,
    )
    await get_message(callback).edit_text(
        "➕ <b>Введите название нового поля:</b>",
        reply_markup=_CANCEL_KB,
        parse_mode="HTML",
    )
    await callback.answer()


# ========================= EDIT ITEM =========================


@router.callback_query(F.data.startswith("sav_edit:"))
@log_exceptions("Ошибка при редактировании")
async def cb_sav_edit(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список полей снимка для редактирования."""
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
        reply_markup=savings_items_keyboard(snapshot.items, "edit"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sav_edit_item:"))
@log_exceptions("Ошибка при выборе поля")
async def cb_sav_edit_item(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Запрашивает новую сумму для выбранного поля."""
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
        await callback.answer("Поле не найдено.", show_alert=True)
        return

    item, snap_date, _ = row
    await state.clear()
    await state.set_state(SavingsStates.editing_item_amount)
    await state.update_data(item_id=item_id, snapshot_date=snap_date.isoformat())
    cur_raw = f"{float(item.amount):.0f}"
    await get_message(callback).edit_text(
        f"✏️ <b>{html.escape(item.name)}</b>\n"
        f"Текущая сумма: <code>{cur_raw}</code>\n\n"
        f"Введите новую сумму:",
        reply_markup=_CANCEL_KB,
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SavingsStates.editing_item_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении новой суммы")
async def msg_savings_edit_amount(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Сохраняет новую сумму для выбранного поля снимка."""
    data = await state.get_data()
    item_id = data.get("item_id")
    assert isinstance(item_id, int)
    snapshot_date_str = data.get("snapshot_date")
    assert isinstance(snapshot_date_str, str)
    user_id = await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)

    try:
        amount = Decimal(
            (message.text or "").strip().replace(" ", "").replace(",", ".")
        )
        if amount < 0:
            await message.answer("Сумма не может быть отрицательной.")
            return
        if amount > MAX_AMOUNT:
            await message.answer(f"Максимальная сумма: {format_money(MAX_AMOUNT)}.")
            return
    except InvalidOperation:
        await message.answer("Введите число.")
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
    text, keyboard = await _build_savings_view(user_id, target_date)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# ========================= DELETE =========================


@router.callback_query(F.data.startswith("sav_delete:"))
@log_exceptions("Ошибка при удалении")
async def cb_sav_delete(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список полей и опцию удаления всего снимка."""
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
        reply_markup=savings_items_keyboard(
            snapshot.items, "delete", snapshot_id=snapshot.id
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sav_delete_item:"))
@log_exceptions("Ошибка при удалении поля")
async def cb_sav_delete_item(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет конкретное поле из снимка."""
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

    text, keyboard = await _build_savings_view(user_id, snap_date)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer("Поле удалено.")


@router.callback_query(F.data.startswith("sav_delete_all:"))
@log_exceptions("Ошибка при удалении снимка")
async def cb_sav_delete_all(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет весь снимок."""
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

    text, keyboard = await _build_savings_view(user_id)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer("Снимок удалён.")
