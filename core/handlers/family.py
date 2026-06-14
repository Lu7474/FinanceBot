"""Handlers for the family budget section.

The family relationship is NOT cached in UserMiddleware (it caches only
(user_id, is_banned)), so every handler re-queries get_family() directly.
"""

import html
import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from dateutil.relativedelta import relativedelta

from config import MAX_CAPTION_LENGTH, RECORDS_PER_PAGE, TIMEZONE
from core.charts import build_family_stacked_chart
from core.database.models import async_session
from core.database.requests import (
    MAX_FAMILY_MEMBERS,
    create_family,
    dissolve_family,
    get_family,
    get_family_category_breakdown,
    get_family_member_ids,
    get_family_members,
    get_family_summary,
    get_history_data,
    join_family,
    kick_member,
    leave_family,
    regenerate_invite_code,
    rename_family,
)
from core.keyboards import (
    family_confirm_keyboard,
    family_history_filter_keyboard,
    family_join_or_create_keyboard,
    family_kick_confirm_keyboard,
    family_manage_keyboard,
    family_menu_keyboard,
    family_report_period_keyboard,
    family_report_type_keyboard,
    main_menu_keyboard,
    member_marker,
)
from core.utils import clean_text, format_money, log_exceptions

from .common import (
    FamilyStates,
    get_message,
    get_user_id_from_event,
    is_family,
)
from .history import build_history_page

router = Router()

MAX_FAMILY_NAME_LENGTH = 100


# ==================== Loading & formatting helpers ====================


async def _load_family(user_id: int):
    """Returns (family, members, is_owner) or (None, [], False) if no family."""
    async with async_session() as session:
        family = await get_family(session, user_id)
        if family is None:
            return None, [], False
        members = await get_family_members(session, family.id)
    is_owner = family.owner_id == user_id
    return family, members, is_owner


def _format_summary(
    family_name: str, members: list, summary: dict, current_user_id: int
) -> str:
    """Builds the family summary screen text for the current period."""
    lines = [f"📊 <b>{html.escape(family_name)}</b>", ""]
    total_income = Decimal("0")
    total_expense = Decimal("0")
    for idx, m in enumerate(members):
        data = summary.get(m.id, {"income": Decimal("0"), "expense": Decimal("0")})
        income = data["income"]
        expense = data["expense"]
        total_income += income
        total_expense += expense
        marker = member_marker(idx)
        you = " (вы)" if m.id == current_user_id else ""
        name = html.escape((m.name or "—"))
        lines.append(
            f"{marker} <b>{name}</b>{you}: "
            f"доходы {format_money(income)} | расходы {format_money(expense)}"
        )
    balance = total_income - total_expense
    sign = "+" if balance >= 0 else ""
    lines.append("─" * 18)
    lines.append(f"Итого доходы:  {format_money(total_income)}")
    lines.append(f"Итого расходы: {format_money(total_expense)}")
    lines.append(f"Общий баланс:  {sign}{format_money(balance)}")
    lines.append("")
    lines.append("<i>за текущий месяц</i>")
    return "\n".join(lines)


async def _show_summary_message(message: Message, user_id: int) -> None:
    """Renders the summary as a fresh message (used from the reply button)."""
    family, members, is_owner = await _load_family(user_id)
    if family is None:
        await message.answer(
            "👨‍👩‍👧 <b>Семейный бюджет</b>\n\n"
            "Создайте общий бюджет или присоединитесь к существующему "
            "по коду приглашения.",
            reply_markup=family_join_or_create_keyboard(),
            parse_mode="HTML",
        )
        return
    async with async_session() as session:
        summary = await get_family_summary(session, family.id, within="month")
    await message.answer(
        _format_summary(family.name, members, summary, user_id),
        reply_markup=family_menu_keyboard(is_owner),
        parse_mode="HTML",
    )


async def _edit_summary(callback: CallbackQuery, user_id: int) -> None:
    """Renders the summary by editing the callback message."""
    family, members, is_owner = await _load_family(user_id)
    if family is None:
        await get_message(callback).edit_text(
            "👨‍👩‍👧 <b>Семейный бюджет</b>\n\n"
            "Создайте общий бюджет или присоединитесь к существующему.",
            reply_markup=family_join_or_create_keyboard(),
            parse_mode="HTML",
        )
        return
    async with async_session() as session:
        summary = await get_family_summary(session, family.id, within="month")
    await get_message(callback).edit_text(
        _format_summary(family.name, members, summary, user_id),
        reply_markup=family_menu_keyboard(is_owner),
        parse_mode="HTML",
    )


# ==================== Entry point ====================


