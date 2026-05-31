"""Reply-подменю «Ещё»: переключение между главным меню и вторым экраном."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.keyboards import main_menu_keyboard, more_menu_keyboard
from core.utils import log_exceptions

from .common import is_back, is_more

router = Router()


@router.message(StateFilter("*"), F.func(is_more))
@log_exceptions("Ошибка при открытии меню «Ещё»")
async def open_more(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка «Ещё» — показывает второй экран меню."""
    await state.clear()
    await message.answer("Дополнительно:", reply_markup=more_menu_keyboard())


@router.message(StateFilter("*"), F.func(is_back))
@log_exceptions("Ошибка при возврате в главное меню")
async def back_to_main(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка «Назад» — возврат к главному меню."""
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard())
