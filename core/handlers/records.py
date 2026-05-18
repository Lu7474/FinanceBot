"""Handlers for adding income/expense records."""

import html
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_AMOUNT, MAX_CATEGORY_LENGTH, TIMEZONE
from core.database.models import async_session
from core.database.requests import (
    check_and_alert_budget,
    get_accounts,
    get_user_categories,
    suggest_category,
)
from core.database.requests._common import now_moscow
from core.keyboards import (
    account_select_keyboard,
    category_suggest_keyboard,
    main_menu_keyboard,
)
from core.utils import log_exceptions

from .common import (
    AddRecord,
    CategoryStates,
    MenuStates,
    get_user_id_from_event,
    is_expense,
    is_income,
    is_main_menu_button,
    save_parsed_records,
)

router = Router()


async def _send_budget_alerts(
    message: Message,
    user_id: int,
    added_records: list[tuple],
) -> None:
    """Checks budget thresholds for expense records and sends alerts."""
    async with async_session() as session:
        for op, amount, category, _ in added_records:
            if op == "-":
                try:
                    alerts = await check_and_alert_budget(
                        session, user_id, category, amount
                    )
                    for alert_text in alerts:
                        await message.bot.send_message(message.chat.id, alert_text)
                except Exception:
                    logging.exception("Budget alert error")
        await session.commit()


def _deserialize_records(
    serialized: list[dict],
) -> list[tuple[str, Decimal, str, datetime | None]]:
    """Восстанавливает записи из FSM-state (str → Decimal/datetime)."""
    result = []
    for d in serialized:
        date = datetime.fromisoformat(d["date"]) if d.get("date") else None
        result.append((d["op"], Decimal(d["amount"]), d["cat"], date))
    return result


def format_added_records_response(
    added_records: list[tuple[str, Decimal, str, datetime | None]],
    errors: list[str] | None = None,
    account_name: str | None = None,
) -> str:
    """Формирует красивый ответ после добавления записей."""
    if not added_records:
        return "Не удалось сохранить записи."

    today = datetime.now(ZoneInfo(TIMEZONE)).date()

    if len(added_records) == 1:
        op, amt, cat, record_date = added_records[0]
        icon = "💵" if op == "+" else "🛒"
        op_type = "Доход" if op == "+" else "Расход"

        date_str = ""
        if record_date and record_date.date() != today:
            date_str = f"\n📅 Дата: {record_date.strftime('%d.%m.%Y')}"

        response = f"""
✅ <b>Запись добавлена!</b>

{icon} {op_type}: <b>{amt:,.0f}₽</b>
📁 Категория: {html.escape(cat)}{date_str}
""".replace(",", " ")
    else:
        total_income = sum(amt for op, amt, _, _ in added_records if op == "+")
        total_expense = sum(amt for op, amt, _, _ in added_records if op == "-")

        response = f"✅ <b>Добавлено записей: {len(added_records)}</b>\n\n"
        for op, amt, cat, record_date in added_records:
            icon = "💵" if op == "+" else "🛒"
            sign = "+" if op == "+" else "-"
            date_suffix = ""
            if record_date and record_date.date() != today:
                date_suffix = f" ({record_date.strftime('%d.%m')})"
            response += (
                f"{icon} {sign}{amt:,.0f}₽ — {html.escape(cat)}{date_suffix}\n".replace(
                    ",", " "
                )
            )

        response += "\n"
        if total_income > 0:
            response += f"📈 Доходы: +{total_income:,.0f}₽\n".replace(",", " ")
        if total_expense > 0:
            response += f"📉 Расходы: -{total_expense:,.0f}₽".replace(",", " ")

    if account_name:
        response += f"\n💳 Счёт: {html.escape(account_name)}"

    if errors:
        response += "\n\n⚠️ <b>Ошибки:</b>\n" + "\n".join(errors)

    return response.strip()


