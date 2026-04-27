"""Handlers for main menu commands: /start, /help, /cancel."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from core.database.models import async_session
from core.database.requests import create_account, get_accounts, set_user
from core.keyboards import main_menu_keyboard
from core.utils import log_exceptions

from .common import get_user_id_from_event

router = Router()


@router.message(Command("start"))
@log_exceptions("Ошибка при инициализации пользователя")
async def handle_start(message: Message, **kwargs) -> None:
    """Команда /start — регистрация пользователя и показ главного меню."""
    async with async_session() as session:
        user = await set_user(session, message.from_user.id, name=message.from_user.full_name)
        if not user:
            await message.answer("Ошибка при регистрации. Попробуйте позже.")
            return
        accounts = await get_accounts(session, user.id)
        if not accounts:
            await create_account(session, user.id, "Наличные")

    first_name = message.from_user.first_name or "друг"

    welcome_text = f"""
💰 <b>Привет, {first_name}!</b>

Я твой персональный финансовый помощник.
Помогу вести учёт доходов и расходов.

<b>Что я умею:</b>
➕ Записывать доходы
➖ Записывать расходы
📊 Строить отчёты по категориям
🕘 Показывать историю операций
🗑️ Удалять ненужные записи

<b>Быстрый ввод:</b>
<code>+5000 зарплата</code>
<code>-200 кофе</code>

Выбери действие в меню ниже 👇
"""

    await message.answer(
        welcome_text.strip(),
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
@log_exceptions("Ошибка при отмене")
async def handle_cancel(message: Message, state: FSMContext, **kwargs) -> None:
    """Команда /cancel — отмена текущей операции и возврат в главное меню."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активной операции для отмены.")
        return

    await state.clear()
    await message.answer("Операция отменена.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "cancel")
async def handle_cancel_callback(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Обработка нажатия кнопки Отмена в inline-клавиатурах."""
    await state.clear()
    await callback.message.edit_text("Операция отменена.")
    await callback.answer()


@router.message(Command("help"))
async def handle_help(message: Message, **kwargs) -> None:
    """Команда /help — справка по боту."""
    help_text = """<b>Справка по боту</b>

<b>Команды:</b>
/start — начать работу с ботом
/help — показать эту справку
/cancel — отменить текущую операцию

<b>Основные функции:</b>
<b>Доход</b> — добавить доход
<b>Расход</b> — добавить расход
<b>История</b> — просмотр операций за период
<b>Отчёт</b> — график доходов/расходов по месяцам
<b>Удалить</b> — удалить запись

<b>Формат ввода:</b>
Одна запись: <code>1000 еда</code>
Несколько записей (каждая с новой строки):
<code>1000 зарплата
500 еда
200 транспорт</code>

<b>Старые записи (с датой):</b>
<code>27.01 500 продукты</code> — 27 января
<code>15.12.25 1000 подарок</code> — 15.12.2025

<b>Быстрый ввод (без кнопки):</b>
<code>+1000 зарплата</code> — доход
<code>-500 еда</code> — расход
<code>27.01 -350 магазин</code> — расход 27.01"""

    await message.answer(help_text, parse_mode="HTML")
