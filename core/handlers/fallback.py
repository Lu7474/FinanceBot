"""Fallback handler for unknown messages."""

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message

from core.keyboards import main_menu_keyboard

router = Router()


@router.message(StateFilter(None), F.text)
async def handle_unknown_message(message: Message, **kwargs) -> None:
    """Обработка текстовых сообщений, не подходящих под другие хендлеры."""
    await message.answer(
        "🤔 Не понял команду.\n\n"
        "<b>Используйте кнопки меню</b> или быстрый ввод:\n"
        "<code>+1000 зарплата</code> — доход\n"
        "<code>-500 еда</code> — расход",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
