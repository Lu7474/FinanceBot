"""Handlers for the Settings section: description toggle + notifications link."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.database.models import User, async_session
from core.keyboards import notification_settings_keyboard, settings_menu_keyboard
from core.utils import log_exceptions

from .common import get_message, get_user_id_from_event, is_settings

router = Router()


async def _get_user_obj(session, user_id: int) -> User | None:
    """Fetch User by internal id."""
    from sqlalchemy import select

    return await session.scalar(select(User).where(User.id == user_id))


# ==================== Settings page ====================


@router.message(StateFilter("*"), F.func(is_settings))
@log_exceptions("Ошибка при открытии настроек")
async def open_settings(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка «Настройки» — показывает inline-меню настроек."""
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return

    async with async_session() as session:
        user = await _get_user_obj(session, user_id)

    if not user:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return

    await message.answer(
        "⚙️ <b>Настройки</b>\n\nНажмите на пункт чтобы изменить.",
        reply_markup=settings_menu_keyboard(user),
        parse_mode="HTML",
    )


# ==================== Toggle: description ====================


@router.callback_query(F.data == "settings:toggle_desc")
@log_exceptions("Ошибка при переключении описания записей")
async def handle_toggle_description(callback: CallbackQuery, **kwargs) -> None:
    """Toggle use_description flag and refresh the keyboard."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        user = await _get_user_obj(session, user_id)
        if not user:
            await callback.answer("Ошибка.")
            return
        user.use_description = not user.use_description
        await session.commit()
        await session.refresh(user)

    try:
        await get_message(callback).edit_reply_markup(
            reply_markup=settings_menu_keyboard(user)
        )
    except TelegramBadRequest:
        pass

    state = "включено" if user.use_description else "выключено"
    await callback.answer(f"Описание записей: {state}")


# ==================== Link: notifications ====================


@router.callback_query(F.data == "settings:notifications")
@log_exceptions("Ошибка при открытии уведомлений из настроек")
async def handle_open_notifications(callback: CallbackQuery, **kwargs) -> None:
    """Open the notification settings screen from Settings."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        user = await _get_user_obj(session, user_id)

    if not user:
        await callback.answer("Ошибка.")
        return

    try:
        await get_message(callback).edit_text(
            "🔔 <b>Уведомления</b>\n\nНажмите на пункт чтобы включить/выключить.",
            reply_markup=notification_settings_keyboard(user, back_to_settings=True),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


# ==================== Back: notifications → settings ====================


@router.callback_query(F.data == "settings:back")
@log_exceptions("Ошибка при возврате в настройки")
async def handle_back_to_settings(callback: CallbackQuery, **kwargs) -> None:
    """Return to the settings menu from the notifications screen."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        user = await _get_user_obj(session, user_id)

    if not user:
        await callback.answer("Ошибка.")
        return

    try:
        await get_message(callback).edit_text(
            "⚙️ <b>Настройки</b>\n\nНажмите на пункт чтобы изменить.",
            reply_markup=settings_menu_keyboard(user),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

    await callback.answer()