@router.message(F.func(is_family))
@log_exceptions("Ошибка при открытии раздела «Семья»")
async def show_family(message: Message, state: FSMContext, **kwargs) -> None:
    await state.clear()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _show_summary_message(message, user_id)
    await state.set_state(FamilyStates.summary)


@router.message(Command("join"))
@log_exceptions("Ошибка при вступлении в семью по команде")
async def join_command(
    message: Message, command: CommandObject, state: FSMContext, **kwargs
) -> None:
    """/join <code> — alternative way to join a family."""
    await state.clear()
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer(
            "Укажите код приглашения: <code>/join КОД</code>", parse_mode="HTML"
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _try_join(message, state, user_id, code)


# ==================== Create ====================


@router.callback_query(F.data == "fam:create")
@log_exceptions("Ошибка при создании семьи")
async def fam_create_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    await get_message(callback).edit_text(
        "Введите название семьи (например, «Семья Ивановых»):"
    )
    await state.set_state(FamilyStates.creating_name)
    await callback.answer()


@router.message(FamilyStates.creating_name)
@log_exceptions("Ошибка при вводе названия семьи")
async def fam_create_name(message: Message, state: FSMContext, **kwargs) -> None:
    name = clean_text(message.text or "")
    if not name or len(name) > MAX_FAMILY_NAME_LENGTH:
        await message.answer(
            f"Название должно быть от 1 до {MAX_FAMILY_NAME_LENGTH} символов. "
            "Попробуйте ещё раз:"
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return

    # Guard against a double family (race / stale state)
    existing, _, _ = await _load_family(user_id)
    if existing is not None:
        await message.answer("Вы уже состоите в семье.")
        await _show_summary_message(message, user_id)
        await state.set_state(FamilyStates.summary)
        return

    async with async_session() as session:
        family = await create_family(session, user_id, name)
        await session.commit()
        code = family.invite_code
        fam_name = family.name

    await message.answer(
        f"✅ Семья <b>{html.escape(fam_name)}</b> создана!\n\n"
        f"Код приглашения: <code>{code}</code>\n\n"
        f"Поделитесь им с партнёром — он введёт <code>/join {code}</code> "
        "или нажмёт «Присоединиться».",
        parse_mode="HTML",
    )
    await _show_summary_message(message, user_id)
    await state.set_state(FamilyStates.summary)


# ==================== Join ====================


@router.callback_query(F.data == "fam:join")
@log_exceptions("Ошибка при начале вступления в семью")
async def fam_join_start(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await get_message(callback).edit_text("Введите код приглашения от партнёра:")
    await state.set_state(FamilyStates.joining_code)
    await callback.answer()


@router.message(FamilyStates.joining_code)
@log_exceptions("Ошибка при вводе кода приглашения")
async def fam_join_code(message: Message, state: FSMContext, **kwargs) -> None:
    code = (message.text or "").strip().upper()
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    await _try_join(message, state, user_id, code)


async def _try_join(
    message: Message, state: FSMContext, user_id: int, code: str
) -> None:
    """Shared join logic for both the button flow and the /join command."""
    tg_id = message.from_user.id if message.from_user else "?"

    existing, _, _ = await _load_family(user_id)
    if existing is not None:
        await message.answer("Вы уже состоите в семье. Сначала покиньте текущую.")
        await _show_summary_message(message, user_id)
        await state.set_state(FamilyStates.summary)
        return

    async with async_session() as session:
        family = await join_family(session, user_id, code)
        if family is not None:
            await session.commit()
            fam_name = family.name

    if family is None:
        # Distinguish "full" from "not found" only loosely; log the failure.
        logging.warning("Неудачная попытка /join: tg_id=%s code=%s", tg_id, code)
        await message.answer(
            "❌ Не удалось вступить. Код неверный, семья заполнена (5/5) "
            "или вы уже состоите в семье. Проверьте код и попробуйте снова."
        )
        return

    await message.answer(f"✅ Вы вступили в семью «{html.escape(fam_name)}»")
    await _show_summary_message(message, user_id)
    await state.set_state(FamilyStates.summary)


# ==================== Navigation ====================


@router.callback_query(F.data == "fam:noop")
async def fam_noop(callback: CallbackQuery, **kwargs) -> None:
    await callback.answer()


@router.callback_query(F.data == "fam:back")
@log_exceptions("Ошибка при возврате из раздела «Семья»")
async def fam_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.clear()
    await get_message(callback).answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    try:
        await get_message(callback).delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "fam:menu")
@log_exceptions("Ошибка при возврате к сводке семьи")
async def fam_menu(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _edit_summary(callback, user_id)
    await state.set_state(FamilyStates.summary)
    await callback.answer()


# ==================== Shared history ====================


async def _render_history(
    callback: CallbackQuery, user_id: int, page: int, filter_uid: int | None
) -> None:
    family, members, _ = await _load_family(user_id)
    if family is None:
        await callback.answer("Семья не найдена.", show_alert=True)
        return

    async with async_session() as session:
        member_ids = await get_family_member_ids(session, family.id)
        if filter_uid is not None and filter_uid not in member_ids:
            await callback.answer("Некорректный фильтр.", show_alert=True)
            return
        scope = [filter_uid] if filter_uid is not None else member_ids
        total_count, income_sum, expense_sum, records = await get_history_data(
            session,
            scope,
            within="all",
            limit=RECORDS_PER_PAGE,
            offset=page * RECORDS_PER_PAGE,
            newest_first=True,
        )

    total_pages = max(1, (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE)
    if not records:
        text = "📋 <b>Общая история</b>\n\nНет записей."
    else:
        text, _ = build_history_page(
            records,
            page,
            total_pages,
            income_sum,
            expense_sum,
            header="📋 <b>Общая история</b>",
            members=members,
        )

    await get_message(callback).edit_text(
        text,
        reply_markup=family_history_filter_keyboard(
            members, page, total_pages, filter_uid
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "fam:history")
@log_exceptions("Ошибка при открытии общей истории")
async def fam_history(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await state.update_data(fam_hist_filter=None)
    await _render_history(callback, user_id, 0, None)
    await state.set_state(FamilyStates.viewing_history)
    await callback.answer()


@router.callback_query(F.data.startswith("fam:hist_page:"))
@log_exceptions("Ошибка при пагинации общей истории")
async def fam_history_page(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    try:
        page = int((callback.data or "").split(":")[2])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    data = await state.get_data()
    filter_uid = data.get("fam_hist_filter")
    await _render_history(callback, user_id, page, filter_uid)
    await callback.answer()


@router.callback_query(F.data.startswith("fam:hist_filter:"))
@log_exceptions("Ошибка при фильтрации общей истории")
async def fam_history_filter(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    raw = (callback.data or "").split(":")[2]
    if raw == "all":
        filter_uid: int | None = None
    else:
        try:
            filter_uid = int(raw)
        except ValueError:
            await callback.answer("Некорректные данные.")
            return
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await state.update_data(fam_hist_filter=filter_uid)
    await _render_history(callback, user_id, 0, filter_uid)
    await callback.answer()


# ==================== Shared report ====================


def _period_args(period: str):
    """Returns (within, date_from, date_to, label) for the family report."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    if period == "quarter":
        start = (now - relativedelta(months=2)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return "range", start, now, "последние 3 месяца"
    if period == "year":
        return "year", None, None, "этот год"
    return "month", None, None, "этот месяц"


def _format_report_caption(
    breakdown: list, members: list, operation: str, label: str
) -> str:
    """Text caption for the family report chart."""
    name_by_uid = {m.id: (m.name or "—") for m in members}
    marker_by_uid = {m.id: member_marker(i) for i, m in enumerate(members)}

    by_cat: dict[str, Decimal] = {}
    by_member: dict[int, Decimal] = {}
    for row in breakdown:
        by_cat[row["category"]] = (
            by_cat.get(row["category"], Decimal("0")) + row["total"]
        )
        by_member[row["user_id"]] = (
            by_member.get(row["user_id"], Decimal("0")) + row["total"]
        )
    total = sum(by_cat.values(), Decimal("0"))
    type_name = "Доходы" if operation == "+" else "Расходы"

    lines = [
        f"📊 <b>Семейный отчёт — {label}</b>",
        f"Тип: {type_name}",
        "",
        f"Итого: <b>{format_money(total)}</b>",
        "",
    ]
    lines.append("<b>По категориям:</b>")
    for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"  • {html.escape(cat)}: {format_money(amt)}")
    lines.append("")
    lines.append("<b>Участники:</b>")
    for m in members:
        amt = by_member.get(m.id, Decimal("0"))
        lines.append(
            f"  {marker_by_uid.get(m.id, '▫️')} "
            f"{html.escape(name_by_uid.get(m.id, '—'))}: {format_money(amt)}"
        )
    result = "\n".join(lines)
    if len(result) > MAX_CAPTION_LENGTH:
        result = result[: MAX_CAPTION_LENGTH - 20] + "\n\n... (обрезано)"
    return result


async def _send_report(
    callback: CallbackQuery, user_id: int, op: str, period: str
) -> None:
    operation = "+" if op == "inc" else "-"
    within, date_from, date_to, label = _period_args(period)

    family, members, _ = await _load_family(user_id)
    if family is None:
        await callback.answer("Семья не найдена.", show_alert=True)
        return

    async with async_session() as session:
        breakdown = await get_family_category_breakdown(
            session, family.id, operation, within, date_from, date_to
        )

    kb = family_report_period_keyboard(op, period)
    msg = get_message(callback)

    # The message may already be a photo (period switch), so never edit_text it —
    # send a fresh message and drop the previous one.
    if not breakdown:
        await msg.answer("Нет данных за выбранный период.", reply_markup=kb)
        try:
            await msg.delete()
        except Exception:
            pass
        return

    member_meta = [(m.id, m.name or "—") for m in members]
    buf = await build_family_stacked_chart(breakdown, member_meta, operation)
    caption = _format_report_caption(breakdown, members, operation, label)

    if buf:
        await msg.answer_photo(
            photo=BufferedInputFile(buf.read(), filename="family_report.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        await msg.answer(caption, parse_mode="HTML", reply_markup=kb)
    try:
        await msg.delete()
    except Exception:
        pass


@router.callback_query(F.data == "fam:report")
@log_exceptions("Ошибка при открытии семейного отчёта")
async def fam_report(callback: CallbackQuery, **kwargs) -> None:
    await get_message(callback).edit_text(
        "📊 <b>Общий отчёт</b>\n\nВыберите тип:",
        reply_markup=family_report_type_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fam:rep_type:"))
@log_exceptions("Ошибка при выборе типа семейного отчёта")
async def fam_report_type(callback: CallbackQuery, **kwargs) -> None:
    raw = (callback.data or "").split(":")[2]
    op = "inc" if raw == "income" else "exp"
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await callback.answer("⏳ Генерация...")
    await _send_report(callback, user_id, op, "month")


@router.callback_query(F.data.startswith("fam:rep_period:"))
@log_exceptions("Ошибка при смене периода семейного отчёта")
async def fam_report_period(callback: CallbackQuery, **kwargs) -> None:
    parts = (callback.data or "").split(":")
    try:
        op, period = parts[2], parts[3]
    except IndexError:
        await callback.answer("Некорректные данные.")
        return
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await callback.answer("⏳ Генерация...")
    await _send_report(callback, user_id, op, period)


# ==================== Management (owner only) ====================


def _format_manage(family, members: list, owner_id: int) -> str:
    lines = [
        "⚙️ <b>Управление семьёй</b>",
        "",
        f"Название: <b>{html.escape(family.name)}</b>",
        f"Код приглашения: <code>{family.invite_code}</code>",
        "",
        f"Члены ({len(members)}/{MAX_FAMILY_MEMBERS}):",
    ]
    for idx, m in enumerate(members):
        marker = member_marker(idx)
        if m.id == owner_id:
            lines.append(f"  👑 {marker} {html.escape(m.name or '—')} (вы, владелец)")
        else:
            lines.append(f"  👤 {marker} {html.escape(m.name or '—')}")
    return "\n".join(lines)


async def _edit_manage(callback: CallbackQuery, user_id: int) -> bool:
    """Renders the management screen. Returns False if the user isn't the owner."""
    family, members, is_owner = await _load_family(user_id)
    if family is None or not is_owner:
        await callback.answer("Доступно только владельцу.", show_alert=True)
        return False
    await get_message(callback).edit_text(
        _format_manage(family, members, user_id),
        reply_markup=family_manage_keyboard(members, user_id),
        parse_mode="HTML",
    )
    return True


@router.callback_query(F.data == "fam:manage")
@log_exceptions("Ошибка при открытии управления семьёй")
async def fam_manage(callback: CallbackQuery, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    await _edit_manage(callback, user_id)
    await callback.answer()


@router.callback_query(F.data == "fam:regen")
@log_exceptions("Ошибка при обновлении кода приглашения")
async def fam_regen(callback: CallbackQuery, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    family, _, is_owner = await _load_family(user_id)
    if family is None or not is_owner:
        await callback.answer("Доступно только владельцу.", show_alert=True)
        return
    async with async_session() as session:
        new_code = await regenerate_invite_code(session, family.id, user_id)
        await session.commit()
    if new_code:
        await _edit_manage(callback, user_id)
        await callback.answer(f"Новый код: {new_code}", show_alert=True)
    else:
        await callback.answer("Не удалось обновить код.", show_alert=True)


@router.callback_query(F.data == "fam:rename")
@log_exceptions("Ошибка при начале переименования семьи")
async def fam_rename_start(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    family, _, is_owner = await _load_family(user_id)
    if family is None or not is_owner:
        await callback.answer("Доступно только владельцу.", show_alert=True)
        return
    await get_message(callback).edit_text("Введите новое название семьи:")
    await state.set_state(FamilyStates.renaming)
    await callback.answer()


@router.message(FamilyStates.renaming)
@log_exceptions("Ошибка при переименовании семьи")
async def fam_rename_name(message: Message, state: FSMContext, **kwargs) -> None:
    name = clean_text(message.text or "")
    if not name or len(name) > MAX_FAMILY_NAME_LENGTH:
        await message.answer(
            f"Название должно быть от 1 до {MAX_FAMILY_NAME_LENGTH} символов. "
            "Попробуйте ещё раз:"
        )
        return
    user_id = await get_user_id_from_event(message, kwargs)
    if not user_id:
        await message.answer("Ошибка.")
        return
    family, _, is_owner = await _load_family(user_id)
    if family is None or not is_owner:
        await message.answer("Доступно только владельцу.")
        await state.clear()
        return
    async with async_session() as session:
        await rename_family(session, family.id, user_id, name)
        await session.commit()
    await message.answer(f"✅ Семья переименована в «{html.escape(name)}».")
    await _show_summary_message(message, user_id)
    await state.set_state(FamilyStates.summary)


@router.callback_query(F.data.startswith("fam:kick:"))
@log_exceptions("Ошибка при запросе удаления члена")
async def fam_kick_confirm(callback: CallbackQuery, **kwargs) -> None:
    try:
        target = int((callback.data or "").split(":")[2])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return
    await get_message(callback).edit_text(
        "Удалить этого члена из семьи? Его записи останутся у него.",
        reply_markup=family_kick_confirm_keyboard(target),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("fam:kick_yes:"))
@log_exceptions("Ошибка при удалении члена семьи")
async def fam_kick_do(callback: CallbackQuery, **kwargs) -> None:
    try:
        target = int((callback.data or "").split(":")[2])
    except IndexError, ValueError:
        await callback.answer("Некорректные данные.")
        return
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    family, _, is_owner = await _load_family(user_id)
    if family is None or not is_owner:
        await callback.answer("Доступно только владельцу.", show_alert=True)
        return
    async with async_session() as session:
        ok = await kick_member(session, family.id, user_id, target)
        await session.commit()
    await callback.answer("Член удалён." if ok else "Не удалось удалить.")
    await _edit_manage(callback, user_id)


@router.callback_query(F.data == "fam:leave")
@log_exceptions("Ошибка при запросе выхода из семьи")
async def fam_leave_confirm(callback: CallbackQuery, **kwargs) -> None:
    await get_message(callback).edit_text(
        "Вы уверены? Ваши записи останутся, но вы больше не будете видеть "
        "записи партнёра.",
        reply_markup=family_confirm_keyboard("leave"),
    )
    await callback.answer()


@router.callback_query(F.data == "fam:leave_yes")
@log_exceptions("Ошибка при выходе из семьи")
async def fam_leave_do(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    async with async_session() as session:
        ok = await leave_family(session, user_id)
        await session.commit()
    if not ok:
        await callback.answer(
            "Владелец не может покинуть семью — только расформировать.",
            show_alert=True,
        )
        return
    await state.clear()
    await get_message(callback).edit_text("Вы покинули семью.")
    await get_message(callback).answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "fam:dissolve")
@log_exceptions("Ошибка при запросе расформирования семьи")
async def fam_dissolve_confirm(callback: CallbackQuery, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    family, _, is_owner = await _load_family(user_id)
    if family is None or not is_owner:
        await callback.answer("Доступно только владельцу.", show_alert=True)
        return
    await get_message(callback).edit_text(
        "Расформировать семью? Все члены потеряют доступ к общей статистике. "
        "Записи каждого останутся.",
        reply_markup=family_confirm_keyboard("dissolve"),
    )
    await callback.answer()


@router.callback_query(F.data == "fam:dissolve_yes")
@log_exceptions("Ошибка при расформировании семьи")
async def fam_dissolve_do(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    user_id = await get_user_id_from_event(callback, kwargs)
    if not user_id:
        await callback.answer("Ошибка.")
        return
    family, _, is_owner = await _load_family(user_id)
    if family is None or not is_owner:
        await callback.answer("Доступно только владельцу.", show_alert=True)
        return
    async with async_session() as session:
        await dissolve_family(session, family.id, user_id)
        await session.commit()
    await state.clear()
    await get_message(callback).edit_text("Семья расформирована.")
    await get_message(callback).answer(
        "Главное меню:", reply_markup=main_menu_keyboard()
    )
    await callback.answer()
