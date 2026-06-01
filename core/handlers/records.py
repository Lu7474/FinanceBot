"""Handlers for adding income/expense records."""

import html
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MAX_AMOUNT, MAX_CATEGORY_LENGTH, TIMEZONE
from core.database.models import User, async_session
from core.database.requests import (
    check_and_alert_budget,
    get_accounts,
    get_last_record_id,
    get_user_categories,
    suggest_category,
    update_record,
)
from core.database.requests._common import now_moscow
from core.database.requests.records import count_records
from core.keyboards import (
    account_select_keyboard,
    category_suggest_keyboard,
    description_prompt_keyboard,
    main_menu_keyboard,
    notify_onboarding_keyboard,
    record_detail_keyboard,
)
from core.utils import clean_text, format_record_card, log_exceptions

from .common import (
    AddRecord,
    CategoryStates,
    get_message,
    get_user_id_from_event,
    is_expense,
    is_income,
    is_main_menu_button,
    save_parsed_records,
)

router = Router()


async def _maybe_send_onboarding(target: Message, user_id: int) -> None:
    """Send notification onboarding after the user's first record."""
    async with async_session() as session:
        total = await count_records(session, user_id, within="all")
    if total == 1:
        await target.answer(
            "🔔 Хотите получать автоматические сводки и напоминания?\n\n"
            "• Ежедневные итоги (21:00)\n"
            "• Ежемесячная сводка\n"
            "• Еженедельная сводка\n"
            "• Напоминание если забыли записать\n",
            reply_markup=notify_onboarding_keyboard(),
        )


async def _maybe_offer_description(target: Message, user_id: int, added: list) -> None:
    """In button mode, offer to attach a description to a single just-added record."""
    if len(added) != 1:
        return
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user or user.description_mode != "button":
            return
        record_id = await get_last_record_id(session, user_id)
    if record_id:
        await target.answer(
            "Добавить описание к записи?",
            reply_markup=description_prompt_keyboard(record_id),
        )


async def _send_budget_alerts(
    message: Message,
    user_id: int,
    added_records: list[tuple],
) -> None:
    """Checks budget thresholds for expense records and sends alerts."""
    assert message.bot is not None
    async with async_session() as session:
        for op, amount, category, _, _ in added_records:
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
) -> list[ParsedRecord]:
    """Восстанавливает записи из FSM-state (str → Decimal/datetime)."""
    result = []
    try:
        for d in serialized:
            date = datetime.fromisoformat(d["date"]) if d.get("date") else None
            result.append(
                ParsedRecord(
                    d["op"], Decimal(d["amount"]), d["cat"], date, d.get("desc")
                )
            )
    except (InvalidOperation, ValueError, TypeError, KeyError) as e:
        raise ValueError(f"Повреждённые данные в FSM: {e}") from e
    return result


def format_added_records_response(
    added_records: list[tuple],
    errors: list[str] | None = None,
    account_name: str | None = None,
) -> str:
    """Формирует красивый ответ после добавления записей."""
    if not added_records:
        return "Не удалось сохранить записи."

    today = datetime.now(ZoneInfo(TIMEZONE)).date()

    if len(added_records) == 1:
        op, amt, cat, record_date, _ = added_records[0]
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
        total_income = sum(amt for op, amt, _, _, _ in added_records if op == "+")
        total_expense = sum(amt for op, amt, _, _, _ in added_records if op == "-")

        response = f"✅ <b>Добавлено записей: {len(added_records)}</b>\n\n"
        for op, amt, cat, record_date, _ in added_records:
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


class ParsedRecord(NamedTuple):
    """Result of parsing one record line."""

    operation: str
    amount: Decimal
    category: str
    date: datetime | None
    description: Optional[str] = None


