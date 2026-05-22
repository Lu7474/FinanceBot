"""Handlers for user category management and smart category suggestion for records."""

import html

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from config import MAX_CATEGORY_LENGTH
from core.database.models import UserCategory, async_session
from core.database.requests import (
    add_user_category,
    count_records_with_category,
    delete_user_category,
    get_accounts,
    get_user_categories,
    learn_keyword,
    rename_user_category,
    seed_default_categories,
)
from core.keyboards import (
    category_manage_keyboard,
    category_select_keyboard,
    main_menu_keyboard,
    user_categories_menu_keyboard,
)
from core.utils import clean_text, log_exceptions

from .common import (
    AddRecord,
    CategoryStates,
    get_message,
    get_user_id_from_event,
    is_categories,
    save_parsed_records,
)
from .records import (
    _deserialize_records,
    _send_budget_alerts,
    format_added_records_response,
)

router = Router()


# ==================== Helpers ====================


async def _show_categories_menu(target, user_id: int, state: FSMContext) -> None:
    """Fetches categories and renders the menu (used by entry and post-action refresh)."""
    async with async_session() as session:
        all_cats = await get_user_categories(session, user_id)

    expenses = [c for c in all_cats if c.cat_type == "-"]
    incomes = [c for c in all_cats if c.cat_type == "+"]
    both = [c for c in all_cats if c.cat_type == "*"]

    def fmt(cats: list) -> str:
        return ", ".join(c.name for c in cats) if cats else "—"

    text = "⚙️ <b>Категории</b>\n\n"
    text += f"📉 <b>Расходы:</b> {html.escape(fmt(expenses))}\n"
    text += f"📈 <b>Доходы:</b> {html.escape(fmt(incomes))}"
    if both:
        text += f"\n🔄 <b>Оба типа:</b> {html.escape(fmt(both))}"

    kb = user_categories_menu_keyboard()

    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await get_message(target).edit_text(text, reply_markup=kb, parse_mode="HTML")

    await state.set_state(CategoryStates.choosing_action)


async def _continue_to_account_or_save(
    target,
    state: FSMContext,
    user_id: int,
    pending_records: list[dict],
    errors: list[str],
) -> None:
    """After category is resolved: show account keyboard or save immediately."""
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        records_to_add = _deserialize_records(pending_records)
        added = await save_parsed_records(user_id, records_to_add)
        response = format_added_records_response(added, errors)
        if isinstance(target, Message):
            await target.answer(
                response, reply_markup=main_menu_keyboard(), parse_mode="HTML"
            )
            await _send_budget_alerts(target, user_id, added)
        else:
            await get_message(target).edit_text(response, parse_mode="HTML")
            await get_message(target).answer(
                "Выберите действие:", reply_markup=main_menu_keyboard()
            )
            await _send_budget_alerts(get_message(target), user_id, added)
        await state.clear()
    else:
        from core.keyboards import account_select_keyboard

        kb = account_select_keyboard(accounts)
        if isinstance(target, Message):
            await target.answer(
                "💳 <b>Выберите счёт:</b>", reply_markup=kb, parse_mode="HTML"
            )
        else:
            await get_message(target).edit_text(
                "💳 <b>Выберите счёт:</b>", reply_markup=kb, parse_mode="HTML"
            )
        await state.update_data(
            pending_records=pending_records, parse_errors=errors, user_id=user_id
        )
        await state.set_state(AddRecord.waiting_for_account)


# ==================== Menu entry ====================


@router.message(F.func(is_categories))
@log_exceptions("Ошибка при открытии категорий")
async def show_categories_menu(message: Message, state: FSMContext, **kwargs) -> None:
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return
    async with async_session() as session:
        await seed_default_categories(session, user_id)
        await session.commit()
    await _show_categories_menu(message, user_id, state)


# ==================== Add category ====================


@router.callback_query(CategoryStates.choosing_action, F.data == "cat_action:add")
@log_exceptions("Ошибка при добавлении категории")
async def handle_cat_add_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📉 Расход", callback_data="cat_type:-"),
                InlineKeyboardButton(text="📈 Доход", callback_data="cat_type:+"),
                InlineKeyboardButton(text="🔄 Оба", callback_data="cat_type:*"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="cat_type_back")],
        ]
    )
    await get_message(callback).edit_text("Выберите тип новой категории:", reply_markup=kb)
    await state.set_state(CategoryStates.choosing_type_for_add)
    await callback.answer()