def parse_record_line(
    line: str, default_operation: str | None = None
) -> tuple[str, Decimal, str, datetime | None] | None:
    """Парсит строку записи и возвращает (операция, сумма, категория, дата) или None при ошибке.

    Форматы:
    - "1000 еда" — использует default_operation, сегодняшняя дата
    - "+1000 зарплата" — доход, сегодняшняя дата
    - "-500 еда" — расход, сегодняшняя дата
    - "27.01 500 еда" — указанная дата (ДД.ММ текущего года)
    - "27.01.25 500 еда" — указанная дата (ДД.ММ.ГГ)
    """
    line = line.strip()
    if not line:
        return None

    record_date = None

    date_match = re.match(
        r"^(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])(?:\.(\d{2}))?\s+", line
    )
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year_short = date_match.group(3)

        if year_short:
            year = 2000 + int(year_short)
        else:
            year = datetime.now(ZoneInfo(TIMEZONE)).year

        try:
            record_date = datetime(year, month, day, 12, 0, 0)
        except ValueError:
            return None

        now = now_moscow()
        if record_date.date() > (now + timedelta(days=1)).date():
            return None

        line = line[date_match.end() :].strip()

    operation = default_operation
    if line.startswith("+"):
        operation = "+"
        line = line[1:].strip()
    elif line.startswith("-"):
        operation = "-"
        line = line[1:].strip()

    if not operation:
        return None

    match = re.search(r"(\d+(?:[.,]\d+)?)", line)
    if not match:
        return None

    try:
        amount = Decimal(match.group(1).replace(",", "."))
        if amount <= 0 or amount > Decimal(str(MAX_AMOUNT)):
            return None
    except (InvalidOperation, ValueError):
        return None

    category = line.replace(match.group(0), "").strip()
    if not category:
        category = "Не указано"
    else:
        category = category.capitalize()

    if len(category) > MAX_CATEGORY_LENGTH:
        return None

    return operation, amount, category, record_date


async def _maybe_ask_category(
    message_or_callback,
    state: FSMContext,
    user_id: int,
    op: str,
    cat: str,
    serialized: list[dict],
    errors: list[str],
) -> bool:
    """Intercepts flow only when there is a confident category suggestion.

    Returns True if we intercepted (caller should return early).
    Returns False to let the caller continue with the category as typed.
    """
    async with async_session() as session:
        user_cats = await get_user_categories(session, user_id)

    if not user_cats:
        return False

    relevant = [c for c in user_cats if c.cat_type in (op, "*")]
    cat_names_lower = {c.name.lower() for c in relevant}

    if cat.lower() in cat_names_lower:
        return False  # exact match — normal flow

    async with async_session() as session:
        suggestion = await suggest_category(session, user_id, cat, op_type=op)

    if not suggestion:
        return False  # no confident suggestion — respect what user typed

    await state.update_data(
        pending_records=serialized,
        parse_errors=errors,
        user_id=user_id,
        pending_op=op,
        original_description=cat,
        suggested_category=suggestion,
    )

    is_msg = isinstance(message_or_callback, Message)
    text = f"💡 Категория <b>{html.escape(suggestion)}</b>?"
    kb = category_suggest_keyboard()
    if is_msg:
        await message_or_callback.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message_or_callback.message.edit_text(
            text, reply_markup=kb, parse_mode="HTML"
        )
    await state.set_state(CategoryStates.confirming_suggested_category)
    return True


@router.message(
    ~StateFilter(MenuStates.waiting_for_report_type),
    F.func(lambda m: is_income(m) or is_expense(m)),
)
@log_exceptions("Ошибка при обработке операции")
async def handle_income_expense(message: Message, state: FSMContext, **kwargs) -> None:
    """Начало добавления записи: сохраняем тип операции, просим ввести сумму."""
    await state.clear()
    is_income_op = is_income(message)
    operation = "+" if is_income_op else "-"
    await state.update_data(operation=operation)

    if is_income_op:
        prompt_text = "💵 Введите сумму и категорию:\n<code>5000 зарплата</code>"
    else:
        prompt_text = "🛒 Введите сумму и категорию:\n<code>500 продукты</code>"

    await message.answer(prompt_text, parse_mode="HTML")
    await state.set_state(AddRecord.waiting_for_amount)


