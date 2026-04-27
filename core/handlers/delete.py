"""Handlers for deleting records."""

from calendar import monthrange
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.database.models import async_session
from core.database.requests import (
    count_records,
    delete_record,
    get_records,
    get_user_by_tg_id,
)
from core.utils import get_available_years_and_months
from core.keyboards import (
    confirm_delete_keyboard,
    delete_period_keyboard,
    get_delete_months_keyboard,
    get_delete_years_keyboard,
)
from core.utils import RU_MONTHS, log_exceptions
from config import RECORDS_PER_PAGE, TIMEZONE

from .common import MenuStates, is_delete

router = Router()


def build_delete_keyboard(
    page_records: list[dict[str, Any]],
    page: int,
    total_pages: int,
) -> InlineKeyboardBuilder:
    """Формирует клавиатуру со списком записей для удаления."""
    kb = InlineKeyboardBuilder()

    for r in page_records:
        icon = "🛒" if r["operation"] == "-" else "💵"
        short_date = r["created_at"].strftime("%d.%m.%y")
        cat = r["category"][:12] + "…" if len(r["category"]) > 12 else r["category"]
        text = f"{icon} {r['amount']:.0f}₽ {cat} {short_date}"
        kb.button(text=text, callback_data=f"del_record:{r['id']}")

    num_records = len(page_records)
    if num_records > 0:
        kb.adjust(*([1] * num_records))

    if total_pages > 1:
        nav_kb = InlineKeyboardBuilder()
        if page > 0:
            nav_kb.button(text="◀ Назад", callback_data=f"del_page:{page - 1}")
        nav_kb.button(text=f"{page + 1}/{total_pages}", callback_data="del_page:noop")
        if page < total_pages - 1:
            nav_kb.button(text="Вперёд ▶", callback_data=f"del_page:{page + 1}")
        nav_kb.adjust(3)
        kb.attach(nav_kb)

    cancel_kb = InlineKeyboardBuilder()
    cancel_kb.button(text="Отмена", callback_data="cancel")
    kb.attach(cancel_kb)

    return kb


@router.message(StateFilter("*"), F.func(is_delete))
@log_exceptions("Ошибка при показе меню удаления")
async def menu_delete(message: Message, state: FSMContext, **kwargs) -> None:
    """Кнопка Удалить — показываем выбор периода."""
    await state.clear()
    await message.answer(
        "За какой период показать записи для удаления?",
        reply_markup=delete_period_keyboard(),
    )
    await state.set_state(MenuStates.waiting_for_delete_period)


