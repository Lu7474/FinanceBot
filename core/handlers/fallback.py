"""Fallback handler for unknown messages."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from core.keyboards import main_menu_keyboard

router = Router()


@router.callback_query(F.data.startswith("sav_") | F.data.startswith("wealth_"))
async def handle_legacy_capital_callbacks(callback: CallbackQuery, **kwargs) -> None:
    """Старые кнопки накоплений/активов из истории чата — раздел переехал в «Капитал»."""
    await callback.answer(
        "Раздел обновился. Откройте «Капитал» заново.", show_alert=True
    )


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
