"""Handlers for wealth items (assets/liabilities)."""

import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from config import MAX_AMOUNT
from core.database.models import WealthItem, async_session
from core.database.requests import (
    add_wealth_item,
    delete_wealth_item,
    get_wealth_items,
    update_wealth_item,
)
from core.keyboards import (
    wealth_back_keyboard,
    wealth_items_keyboard,
    wealth_menu_keyboard,
    wealth_type_keyboard,
)
from core.utils import clean_text, format_money, format_wealth, log_exceptions

from .common import (
    WealthStates,
    get_message,
    get_user_id_from_event,
    is_main_menu_button,
)
from .savings import _build_savings_view

router = Router()


async def _build_wealth_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Returns (text, keyboard) for the wealth view."""
    async with async_session() as session:
        items = await get_wealth_items(session, user_id)
    text = format_wealth(items)
    return text, wealth_menu_keyboard()


@router.callback_query(F.data == "sav_wealth")
@log_exceptions("Ошибка при открытии активов/пассивов")
async def cb_sav_wealth(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Открывает раздел активов/пассивов."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    assert isinstance(user_id, int)
    text, keyboard = await _build_wealth_view(user_id)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "wealth_to_savings")
@log_exceptions("Ошибка при возврате к накоплениям")
async def cb_wealth_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Возврат из активов/пассивов к снимку накоплений."""
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


@router.callback_query(F.data == "wealth_back")
@log_exceptions("Ошибка при возврате к Активам/Пассивам")
async def cb_wealth_back_to_view(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Возврат к экрану балансов активов/пассивов из любого шага wizard'а."""
    await state.clear()
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.", show_alert=True)
        return
    assert isinstance(user_id, int)
    text, keyboard = await _build_wealth_view(user_id)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer()


# --- Add wealth item ---


@router.callback_query(F.data == "wealth_add")
async def cb_wealth_add(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Начинает добавление нового актива/пассива."""
    await state.clear()
    await state.set_state(WealthStates.choosing_type)
    await get_message(callback).edit_text(
        "➕ <b>Добавить запись</b>\n\nВыберите тип:",
        reply_markup=wealth_type_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(WealthStates.choosing_type, F.data.startswith("wealth_type:"))
async def cb_wealth_type(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Запоминает тип и просит ввести название."""
    type_ = (callback.data or "").split(":")[1]
    await state.set_state(WealthStates.entering_name)
    await state.update_data(type_=type_)
    type_label = "💚 Актив" if type_ == "A" else "🔴 Пассив"
    await get_message(callback).edit_text(
        f"{type_label}\n\nВведите название:",
        reply_markup=wealth_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(WealthStates.entering_name, ~F.func(is_main_menu_button))
async def msg_wealth_name(message: Message, state: FSMContext, **kwargs) -> None:
    name = clean_text(message.text or "")
    if not name or len(name) > 100:
        await message.answer("Название от 1 до 100 символов.")
        return
    await state.set_state(WealthStates.entering_amount)
    await state.update_data(name=name)
    await message.answer(
        f"<b>{html.escape(name)}</b>\n\nВведите сумму:",
        reply_markup=wealth_back_keyboard(),
        parse_mode="HTML",
    )


@router.message(WealthStates.entering_amount, ~F.func(is_main_menu_button))
async def msg_wealth_amount(message: Message, state: FSMContext, **kwargs) -> None:
    try:
        amount = Decimal(
            (message.text or "").strip().replace(" ", "").replace(",", ".")
        )
        if amount < 0 or amount > MAX_AMOUNT:
            await message.answer(f"Сумма от 0 до {format_money(MAX_AMOUNT)}.")
            return
    except InvalidOperation:
        await message.answer("Введите число.")
        return

    await state.set_state(WealthStates.entering_note)
    await state.update_data(amount=str(amount))
    await message.answer(
        "Добавить заметку? (необязательно)",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Пропустить", callback_data="wealth_skip_note"
                    )
                ],
                [InlineKeyboardButton(text="← Назад", callback_data="wealth_back")],
            ]
        ),
    )


