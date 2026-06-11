"""Handlers for main menu commands: /start, /help, /cancel."""

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.database.models import async_session
from core.database.requests import set_user
from core.keyboards import main_menu_keyboard
from core.utils import log_exceptions

from .common import get_message

router = Router()


@router.message(Command("start"))
@log_exceptions("Ошибка при инициализации пользователя")
async def handle_start(message: Message, **kwargs) -> None:
    """Команда /start — регистрация пользователя и показ главного меню."""
    assert message.from_user is not None
    async with async_session() as session:
        user = await set_user(
            session,
            message.from_user.id,
            name=message.from_user.full_name,
            default_account_name="Наличные",
        )
        if not user:
            await message.answer("Ошибка при регистрации. Попробуйте позже.")
            return

    first_name = html.escape(message.from_user.first_name or "друг")

    welcome_text = f"""
💰 <b>Привет, {first_name}!</b>

Я твой персональный финансовый помощник.

<b>Что я умею:</b>
➕ Записывать доходы и расходы
📊 Строить отчёты и графики
🕘 Историю с поиском и фильтрами
💼 Управлять счетами и переводами
🏦 Вести накопления и цели
💸 Учитывать долги и займы
🗂️ Настраивать категории
🎯 Контролировать бюджеты
📤 Экспортировать и импортировать данные

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
@log_exceptions("Ошибка при отмене")
async def handle_cancel_callback(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка нажатия кнопки Отмена в inline-клавиатурах."""
    await state.clear()
    await get_message(callback).edit_text("Операция отменена.")
    await get_message(callback).answer(
        "Выберите действие:", reply_markup=main_menu_keyboard()
    )
    await callback.answer()


@router.message(Command("help"))
async def handle_help(message: Message, state: FSMContext, **kwargs) -> None:
    """Команда /help — справка по боту. Сбрасывает FSM, чтобы /help не зависал в state."""
    await state.clear()
    help_text = """<b>Справка по боту</b>

<b>Команды:</b>
/start — начать работу с ботом
/help — показать эту справку
/cancel — отменить текущую операцию
/backup — скачать резервную копию данных
/notifications — настройка уведомлений

<b>Основные функции:</b>
<b>Доход / Расход</b> — добавить запись
<b>История</b> — операции за период, поиск и фильтры
<b>Отчёт</b> — по категориям, структура по месяцам, годовой
<b>Счета</b> — управление счетами, переводы, коррекция баланса
<b>Накопления</b> — снимки баланса по дням, динамика
<b>Цели</b> — финансовые цели, пополнение, снятие
<b>Долги</b> — кто кому должен, погашения, напоминания о сроке
<b>Платежи</b> — напоминания о разовых и регулярных платежах, оплата с записью расхода в баланс
<b>Семья</b> — общий бюджет: история и отчёты на несколько человек
<b>Категории</b> — добавить / переименовать / удалить
<b>Бюджеты</b> — лимиты расходов по категориям
<b>Настройки</b> — описание записей, уведомления
<b>Экспорт</b> — выгрузить данные в Excel
<b>Импорт</b> — загрузить данные из файла
<b>Удалить запись</b> — удалить операцию

<b>Поиск в истории:</b>
<code>кофе</code> — по тексту категории
<code>&gt;1000</code> — сумма больше 1000
<code>&lt;500</code> — сумма меньше 500
<code>=350</code> — точная сумма

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