def _split_category_description(
    rest: str, mode: str, user_categories: Optional[list[str]]
) -> tuple[str, Optional[str]]:
    """Split the post-amount remainder into (category, description) per mode.

    Modes:
    - off / button → whole remainder is the category, no description.
    - brackets → trailing "(...)" is the description, prefix is the category.
    - auto → longest matching known category as a prefix; tail → description.
      Fallback (no match): first word = category, rest = description.
    """
    if mode == "brackets":
        m = re.search(r"\(([^)]*)\)\s*$", rest)
        if m:
            description = m.group(1).strip() or None
            category = clean_text(rest[: m.start()])
        else:
            description = None
            category = clean_text(rest)
        category = category.capitalize() if category else "Не указано"
        return category, description

    if mode == "auto":
        words = clean_text(rest).split()
        if not words:
            return "Не указано", None
        cats_sorted = sorted(
            user_categories or [], key=lambda c: len(c.split()), reverse=True
        )
        lower_words = [w.lower() for w in words]
        for cat in cats_sorted:
            cat_words = cat.split()
            n = len(cat_words)
            if (
                n
                and n <= len(words)
                and lower_words[:n] == [cw.lower() for cw in cat_words]
            ):
                description = " ".join(words[n:]).strip() or None
                return cat, description  # keep stored category casing
        # fallback: first word = category, rest = description
        category = words[0].capitalize()
        description = " ".join(words[1:]).strip() or None
        return category, description

    # off / button (and any unknown mode): whole remainder is the category
    category = clean_text(rest)
    category = category.capitalize() if category else "Не указано"
    return category, None


def parse_record_line(
    line: str,
    default_operation: str | None = None,
    *,
    mode: str = "off",
    user_categories: Optional[list[str]] = None,
) -> ParsedRecord | None:
    """Парсит строку записи в ParsedRecord или None при ошибке.

    Форматы:
    - "1000 еда" — использует default_operation, сегодняшняя дата
    - "+1000 зарплата" — доход, сегодняшняя дата
    - "-500 еда" — расход, сегодняшняя дата
    - "27.01 500 еда" — указанная дата (ДД.ММ текущего года)
    - "27.01.25 500 еда" — указанная дата (ДД.ММ.ГГ)

    `mode` управляет выделением описания из хвоста строки (см.
    `_split_category_description`); `user_categories` нужен только для mode="auto".
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
    except InvalidOperation, ValueError:
        return None

    rest = line.replace(match.group(0), "", 1)
    category, description = _split_category_description(rest, mode, user_categories)

    if len(category) > MAX_CATEGORY_LENGTH:
        return None
    if description:
        description = description[:255]

    return ParsedRecord(operation, amount, category, record_date, description)


async def _get_description_context(user_id: int) -> tuple[str, list[str]]:
    """Return (description_mode, category_names) used to parse the user's input."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        mode = user.description_mode if user else "off"
        cats: list[str] = []
        if mode == "auto":
            user_cats = await get_user_categories(session, user_id)
            cats = [c.name for c in user_cats]
    return mode, cats


def _parse_lines(
    lines: list[str],
    default_operation: str | None,
    mode: str,
    cats: list[str],
) -> tuple[list[ParsedRecord], list[str]]:
    """Parse multiple input lines into records + per-line error messages."""
    records_to_add: list[ParsedRecord] = []
    errors: list[str] = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        result = parse_record_line(
            line, default_operation, mode=mode, user_categories=cats
        )
        if result:
            records_to_add.append(result)
        else:
            errors.append(f"Строка {i}: не удалось распознать")
    return records_to_add, errors


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
        await get_message(message_or_callback).edit_text(
            text, reply_markup=kb, parse_mode="HTML"
        )
    await state.set_state(CategoryStates.confirming_suggested_category)
    return True