@router.callback_query(WealthStates.entering_note, F.data == "wealth_skip_note")
@log_exceptions("Ошибка при сохранении актива")
async def cb_wealth_skip_note(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет запись без заметки."""
    await _save_wealth_item(callback, state, note=None, **kwargs)


@router.message(WealthStates.entering_note, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении актива")
async def msg_wealth_note(message: Message, state: FSMContext, **kwargs) -> None:
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
    text, keyboard = await _build_wealth_view(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def _save_wealth_item(
    callback: CallbackQuery, state: FSMContext, note: str | None, **kwargs
) -> None:
    """Helper: saves wealth item and shows updated wealth view."""
    data = await state.get_data()
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        item = await add_wealth_item(
            session, user_id, data["type_"], data["name"], Decimal(data["amount"]), note
        )
        if item:
            await session.commit()
    await state.clear()
    if not item:
        await callback.answer("Ошибка при сохранении.", show_alert=True)
        return
    text, keyboard = await _build_wealth_view(user_id)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer("Сохранено ✅")


# --- Edit wealth item ---


@router.callback_query(F.data == "wealth_edit")
@log_exceptions("Ошибка при редактировании актива")
async def cb_wealth_edit(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Показывает список активов/пассивов для выбора редактирования."""
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        items = await get_wealth_items(session, user_id)
    if not items:
        await callback.answer("Список пуст.", show_alert=True)
        return
    await get_message(callback).edit_text(
        "✏️ <b>Выберите запись для редактирования суммы:</b>",
        reply_markup=wealth_items_keyboard(items, "edit"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("wealth_edit_item:"))
@log_exceptions("Ошибка при выборе записи")
async def cb_wealth_edit_item(
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
    await state.set_state(WealthStates.editing_amount)
    await state.update_data(item_id=item_id)
    type_label = "💚 Актив" if item.type == "A" else "🔴 Пассив"
    cur_raw = f"{float(item.amount):.0f}"
    await get_message(callback).edit_text(
        f"{type_label} <b>{html.escape(item.name)}</b>\n"
        f"Текущая сумма: <code>{cur_raw}</code>\n\n"
        f"Введите новую сумму:",
        reply_markup=wealth_back_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(WealthStates.editing_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при обновлении суммы")
async def msg_wealth_edit_amount(message: Message, state: FSMContext, **kwargs) -> None:
    """Сохраняет новую сумму актива/пассива."""
    data = await state.get_data()
    item_id = data.get("item_id")
    assert isinstance(item_id, int)
    user_id = await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)

    try:
        amount = Decimal(
            (message.text or "").strip().replace(" ", "").replace(",", ".")
        )
        if amount < 0 or amount > MAX_AMOUNT:
            await message.answer(f"Сумма от 0 до {format_money(MAX_AMOUNT)}.")
            return
    except InvalidOperation:
        await message.answer("Введите число.")
        return

    async with async_session() as session:
        ok = await update_wealth_item(session, item_id, user_id, amount=amount)
        if ok:
            await session.commit()

    await state.clear()
    if not ok:
        await message.answer("Ошибка при обновлении.")
        return

    text, keyboard = await _build_wealth_view(user_id)
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


# --- Delete wealth item ---


@router.callback_query(F.data == "wealth_delete")
@log_exceptions("Ошибка при удалении актива")
async def cb_wealth_delete(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Показывает список для выбора записи на удаление."""
    user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)
    async with async_session() as session:
        items = await get_wealth_items(session, user_id)
    if not items:
        await callback.answer("Список пуст.", show_alert=True)
        return
    await get_message(callback).edit_text(
        "🗑 <b>Выберите запись для удаления:</b>",
        reply_markup=wealth_items_keyboard(items, "delete"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("wealth_delete_item:"))
@log_exceptions("Ошибка при удалении записи")
async def cb_wealth_delete_item(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Удаляет выбранный актив/пассив."""
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

    text, keyboard = await _build_wealth_view(user_id)
    await get_message(callback).edit_text(
        text, reply_markup=keyboard, parse_mode="HTML"
    )
    await callback.answer("Удалено.")