@router.callback_query(CategoryStates.choosing_type_for_add, F.data == "cat_type_back")
async def handle_cat_type_back(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = kwargs.get("user_id") or (await state.get_data()).get("user_id")
    if user_id:
        await _show_categories_menu(callback, user_id, state)
    else:
        await get_message(callback).edit_text("Выберите действие:")
        await get_message(callback).answer(
            "Выберите действие:", reply_markup=main_menu_keyboard()
        )
        await state.clear()
    await callback.answer()


@router.callback_query(
    CategoryStates.choosing_type_for_add, F.data.startswith("cat_type:")
)
@log_exceptions("Ошибка при выборе типа категории")
async def handle_cat_type_selected(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    cat_type = (callback.data or "").split(":")[1]
    await state.update_data(new_cat_type=cat_type)
    type_label = {"+": "дохода", "-": "расхода", "*": "для обоих типов"}[cat_type]
    await get_message(callback).edit_text(
        f"Введите название новой категории {type_label}\n"
        f"<i>(макс. {MAX_CATEGORY_LENGTH} символов)</i>",
        parse_mode="HTML",
    )
    await state.set_state(CategoryStates.entering_name_for_add)
    await callback.answer()


@router.message(CategoryStates.entering_name_for_add)
@log_exceptions("Ошибка при сохранении категории")
async def handle_cat_name_input(message: Message, state: FSMContext, **kwargs) -> None:
    name = clean_text(message.text or "").capitalize()
    if not name or len(name) > MAX_CATEGORY_LENGTH:
        await message.answer(
            f"Название должно быть от 1 до {MAX_CATEGORY_LENGTH} символов."
        )
        return

    data = await state.get_data()
    cat_type = data.get("new_cat_type", "-")
    user_id = kwargs.get("user_id") or data.get("user_id")
    if not user_id:
        user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    assert isinstance(user_id, int)

    async with async_session() as session:
        cat = await add_user_category(session, user_id, name, cat_type)
        if cat is not None:
            await session.commit()

    if cat is None:
        await message.answer(
            "Не удалось создать категорию. Возможно, она уже существует или достигнут лимит (30)."
        )
        return

    await message.answer(
        f"✅ Категория <b>{html.escape(name)}</b> добавлена.", parse_mode="HTML"
    )
    await _show_categories_menu(message, user_id, state)


# ==================== Rename category ====================


@router.callback_query(CategoryStates.choosing_action, F.data == "cat_action:rename")
@log_exceptions("Ошибка при переименовании категории")
async def handle_cat_rename_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = kwargs.get("user_id") or (await state.get_data()).get("user_id")
    if not user_id:
        user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        cats = await get_user_categories(session, user_id)

    if not cats:
        await callback.answer("Нет категорий для переименования.", show_alert=True)
        return

    await state.update_data(user_id=user_id)
    await get_message(callback).edit_text(
        "Выберите категорию для переименования:",
        reply_markup=category_manage_keyboard(cats, "rename"),
    )
    await state.set_state(CategoryStates.choosing_category_to_rename)
    await callback.answer()


@router.callback_query(
    CategoryStates.choosing_category_to_rename, F.data == "cat_manage_back"
)
async def handle_rename_back(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    data = await state.get_data()
    user_id = data.get("user_id") or kwargs.get("user_id")
    if user_id:
        await _show_categories_menu(callback, user_id, state)
    await callback.answer()


@router.callback_query(
    CategoryStates.choosing_category_to_rename, F.data.startswith("cat_rename:")
)
@log_exceptions("Ошибка при выборе категории для переименования")
async def handle_cat_rename_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    cat_id = int((callback.data or "").split(":")[1])
    data = await state.get_data()
    user_id = data.get("user_id") or kwargs.get("user_id")
    assert isinstance(user_id, int)

    async with async_session() as session:
        cat = await session.scalar(
            select(UserCategory).where(
                UserCategory.id == cat_id, UserCategory.user_id == user_id
            )
        )
    if not cat:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    await state.update_data(rename_cat_id=cat_id, rename_cat_old=cat.name)
    await get_message(callback).edit_text(
        f"Текущее название: <code>{html.escape(cat.name)}</code>\n\n"
        f"Введите новое название (макс. {MAX_CATEGORY_LENGTH} символов):",
        parse_mode="HTML",
    )
    await state.set_state(CategoryStates.entering_new_name)
    await callback.answer()


@router.message(CategoryStates.entering_new_name)
@log_exceptions("Ошибка при переименовании")
async def handle_cat_new_name_input(
    message: Message, state: FSMContext, **kwargs
) -> None:
    new_name = clean_text(message.text or "").capitalize()
    if not new_name or len(new_name) > MAX_CATEGORY_LENGTH:
        await message.answer(
            f"Название должно быть от 1 до {MAX_CATEGORY_LENGTH} символов."
        )
        return

    data = await state.get_data()
    cat_id = data.get("rename_cat_id")
    assert isinstance(cat_id, int)
    user_id = kwargs.get("user_id") or data.get("user_id")
    if not user_id:
        user_id = await get_user_id_from_event(message, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        ok = await rename_user_category(session, cat_id, user_id, new_name)
        if ok:
            await session.commit()

    if not ok:
        await message.answer(
            f"Не удалось переименовать. Категория с именем <b>{html.escape(new_name)}</b> уже существует.",
            parse_mode="HTML",
        )
        return

    old_name = data.get("rename_cat_old", "")
    await message.answer(
        f"✅ <b>{html.escape(old_name)}</b> → <b>{html.escape(new_name)}</b>",
        parse_mode="HTML",
    )
    await _show_categories_menu(message, user_id, state)


# ==================== Delete category ====================


@router.callback_query(CategoryStates.choosing_action, F.data == "cat_action:delete")
@log_exceptions("Ошибка при удалении категории")
async def handle_cat_delete_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = kwargs.get("user_id") or (await state.get_data()).get("user_id")
    if not user_id:
        user_id = await get_user_id_from_event(callback, kwargs)
    assert isinstance(user_id, int)

    async with async_session() as session:
        cats = await get_user_categories(session, user_id)

    if not cats:
        await callback.answer("Нет категорий для удаления.", show_alert=True)
        return

    await state.update_data(user_id=user_id)
    await get_message(callback).edit_text(
        "Выберите категорию для удаления:",
        reply_markup=category_manage_keyboard(cats, "delete"),
    )
    await state.set_state(CategoryStates.choosing_category_to_delete)
    await callback.answer()


@router.callback_query(
    CategoryStates.choosing_category_to_delete, F.data == "cat_manage_back"
)
async def handle_delete_back(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    data = await state.get_data()
    user_id = data.get("user_id") or kwargs.get("user_id")
    if user_id:
        await _show_categories_menu(callback, user_id, state)
    await callback.answer()


@router.callback_query(
    CategoryStates.choosing_category_to_delete, F.data.startswith("cat_delete:")
)
@log_exceptions("Ошибка при выборе категории для удаления")
async def handle_cat_delete_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    cat_id = int((callback.data or "").split(":")[1])
    data = await state.get_data()
    user_id = data.get("user_id") or kwargs.get("user_id")
    assert isinstance(user_id, int)

    async with async_session() as session:
        cat = await session.scalar(
            select(UserCategory).where(
                UserCategory.id == cat_id, UserCategory.user_id == user_id
            )
        )
        record_count = (
            await count_records_with_category(session, user_id, cat.name) if cat else 0
        )

    if not cat:
        await callback.answer("Категория не найдена.", show_alert=True)
        return

    await state.update_data(delete_cat_id=cat_id, delete_cat_name=cat.name)

    records_warning = (
        f"\n\n⚠️ Используется в <b>{record_count}</b> записях. Записи останутся без изменений."
        if record_count
        else ""
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"cat_delete_confirm:{cat_id}"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="cat_delete_cancel"),
            ]
        ]
    )
    await get_message(callback).edit_text(
        f"Удалить категорию <b>{html.escape(cat.name)}</b>?{records_warning}",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await state.set_state(CategoryStates.confirming_delete)
    await callback.answer()


@router.callback_query(CategoryStates.confirming_delete, F.data == "cat_delete_cancel")
async def handle_cat_delete_cancel(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    data = await state.get_data()
    user_id = data.get("user_id") or kwargs.get("user_id")
    if user_id:
        await _show_categories_menu(callback, user_id, state)
    await callback.answer()


@router.callback_query(
    CategoryStates.confirming_delete, F.data.startswith("cat_delete_confirm:")
)
@log_exceptions("Ошибка при подтверждении удаления категории")
async def handle_cat_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    cat_id = int((callback.data or "").split(":")[1])
    data = await state.get_data()
    user_id = data.get("user_id") or kwargs.get("user_id")
    assert isinstance(user_id, int)
    cat_name = data.get("delete_cat_name", "")

    async with async_session() as session:
        ok = await delete_user_category(session, cat_id, user_id)
        if ok:
            await session.commit()

    if ok:
        await get_message(callback).edit_text(
            f"🗑 Категория <b>{html.escape(cat_name)}</b> удалена.", parse_mode="HTML"
        )
    else:
        await get_message(callback).edit_text("Не удалось удалить категорию.")

    await _show_categories_menu(callback, user_id, state)
    await callback.answer()


# ==================== Category selection flow for records ====================


@router.callback_query(
    CategoryStates.choosing_category_for_record, F.data.startswith("cat_select:")
)
@log_exceptions("Ошибка при выборе категории для записи")
async def handle_cat_select_for_record(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    cat_id = int((callback.data or "").split(":")[1])
    data = await state.get_data()
    user_id = data.get("user_id")
    assert isinstance(user_id, int)
    pending_records = data.get("pending_records", [])
    errors = data.get("parse_errors", [])
    original_description = data.get("original_description", "")

    async with async_session() as session:
        cat = await session.scalar(
            select(UserCategory).where(
                UserCategory.id == cat_id, UserCategory.user_id == user_id
            )
        )
        if cat and pending_records:
            pending_records[0]["cat"] = cat.name
            if original_description:
                await learn_keyword(session, user_id, original_description, cat_id)
        await session.commit()

    await callback.answer()
    await _continue_to_account_or_save(
        callback, state, user_id, pending_records, errors
    )


@router.callback_query(
    CategoryStates.choosing_category_for_record, F.data == "cat_select_other"
)
async def handle_cat_select_other(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    await get_message(callback).edit_text("✏️ Введите название категории:")
    await state.set_state(CategoryStates.entering_category_for_record)
    await callback.answer()


@router.message(CategoryStates.entering_category_for_record)
@log_exceptions("Ошибка при вводе категории вручную")
async def handle_cat_manual_input(
    message: Message, state: FSMContext, **kwargs
) -> None:
    cat_name = clean_text(message.text or "").capitalize()
    if not cat_name or len(cat_name) > MAX_CATEGORY_LENGTH:
        await message.answer(
            f"Название должно быть от 1 до {MAX_CATEGORY_LENGTH} символов."
        )
        return

    data = await state.get_data()
    user_id = data.get("user_id")
    assert isinstance(user_id, int)
    pending_records = data.get("pending_records", [])
    errors = data.get("parse_errors", [])

    if pending_records:
        pending_records[0]["cat"] = cat_name

    await _continue_to_account_or_save(message, state, user_id, pending_records, errors)


# ==================== Suggested category confirmation ====================


@router.callback_query(
    CategoryStates.confirming_suggested_category, F.data == "cat_suggest_yes"
)
@log_exceptions("Ошибка при подтверждении категории")
async def handle_suggest_yes(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    data = await state.get_data()
    user_id = data.get("user_id")
    assert isinstance(user_id, int)
    suggested = data.get("suggested_category", "")
    pending_records = data.get("pending_records", [])
    errors = data.get("parse_errors", [])
    original_description = data.get("original_description", "")

    if pending_records and suggested:
        pending_records[0]["cat"] = suggested
        if original_description:
            async with async_session() as session:
                from sqlalchemy import select as _select

                cat = await session.scalar(
                    _select(UserCategory).where(
                        UserCategory.user_id == user_id, UserCategory.name == suggested
                    )
                )
                if cat:
                    await learn_keyword(session, user_id, original_description, cat.id)
                await session.commit()

    await callback.answer()
    await _continue_to_account_or_save(
        callback, state, user_id, pending_records, errors
    )


@router.callback_query(
    CategoryStates.confirming_suggested_category, F.data == "cat_suggest_other"
)
@log_exceptions("Ошибка при выборе другой категории")
async def handle_suggest_other(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    data = await state.get_data()
    user_id = data.get("user_id")
    assert isinstance(user_id, int)
    pending_op = data.get("pending_op", "")

    async with async_session() as session:
        cats = await get_user_categories(session, user_id, pending_op or None)

    await get_message(callback).edit_text(
        "📁 Выберите категорию:",
        reply_markup=category_select_keyboard(cats),
    )
    await state.set_state(CategoryStates.choosing_category_for_record)
    await callback.answer()


@router.callback_query(
    CategoryStates.confirming_suggested_category, F.data == "cat_suggest_manual"
)
async def handle_suggest_manual(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    await get_message(callback).edit_text("✏️ Введите название категории:")
    await state.set_state(CategoryStates.entering_category_for_record)
    await callback.answer()