@router.callback_query(MenuStates.waiting_for_delete_period)
@log_exceptions("Ошибка при получении записей для удаления")
async def menu_delete_period(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка выбора периода или навигации по годам/месяцам."""

    if callback.data == "del_select_month":
        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                return

            years_months = await get_available_years_and_months(session, user.id)

        if not years_months:
            await callback.message.edit_text("У вас пока нет записей.")
            await state.clear()
            await callback.answer()
            return

        years = list(years_months.keys())
        await state.update_data(delete_years_months=years_months)
        await callback.message.edit_text(
            "Выберите год:",
            reply_markup=get_delete_years_keyboard(years),
        )
        await callback.answer()
        return

    if callback.data.startswith("del_year:"):
        try:
            year = int(callback.data.split(":")[1])
        except (IndexError, ValueError):
            await callback.answer("Некорректные данные.")
            return

        data = await state.get_data()
        years_months = data.get("delete_years_months", {})
        months = years_months.get(year, [])

        if not months:
            await callback.answer("Нет записей за этот год.")
            return

        await state.update_data(delete_selected_year=year)
        await callback.message.edit_text(
            f"<b>{year}</b> — выберите месяц:",
            reply_markup=get_delete_months_keyboard(year, months),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if callback.data.startswith("del_month:"):
        try:
            parts = callback.data.split(":")
            year = int(parts[1])
            month = int(parts[2])
        except (IndexError, ValueError):
            await callback.answer("Некорректные данные.")
            return

        start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=ZoneInfo(TIMEZONE))
        last_day = monthrange(year, month)[1]
        end_date = datetime(year, month, last_day, 23, 59, 59, tzinfo=ZoneInfo(TIMEZONE))

        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                return

            total_count = await count_records(session, user.id, "range", start_date, end_date)
            if total_count == 0:
                await callback.answer("Записей за этот месяц нет.")
                return

            total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
            records = await get_records(
                session, user.id, "range", start_date, end_date,
                limit=RECORDS_PER_PAGE, offset=0
            )

        records_data = [r.to_dict(include_id=True) for r in records]

        await state.update_data(
            delete_period="range",
            delete_date_from=start_date,
            delete_date_to=end_date,
            delete_page=0,
            delete_total_count=total_count,
            delete_total_pages=total_pages,
            delete_selected_year=year,
            delete_selected_month=month,
        )

        kb = build_delete_keyboard(records_data, 0, total_pages)
        await callback.message.edit_text(
            f"Записи за {RU_MONTHS[month]} {year} (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await state.set_state(MenuStates.waiting_for_delete_record)
        await callback.answer()
        return

    if callback.data == "del_back_to_period":
        await callback.message.edit_text(
            "За какой период показать записи для удаления?",
            reply_markup=delete_period_keyboard(),
        )
        await callback.answer()
        return

    if callback.data == "del_back_to_years":
        data = await state.get_data()
        years_months = data.get("delete_years_months", {})
        years = list(years_months.keys())

        await callback.message.edit_text(
            "Выберите год:",
            reply_markup=get_delete_years_keyboard(years),
        )
        await callback.answer()
        return

    if callback.data.startswith("del_period:"):
        try:
            period = callback.data.split(":")[1]
        except (IndexError, AttributeError):
            await callback.answer("Некорректные данные.")
            await state.clear()
            return

        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                return

            total_count = await count_records(session, user.id, period)
            if total_count == 0:
                await callback.message.edit_text("Записей за выбранный период нет.")
                await state.clear()
                await callback.answer()
                return

            total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
            records = await get_records(session, user.id, period, limit=RECORDS_PER_PAGE, offset=0)

        records_data = [r.to_dict(include_id=True) for r in records]

        await state.update_data(
            delete_period=period,
            delete_page=0,
            delete_total_count=total_count,
            delete_total_pages=total_pages,
        )

        kb = build_delete_keyboard(records_data, 0, total_pages)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await state.set_state(MenuStates.waiting_for_delete_record)
        await callback.answer()
        return

    await callback.answer("Некорректные данные.")


@router.callback_query(MenuStates.waiting_for_delete_record)
@log_exceptions("Ошибка при удалении записи")
async def menu_delete_record(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка: навигация по страницам или удаление выбранной записи."""
    data = await state.get_data()
    period = data.get("delete_period")
    total_count = data.get("delete_total_count", 0)
    date_from = data.get("delete_date_from")
    date_to = data.get("delete_date_to")

    if not period:
        await callback.answer("Данные устарели. Попробуйте снова.")
        await state.clear()
        return

    if callback.data.startswith("del_page:"):
        try:
            page_str = callback.data.split(":")[1]
            if page_str == "noop":
                await callback.answer()
                return
            new_page = int(page_str)
        except (IndexError, ValueError, AttributeError):
            await callback.answer("Некорректные данные.")
            return

        total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
        if new_page < 0 or new_page >= total_pages:
            await callback.answer("Страница не существует.")
            return

        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                await callback.answer()
                return

            offset = new_page * RECORDS_PER_PAGE
            records = await get_records(
                session, user.id, period, date_from, date_to,
                limit=RECORDS_PER_PAGE, offset=offset
            )

        records_data = [r.to_dict(include_id=True) for r in records]

        await state.update_data(delete_page=new_page)
        kb = build_delete_keyboard(records_data, new_page, total_pages)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await callback.answer()
        return

    if callback.data.startswith("del_record:"):
        try:
            record_id = int(callback.data.split(":")[1])
        except (IndexError, ValueError, AttributeError):
            await callback.answer("Некорректные данные.")
            await state.clear()
            return

        await state.update_data(delete_record_id=record_id)
        await callback.message.edit_text(
            "⚠️ Вы уверены, что хотите удалить эту запись?",
            reply_markup=confirm_delete_keyboard(record_id),
        )
        await state.set_state(MenuStates.waiting_for_delete_confirm)
        await callback.answer()
        return

    await callback.answer("Некорректные данные.")
    await state.clear()


@router.callback_query(MenuStates.waiting_for_delete_confirm)
@log_exceptions("Ошибка при подтверждении удаления")
async def menu_delete_confirm(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Обработка подтверждения или отмены удаления."""
    data = await state.get_data()
    period = data.get("delete_period")
    current_page = data.get("delete_page", 0)
    date_from = data.get("delete_date_from")
    date_to = data.get("delete_date_to")

    if callback.data == "cancel_del":
        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.message.edit_text("Пользователь не найден.")
                await state.clear()
                await callback.answer()
                return

            total_count = await count_records(session, user.id, period, date_from, date_to)
            if total_count == 0:
                await callback.message.edit_text("Записей нет.")
                await state.clear()
                await callback.answer()
                return

            total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
            if current_page >= total_pages:
                current_page = total_pages - 1

            offset = current_page * RECORDS_PER_PAGE
            records = await get_records(
                session, user.id, period, date_from, date_to,
                limit=RECORDS_PER_PAGE, offset=offset
            )

        records_data = [r.to_dict(include_id=True) for r in records]
        kb = build_delete_keyboard(records_data, current_page, total_pages)
        await callback.message.edit_text(
            f"Выберите запись для удаления (всего: {total_count}):",
            reply_markup=kb.as_markup(),
        )
        await state.set_state(MenuStates.waiting_for_delete_record)
        await callback.answer("Удаление отменено")
        return

    if callback.data.startswith("confirm_del:"):
        try:
            record_id = int(callback.data.split(":")[1])
        except (IndexError, ValueError, AttributeError):
            await callback.answer("Некорректные данные.")
            await state.clear()
            return

        async with async_session() as session:
            user = await get_user_by_tg_id(session, callback.from_user.id)
            if not user:
                await callback.answer("Пользователь не найден.")
                await state.clear()
                return

            user_id = user.id
            deleted = await delete_record(session, user_id, record_id)

            if deleted:
                await callback.answer("✅ Запись удалена!")

                new_total = await count_records(session, user_id, period, date_from, date_to)

                if new_total == 0:
                    await callback.message.edit_text("Все записи удалены.")
                    await state.clear()
                    return

                total_pages = (new_total + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
                if current_page >= total_pages:
                    current_page = total_pages - 1

                offset = current_page * RECORDS_PER_PAGE
                records = await get_records(
                    session, user_id, period, date_from, date_to,
                    limit=RECORDS_PER_PAGE, offset=offset
                )

                records_data = [r.to_dict(include_id=True) for r in records]

                await state.update_data(
                    delete_page=current_page,
                    delete_total_count=new_total,
                    delete_total_pages=total_pages,
                )

                kb = build_delete_keyboard(records_data, current_page, total_pages)
                await callback.message.edit_text(
                    f"Выберите запись для удаления (всего: {new_total}):",
                    reply_markup=kb.as_markup(),
                )
                await state.set_state(MenuStates.waiting_for_delete_record)
            else:
                await callback.answer("⚠️ Запись не найдена или уже удалена.")
                await state.set_state(MenuStates.waiting_for_delete_record)
        return

    await callback.answer("Некорректные данные.")
    await state.clear()
