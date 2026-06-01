"""Handlers for the Settings section: description mode + notifications link."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.database.models import User, async_session
from core.keyboards import notification_settings_keyboard, settings_menu_keyboard
from core.keyboards.settings import DESCRIPTION_MODES
from core.utils import log_exceptions

from .common import get_message, get_user_id_from_event, is_settings

router = Router()

_MODE_LABELS = dict(DESCRIPTION_MODES)
_VALID_MODES = set(_MODE_LABELS)

_SETTINGS_TEXT = (
    "⚙️ <b>Настройки</b>\n\n"
    "📝 <b>Описание записей</b> — текстовый комментарий к доходу/расходу. "
    "Выберите способ ввода:"
)


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
        _SETTINGS_TEXT,
        reply_markup=settings_menu_keyboard(user),
        parse_mode="HTML",
    )


# ==================== Description mode (radio) ====================


@router.callback_query(F.data.startswith("settings:mode:"))
@log_exceptions("Ошибка при выборе режима описания")
async def handle_set_description_mode(callback: CallbackQuery, **kwargs) -> None:
    """Set the description input mode and refresh the radio keyboard."""
    mode = (callback.data or "").split(":")[2]
    if mode not in _VALID_MODES:
        await callback.answer("Неизвестный режим.")
        return

    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        user = await _get_user_obj(session, user_id)
        if not user:
            await callback.answer("Ошибка.")
            return
        if user.description_mode == mode:
            await callback.answer("Уже выбрано.")
            return
        user.description_mode = mode
        await session.commit()
        await session.refresh(user)

    try:
        await get_message(callback).edit_reply_markup(
            reply_markup=settings_menu_keyboard(user)
        )
    except TelegramBadRequest:
        pass

    await callback.answer(f"Режим: {_MODE_LABELS.get(mode, mode)}")


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
            _SETTINGS_TEXT,
            reply_markup=settings_menu_keyboard(user),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

    await callback.answer()
