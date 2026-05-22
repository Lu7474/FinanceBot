"""Handlers for notification settings: toggle, onboarding, /notifications command."""


from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from core.database.models import User, async_session
from core.keyboards import notification_settings_keyboard
from core.utils import log_exceptions

from .common import get_message, get_user_id_from_event

router = Router()

_NOTIFY_FIELDS = {
    "weekly": "notify_weekly",
    "monthly": "notify_monthly",
    "daily": "notify_daily",
    "reminder": "notify_reminder",
}

_NOTIFY_LABELS = {
    "weekly": "Еженедельная сводка",
    "monthly": "Ежемесячная сводка",
    "daily": "Ежедневные итоги",
    "reminder": "Напоминание о записи",
}


async def _get_user_obj(session, user_id: int) -> User | None:
    """Fetch User by internal id."""
    from sqlalchemy import select

    return await session.scalar(select(User).where(User.id == user_id))


# ==================== Settings page ====================


@router.message(Command("notifications"))
@log_exceptions("Ошибка в настройках уведомлений")
async def handle_notifications_command(
    message: Message, **kwargs
) -> None:
    """Show notification settings via /notifications command."""
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
        "🔔 <b>Уведомления</b>\n\nНажмите на пункт чтобы включить/выключить.",
        reply_markup=notification_settings_keyboard(user),
        parse_mode="HTML",
    )


# ==================== Toggle ====================


@router.callback_query(F.data.startswith("notify_toggle:"))
@log_exceptions("Ошибка при переключении уведомления")
async def handle_notify_toggle(callback: CallbackQuery, **kwargs) -> None:
    """Toggle a single notify_* flag and refresh the keyboard."""
    key = (callback.data or "").split(":")[1]
    field = _NOTIFY_FIELDS.get(key)
    if not field:
        await callback.answer("Неизвестная настройка.")
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
        setattr(user, field, not getattr(user, field))
        await session.commit()
        await session.refresh(user)

    try:
        await get_message(callback).edit_reply_markup(
            reply_markup=notification_settings_keyboard(user)
        )
    except TelegramBadRequest:
        pass

    label = _NOTIFY_LABELS.get(key, key)
    state = "включено" if getattr(user, field) else "выключено"
    await callback.answer(f"{label}: {state}")


# ==================== Onboarding ====================


@router.callback_query(F.data == "notify_enable_all")
@log_exceptions("Ошибка при включении уведомлений")
async def handle_notify_enable_all(callback: CallbackQuery, **kwargs) -> None:
    """Enable all notify_* flags at once."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    async with async_session() as session:
        user = await _get_user_obj(session, user_id)
        if not user:
            await callback.answer("Ошибка.")
            return
        user.notify_weekly = True
        user.notify_monthly = True
        user.notify_daily = True
        user.notify_reminder = True
        await session.commit()

    try:
        await get_message(callback).edit_text(
            "🔔 Уведомления включены! Настроить можно командой /notifications."
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


@router.callback_query(F.data == "notify_skip")
@log_exceptions("Ошибка при отклонении уведомлений")
async def handle_notify_skip(callback: CallbackQuery, **kwargs) -> None:
    """Dismiss onboarding without changing settings."""
    try:
        await get_message(callback).edit_text(
            "Хорошо. Включить можно позже командой /notifications."
        )
    except TelegramBadRequest:
        pass

    await callback.answer()