@router.message(F.func(lambda m: is_income(m) or is_expense(m)))
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

    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        await state.clear()
        return

    mode, cats = await _get_description_context(user_id)
    lines = (message.text or "").strip().split("\n")
    records_to_add, errors = _parse_lines(lines, default_operation, mode, cats)

    if not records_to_add:
        await message.answer(
            "Не удалось распознать записи.\n"
            "Формат: <code>1000 еда</code> или <code>+1000 зарплата</code>\n"
            "Можно несколько строк.",
            parse_mode="HTML",
        )
        return

    serialized = [
        {
            "op": r.operation,
            "amount": str(r.amount),
            "cat": r.category,
            "date": r.date.isoformat() if r.date else None,
            "desc": r.description,
        }
        for r in records_to_add
    ]

    # Category suggestion flow for single records
    if len(records_to_add) == 1:
        r = records_to_add[0]
        intercepted = await _maybe_ask_category(
            message, state, user_id, r.operation, r.category, serialized, errors
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
        if added:
            await _maybe_send_onboarding(message, user_id)
            await _maybe_offer_description(message, user_id, added)
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
        if added:
            await _maybe_send_onboarding(message, user_id)
            await _maybe_offer_description(message, user_id, added)
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
        account_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    data = await state.get_data()
    user_id = data.get("user_id")
    assert isinstance(user_id, int)
    serialized = data.get("pending_records", [])
    errors = data.get("parse_errors", [])

    try:
        records_to_add = _deserialize_records(serialized)
    except ValueError:
        logging.exception("FSM deserialization error")
        await state.clear()
        await get_message(callback).answer(
            "⚠️ Данные сессии повреждены. Попробуйте ввести записи заново.",
            reply_markup=main_menu_keyboard(),
        )
        return

    added = await save_parsed_records(user_id, records_to_add, account_id)

    account_name: str | None = None
    async with async_session() as session:
        accounts = await get_accounts(session, user_id)
        for acc in accounts:
            if acc.id == account_id:
                account_name = acc.name
                break

    response = format_added_records_response(added, errors, account_name=account_name)
    await get_message(callback).edit_text(response, parse_mode="HTML")
    await get_message(callback).answer(
        "Выберите действие:", reply_markup=main_menu_keyboard()
    )
    await _send_budget_alerts(get_message(callback), user_id, added)
    if added:
        await _maybe_send_onboarding(get_message(callback), user_id)
        await _maybe_offer_description(get_message(callback), user_id, added)
    await state.clear()
    await callback.answer()


@router.message(
    StateFilter(None), F.text.regexp(r"^([+-]\d|\d{1,2}\.\d{1,2}\.?\d{0,2}\s+[+-]?\d)")
)
@log_exceptions("Ошибка при добавлении записи")
async def handle_direct_record(message: Message, state: FSMContext, **kwargs) -> None:
    """Прямой ввод записей без нажатия кнопки (если начинается с +/- или с даты)."""
    user_id = await get_user_id_from_event(message, kwargs, create_if_missing=True)
    if not user_id:
        await message.answer("Ошибка. Отправьте /start для регистрации.")
        return

    mode, cats = await _get_description_context(user_id)
    lines = (message.text or "").strip().split("\n")
    records_to_add, errors = _parse_lines(lines, None, mode, cats)

    if not records_to_add:
        await message.answer(
            "Не удалось распознать записи.\n"
            "Формат: <code>+1000 зарплата</code> или <code>-500 еда</code>",
            parse_mode="HTML",
        )
        return

    serialized = [
        {
            "op": r.operation,
            "amount": str(r.amount),
            "cat": r.category,
            "date": r.date.isoformat() if r.date else None,
            "desc": r.description,
        }
        for r in records_to_add
    ]

    # Category suggestion flow for single records
    if len(records_to_add) == 1:
        r = records_to_add[0]
        intercepted = await _maybe_ask_category(
            message, state, user_id, r.operation, r.category, serialized, errors
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
        await _maybe_send_onboarding(message, user_id)
        await _maybe_offer_description(message, user_id, added)
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
        await _maybe_send_onboarding(message, user_id)
        await _maybe_offer_description(message, user_id, added)
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


# ==================== Описание записи (режим button) ====================


@router.callback_query(F.data.startswith("add_desc:"))
@log_exceptions("Ошибка при запросе описания")
async def handle_add_description_request(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """«Добавить описание» под только что созданной записью (режим button)."""
    try:
        record_id = int((callback.data or "").split(":")[1])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return

    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return

    await state.update_data(desc_record_id=record_id, desc_user_id=user_id)
    await state.set_state(AddRecord.waiting_for_description)
    await get_message(callback).answer("✏️ Введите описание для записи:")
    await callback.answer()


@router.message(AddRecord.waiting_for_description, ~F.func(is_main_menu_button))
@log_exceptions("Ошибка при сохранении описания")
async def handle_description_input(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Сохраняет введённое описание в запись и показывает обновлённую карточку."""
    data = await state.get_data()
    record_id = data.get("desc_record_id")
    user_id = data.get("desc_user_id")
    await state.clear()

    if not record_id or not user_id:
        await message.answer(
            "Сессия истекла. Попробуйте снова.", reply_markup=main_menu_keyboard()
        )
        return

    description = (message.text or "").strip()[:255] or None
    async with async_session() as session:
        updated = await update_record(
            session, record_id, user_id, description=description
        )
        if updated:
            await session.commit()

    if not updated:
        await message.answer("Запись не найдена.", reply_markup=main_menu_keyboard())
        return

    await message.answer("✅ Описание сохранено.", reply_markup=main_menu_keyboard())
    await message.answer(
        format_record_card(updated),
        reply_markup=record_detail_keyboard(record_id),
        parse_mode="HTML",
    )