@router.message(AddRecord.waiting_for_amount, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при добавлении записи")
async def handle_amount_and_category(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Парсинг суммы и категории, затем запрос выбора счёта."""
    data = await state.get_data()
    default_operation = data.get("operation")

    lines = message.text.strip().split("\n")
    records_to_add = []
    errors = []

    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        result = parse_record_line(line, default_operation)
        if result:
            records_to_add.append(result)
        else:
            errors.append(f"Строка {i}: не удалось распознать")

    if not records_to_add:
        await message.answer(
            "Не удалось распознать записи.\n"
            "Формат: <code>1000 еда</code> или <code>+1000 зарплата</code>\n"
            "Можно несколько строк.",
            parse_mode="HTML",
        )
        return

    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        await state.clear()
        return

    serialized = [
        {
            "op": op,
            "amount": str(amt),
            "cat": cat,
            "date": dt.isoformat() if dt else None,
        }
        for op, amt, cat, dt in records_to_add
    ]

    # Category suggestion flow for single records
    if len(records_to_add) == 1:
        op, _amt, cat, _dt = records_to_add[0]
        intercepted = await _maybe_ask_category(
            message, state, user_id, op, cat, serialized, errors
        )
        if intercepted:
            return

    await state.update_data(
        pending_records=serialized, parse_errors=errors, user_id=user_id
    )

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        added = await save_parsed_records(user_id, records_to_add)
        response = format_added_records_response(added, errors)
        await message.answer(
            response, reply_markup=main_menu_keyboard(), parse_mode="HTML"
        )
        await _send_budget_alerts(message, user_id, added)
        await state.clear()
        return

    if len(accounts) == 1:
        acc = accounts[0]
        added = await save_parsed_records(user_id, records_to_add, acc.id)
        response = format_added_records_response(added, errors, account_name=acc.name)
        await message.answer(
            response, reply_markup=main_menu_keyboard(), parse_mode="HTML"
        )
        await _send_budget_alerts(message, user_id, added)
        await state.clear()
        return

    await message.answer(
        "💳 <b>Выберите счёт:</b>",
        reply_markup=account_select_keyboard(accounts),
        parse_mode="HTML",
    )
    await state.set_state(AddRecord.waiting_for_account)


@router.callback_query(F.data.startswith("acc_select:"), AddRecord.waiting_for_account)
@log_exceptions("Ошибка при выборе счёта")
async def handle_record_account_select(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Сохраняет записи с выбранным счётом."""
    try:
        account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("user_id")
    serialized = data.get("pending_records", [])
    errors = data.get("parse_errors", [])

    records_to_add = _deserialize_records(serialized)
    added = await save_parsed_records(user_id, records_to_add, account_id)

    account_name: str | None = None
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        for acc in accounts:
            if acc.id == account_id:
                account_name = acc.name
                break

    response = format_added_records_response(added, errors, account_name=account_name)
    await callback.message.edit_text(response, parse_mode="HTML")
    await callback.message.answer(
        "Выберите действие:", reply_markup=main_menu_keyboard()
    )
    await _send_budget_alerts(callback.message, user_id, added)
    await state.clear()
    await callback.answer()


@router.message(
    StateFilter(None), F.text.regexp(r"^([+-]\d|\d{1,2}\.\d{1,2}\.?\d{0,2}\s+[+-]?\d)")
)
@log_exceptions("Ошибка при добавлении записи")
async def handle_direct_record(message: Message, state: FSMContext, **kwargs) -> None:
    """Прямой ввод записей без нажатия кнопки (если начинается с +/- или с даты)."""
    lines = message.text.strip().split("\n")

    records_to_add = []
    errors = []

    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue

        result = parse_record_line(line, default_operation=None)
        if result:
            records_to_add.append(result)
        else:
            errors.append(f"Строка {i}: не удалось распознать")

    if not records_to_add:
        await message.answer(
            "Не удалось распознать записи.\n"
            "Формат: <code>+1000 зарплата</code> или <code>-500 еда</code>",
            parse_mode="HTML",
        )
        return

    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return

    serialized = [
        {
            "op": op,
            "amount": str(amt),
            "cat": cat,
            "date": dt.isoformat() if dt else None,
        }
        for op, amt, cat, dt in records_to_add
    ]

    # Category suggestion flow for single records
    if len(records_to_add) == 1:
        op, _amt, cat, _dt = records_to_add[0]
        intercepted = await _maybe_ask_category(
            message, state, user_id, op, cat, serialized, errors
        )
        if intercepted:
            return

    async with async_session() as session:
        accounts = await get_accounts(session, user_id)

    if not accounts:
        added = await save_parsed_records(user_id, records_to_add)
        if not added:
            await message.answer(
                "Не удалось сохранить записи.", reply_markup=main_menu_keyboard()
            )
            return
        response = format_added_records_response(added, errors)
        await message.answer(
            response, reply_markup=main_menu_keyboard(), parse_mode="HTML"
        )
        await _send_budget_alerts(message, user_id, added)
        return

    if len(accounts) == 1:
        acc = accounts[0]
        added = await save_parsed_records(user_id, records_to_add, acc.id)
        if not added:
            await message.answer(
                "Не удалось сохранить записи.", reply_markup=main_menu_keyboard()
            )
            return
        response = format_added_records_response(added, errors, account_name=acc.name)
        await message.answer(
            response, reply_markup=main_menu_keyboard(), parse_mode="HTML"
        )
        await _send_budget_alerts(message, user_id, added)
        return

    await state.update_data(
        pending_records=serialized, parse_errors=errors, user_id=user_id
    )
    await message.answer(
        "💳 <b>Выберите счёт:</b>",
        reply_markup=account_select_keyboard(accounts),
        parse_mode="HTML",
    )
    await state.set_state(AddRecord.waiting_for_account)
