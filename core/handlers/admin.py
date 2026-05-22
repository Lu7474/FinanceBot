"""Admin panel — fully inline-button driven interface."""

import asyncio
import html

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ADMIN_ID
from core.database import requests as db
from core.database.models import async_session
from core.handlers.common import AdminStates, get_message, is_main_menu_button
from core.utils import format_money

USERS_PER_PAGE = 8

router = Router()


async def _safe_edit(message, text: str, **kwargs) -> None:
    """edit_text ignoring 'message is not modified' errors."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


router.message.filter(F.from_user.id == ADMIN_ID)
router.callback_query.filter(F.from_user.id == ADMIN_ID)


# ==================== Клавиатуры ====================


def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
                InlineKeyboardButton(
                    text="👥 Пользователи", callback_data="adm_users_0"
                ),
            ],
            [
                InlineKeyboardButton(text="🔍 Поиск", callback_data="adm_search"),
                InlineKeyboardButton(text="🏆 Топ", callback_data="adm_top"),
            ],
            [
                InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_bc"),
                InlineKeyboardButton(text="🚪 Выйти", callback_data="adm_exit"),
            ],
        ]
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="↩ Главное меню", callback_data="adm_menu"),
            ]
        ]
    )


FILTER_LABELS = {"all": "Все", "active": "Активные", "banned": "Забаненные"}
SORT_LABELS = {"date": "↓ Дата рег.", "activity": "↓ Активность", "name": "А-Я"}


def _list_controls(page: int, total_pages: int, flt: str, srt: str) -> list[list]:
    rows = []
    flt_row = []
    for key, label in FILTER_LABELS.items():
        flt_row.append(
            InlineKeyboardButton(
                text=f"✓ {label}" if key == flt else label,
                callback_data=f"adm_flt_{key}",
            )
        )
    rows.append(flt_row)

    srt_row = []
    for key, label in SORT_LABELS.items():
        srt_row.append(
            InlineKeyboardButton(
                text=f"✓ {label}" if key == srt else label,
                callback_data=f"adm_srt_{key}",
            )
        )
    rows.append(srt_row)

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀", callback_data=f"adm_users_{page - 1}")
        )
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="adm_noop")
    )
    if page + 1 < total_pages:
        nav.append(
            InlineKeyboardButton(text="▶", callback_data=f"adm_users_{page + 1}")
        )
    if len(nav) > 1:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="↩ Главное меню", callback_data="adm_menu")])
    return rows


# ==================== Вход ====================


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminStates.in_admin)
    await message.answer(
        "🔐 <b>Режим администратора</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=_main_menu_kb(),
    )


# ==================== Главное меню ====================


@router.callback_query(AdminStates.in_admin, F.data == "adm_menu")
async def cb_main_menu(query: CallbackQuery) -> None:
    await _safe_edit(
        get_message(query),
        "🔐 <b>Режим администратора</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=_main_menu_kb(),
    )
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data == "adm_exit")
async def cb_exit(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _safe_edit(get_message(query), "Вышел из режима администратора.")
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data == "adm_noop")
async def cb_noop(query: CallbackQuery) -> None:
    await query.answer()


# ==================== Статистика ====================


@router.callback_query(
    AdminStates.in_admin, F.data.in_({"adm_stats", "adm_stats_refresh"})
)
async def cb_stats(query: CallbackQuery) -> None:
    async with async_session() as session:
        stats = await db.get_bot_stats(session)
    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: <b>{stats['total_users']}</b>\n"
        f"⛔ Забанено: <b>{stats['banned_users']}</b>\n"
        f"💳 Счетов: <b>{stats['total_accounts']}</b>\n"
        f"📝 Записей: <b>{stats['total_records']}</b>\n"
        f"📅 Новых сегодня: <b>{stats['new_today']}</b>\n"
        f"📅 Новых за неделю: <b>{stats['new_week']}</b>\n"
        f"🔥 Активных за неделю: <b>{stats['active_week']}</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить", callback_data="adm_stats_refresh"
                ),
                InlineKeyboardButton(text="↩ Главное меню", callback_data="adm_menu"),
            ]
        ]
    )
    await _safe_edit(get_message(query), text, parse_mode="HTML", reply_markup=kb)
    await query.answer("Обновлено ✓" if query.data == "adm_stats_refresh" else "")


# ==================== Список пользователей ====================


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_users_"))
async def cb_users(query: CallbackQuery, state: FSMContext) -> None:
    page = int((query.data or "").split("_")[-1])
    await state.update_data(users_page=page)
    await _render_users_page(query, state, page)
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_flt_"))
async def cb_filter(query: CallbackQuery, state: FSMContext) -> None:
    flt = (query.data or "")[8:]  # "adm_flt_" = 8 chars
    await state.update_data(users_filter=flt, users_page=0)
    await _render_users_page(query, state, 0)
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_srt_"))
async def cb_sort(query: CallbackQuery, state: FSMContext) -> None:
    srt = (query.data or "")[8:]  # "adm_srt_" = 8 chars
    await state.update_data(users_sort=srt, users_page=0)
    await _render_users_page(query, state, 0)
    await query.answer()


async def _render_users_page(
    query: CallbackQuery, state: FSMContext, page: int
) -> None:
    fsm = await state.get_data()
    flt = fsm.get("users_filter", "all")
    srt = fsm.get("users_sort", "date")

    async with async_session() as session:
        total = await db.count_users(session, filter_mode=flt)
        users = await db.get_all_users(
            session,
            offset=page * USERS_PER_PAGE,
            limit=USERS_PER_PAGE,
            filter_mode=flt,
            sort_by=srt,
        )

    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)

    if not users:
        await _safe_edit(
            get_message(query),
            f"👥 Нет пользователей ({FILTER_LABELS.get(flt, flt)}).",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=_list_controls(0, 1, flt, srt)
            ),
        )
        return

    start = page * USERS_PER_PAGE + 1
    text = (
        f"👥 <b>Пользователи [{start}–{start + len(users) - 1} из {total}]</b>  "
        f"<i>({FILTER_LABELS.get(flt, flt)})</i>\n\nНажми на пользователя:"
    )

    rows = []
    for u in users:
        ban_mark = " ⛔" if u.is_banned else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {(u.name or '—')[:30]}{ban_mark}  |  {u.tg_id}",
                    callback_data=f"adm_user_{u.tg_id}",
                )
            ]
        )
    rows.extend(_list_controls(page, total_pages, flt, srt))

    await _safe_edit(
        get_message(query),
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ==================== Карточка пользователя ====================


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_user_"))
async def cb_user_card(query: CallbackQuery, state: FSMContext) -> None:
    tg_id = int((query.data or "")[9:])  # "adm_user_" = 9 chars
    fsm = await state.get_data()
    page = fsm.get("users_page", 0)

    async with async_session() as session:
        user = await db.get_user_by_tg_id(session, tg_id)
        if not user:
            await query.answer("Пользователь не найден.", show_alert=True)
            return
        stats = await db.get_user_stats(session, user.id)
        balances = await db.get_account_balances(session, user.id)

    ban_status = "⛔ Да" if user.is_banned else "✅ Нет"
    last_active = stats.get("last_activity")
    last_str = last_active.strftime("%d.%m.%Y %H:%M") if last_active else "нет записей"

    lines = [
        "👤 <b>Пользователь</b>",
        f"🆔 tg_id: <code>{user.tg_id}</code>",
        f"📛 Имя: {html.escape(user.name or '—')}",
        f"📱 Телефон: {user.phone or '—'}",
        f"📅 Регистрация: {user.created_at.strftime('%d.%m.%Y %H:%M')}",
        f"🕐 Последняя активность: {last_str}",
        f"⛔ Бан: {ban_status}",
        "",
    ]
    if balances:
        lines.append(f"💳 <b>Счета ({len(balances)}):</b>")
        for acc, bal in balances:
            lines.append(f"  • {html.escape(acc.name)} — {format_money(bal)}")
        lines.append("")
    lines += [
        f"📊 Записей: {stats['total_records']} "
        f"(+{stats['income_count']} / -{stats['expense_count']})",
        f"💰 Оборот: +{format_money(stats['income_sum'])} / -{format_money(stats['expense_sum'])}",
    ]

    ban_btn = (
        InlineKeyboardButton(text="✅ Разбанить", callback_data=f"adm_unban_{tg_id}")
        if user.is_banned
        else InlineKeyboardButton(text="⛔ Забанить", callback_data=f"adm_ban_{tg_id}")
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ban_btn,
                InlineKeyboardButton(
                    text="🗑️ Удалить", callback_data=f"adm_del1_{tg_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📥 Скачать CSV", callback_data=f"adm_csv_{tg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩ К списку", callback_data=f"adm_users_{page}"
                )
            ],
        ]
    )
    await _safe_edit(
        get_message(query), "\n".join(lines), parse_mode="HTML", reply_markup=kb
    )
    await query.answer()


# ==================== CSV экспорт ====================


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_csv_"))
async def cb_csv(query: CallbackQuery) -> None:
    tg_id = int((query.data or "")[8:])  # "adm_csv_" = 8 chars
    await query.answer("Генерирую файл...")

    async with async_session() as session:
        user = await db.get_user_by_tg_id(session, tg_id)
        if not user:
            await query.answer("Не найден.", show_alert=True)
            return
        csv_bytes = await db.get_user_records_csv(session, user.id)

    await get_message(query).answer_document(
        BufferedInputFile(csv_bytes, filename=f"records_{tg_id}.csv"),
        caption=f"📥 Записи пользователя <code>{tg_id}</code>",
        parse_mode="HTML",
    )


# ==================== Бан / Разбан ====================


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_ban_"))
async def cb_ban_ask(query: CallbackQuery) -> None:
    tg_id = int((query.data or "")[8:])  # "adm_ban_" = 8 chars
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Забанить", callback_data=f"adm_bando_{tg_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"adm_user_{tg_id}"
                ),
            ]
        ]
    )
    await _safe_edit(
        get_message(query),
        f"Забанить <code>{tg_id}</code>?\nПользователь не сможет пользоваться ботом.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_bando_"))
async def cb_ban_do(query: CallbackQuery, state: FSMContext) -> None:
    tg_id = int((query.data or "")[10:])  # "adm_bando_" = 10 chars
    async with async_session() as session:
        await db.ban_user(session, tg_id, is_banned=True)
        await session.commit()
    page = (await state.get_data()).get("users_page", 0)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩ К пользователю", callback_data=f"adm_user_{tg_id}"
                ),
                InlineKeyboardButton(
                    text="↩ К списку", callback_data=f"adm_users_{page}"
                ),
            ]
        ]
    )
    await _safe_edit(
        get_message(query),
        f"⛔ Пользователь <code>{tg_id}</code> забанен.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_unban_"))
async def cb_unban_do(query: CallbackQuery, state: FSMContext) -> None:
    tg_id = int((query.data or "")[10:])  # "adm_unban_" = 10 chars
    async with async_session() as session:
        await db.ban_user(session, tg_id, is_banned=False)
        await session.commit()
    page = (await state.get_data()).get("users_page", 0)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩ К пользователю", callback_data=f"adm_user_{tg_id}"
                ),
                InlineKeyboardButton(
                    text="↩ К списку", callback_data=f"adm_users_{page}"
                ),
            ]
        ]
    )
    await _safe_edit(
        get_message(query),
        f"✅ Пользователь <code>{tg_id}</code> разбанен.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


# ==================== Удаление ====================


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_del1_"))
async def cb_del1(query: CallbackQuery) -> None:
    tg_id = int((query.data or "")[9:])  # "adm_del1_" = 9 chars
    async with async_session() as session:
        user = await db.get_user_by_tg_id(session, tg_id)
        if not user:
            await query.answer("Не найден.", show_alert=True)
            return
        stats = await db.get_user_stats(session, user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚠️ Да, удалить", callback_data=f"adm_del2_{tg_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"adm_user_{tg_id}"
                ),
            ]
        ]
    )
    await _safe_edit(
        get_message(query),
        f"⚠️ Удалить <code>{tg_id}</code>?\n"
        f"Записей: {stats['total_records']}, все счета — будут удалены.\n\n"
        f"<b>Необратимо!</b>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_del2_"))
async def cb_del2(query: CallbackQuery) -> None:
    tg_id = int((query.data or "")[9:])  # "adm_del2_" = 9 chars
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑️ УДАЛИТЬ НАВСЕГДА", callback_data=f"adm_deldo_{tg_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"adm_user_{tg_id}"
                ),
            ]
        ]
    )
    await _safe_edit(
        get_message(query),
        f"🗑️ <b>Последнее предупреждение!</b>\n\n"
        f"Удалить <code>{tg_id}</code> без восстановления?",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_deldo_"))
async def cb_deldo(query: CallbackQuery) -> None:
    tg_id = int((query.data or "")[10:])  # "adm_deldo_" = 10 chars
    async with async_session() as session:
        ok = await db.delete_user_cascade(session, tg_id)
        await session.commit()
    text = (
        f"🗑️ Пользователь <code>{tg_id}</code> удалён." if ok else "Ошибка при удалении."
    )
    await _safe_edit(
        get_message(query), text, parse_mode="HTML", reply_markup=_back_kb()
    )
    await query.answer()


# ==================== Топ ====================


@router.callback_query(AdminStates.in_admin, F.data == "adm_top")
async def cb_top(query: CallbackQuery) -> None:
    async with async_session() as session:
        top = await db.get_top_users(session, limit=5)

    if not top:
        await _safe_edit(get_message(query), "Нет данных.", reply_markup=_back_kb())
        await query.answer()
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["🏆 <b>Топ-5 активных пользователей</b>\n"]
    for i, (user, count) in enumerate(top):
        lines.append(
            f"{medals[i]} {html.escape(user.name or '—')} | <code>{user.tg_id}</code> | {count} зап."
        )
    await _safe_edit(
        get_message(query), "\n".join(lines), parse_mode="HTML", reply_markup=_back_kb()
    )
    await query.answer()


# ==================== Поиск ====================


@router.callback_query(AdminStates.in_admin, F.data == "adm_search")
async def cb_search_start(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.search_query)
    await _safe_edit(
        get_message(query),
        "🔍 <b>Поиск пользователя</b>\n\nВведи имя или часть имени.\n/cancel — отмена.",
        parse_mode="HTML",
    )
    await query.answer()


@router.message(AdminStates.search_query, F.text == "/cancel")
async def search_cancel(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminStates.in_admin)
    await message.answer(
        "🔐 <b>Режим администратора</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=_main_menu_kb(),
    )


@router.message(AdminStates.search_query, F.text)
async def search_text(message: Message, state: FSMContext) -> None:
    query_str = (message.text or "").strip()
    await state.set_state(AdminStates.in_admin)

    async with async_session() as session:
        users = await db.find_users_by_name(session, query_str)

    if not users:
        await message.answer(
            f"По запросу «{html.escape(query_str)}» никого не нашёл.",
            reply_markup=_back_kb(),
        )
        return

    rows = []
    for u in users:
        ban_mark = " ⛔" if u.is_banned else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {(u.name or '—')[:30]}{ban_mark}  |  {u.tg_id}",
                    callback_data=f"adm_user_{u.tg_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="↩ Главное меню", callback_data="adm_menu")])
    await message.answer(
        f"🔍 <b>Результаты «{html.escape(query_str)}»:</b> найдено {len(users)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ==================== Broadcast ====================


@router.callback_query(AdminStates.in_admin, F.data == "adm_bc")
async def cb_bc_start(query: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Всем", callback_data="adm_bc_tgt_all")],
            [
                InlineKeyboardButton(
                    text="🔥 Активным за 7 дней", callback_data="adm_bc_tgt_active"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ С 10+ записями", callback_data="adm_bc_tgt_power"
                )
            ],
            [InlineKeyboardButton(text="↩ Отмена", callback_data="adm_menu")],
        ]
    )
    await _safe_edit(
        get_message(query),
        "📢 <b>Рассылка</b>\n\nКому отправить?",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await query.answer()


@router.callback_query(AdminStates.in_admin, F.data.startswith("adm_bc_tgt_"))
async def cb_bc_target(query: CallbackQuery, state: FSMContext) -> None:
    target = (query.data or "")[11:]  # "adm_bc_tgt_" = 11 chars
    await state.update_data(broadcast_target=target)
    await state.set_state(AdminStates.broadcast_text)

    labels = {
        "all": "всем пользователям",
        "active": "активным за 7 дней",
        "power": "с 10+ записями",
    }
    await _safe_edit(
        get_message(query),
        f"📢 Рассылка {labels.get(target, '')}.\n\nВведи текст сообщения.\n/cancel — отмена.",
        parse_mode="HTML",
    )
    await query.answer()


@router.message(AdminStates.broadcast_text, F.text == "/cancel")
async def bc_cancel(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminStates.in_admin)
    await message.answer(
        "🔐 <b>Режим администратора</b>\n\nВыбери действие:",
        parse_mode="HTML",
        reply_markup=_main_menu_kb(),
    )


@router.message(AdminStates.broadcast_text, F.text)
async def bc_text_received(message: Message, state: FSMContext) -> None:
    if is_main_menu_button(message):
        await message.answer(
            "⚠️ Вы в режиме ввода текста рассылки. Отправьте текст сообщения или /cancel для отмены."
        )
        return
    text = message.text
    fsm = await state.get_data()
    target = fsm.get("broadcast_target", "all")

    async with async_session() as session:
        if target == "active":
            tg_ids = await db.get_active_user_tg_ids(session, days=7)
        elif target == "power":
            tg_ids = await db.get_power_user_tg_ids(session, min_records=10)
        else:
            tg_ids = await db.get_all_tg_ids(session)

    labels = {"all": "всем", "active": "активным (7д)", "power": "с 10+ записями"}
    await state.update_data(broadcast_text=text, broadcast_tg_ids=tg_ids)
    await state.set_state(AdminStates.in_admin)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить", callback_data="adm_bc_confirm"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="adm_menu"),
            ]
        ]
    )
    await message.answer(
        f"📢 <b>Рассылка {labels.get(target, '')} ({len(tg_ids)} чел.):</b>\n\n{text}\n\nОтправить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(AdminStates.in_admin, F.data == "adm_bc_confirm")
async def cb_bc_confirm(query: CallbackQuery, state: FSMContext) -> None:
    fsm = await state.get_data()
    text = fsm.get("broadcast_text")
    tg_ids = fsm.get("broadcast_tg_ids") or []
    if not text or not tg_ids:
        await query.answer("Данные не найдены.", show_alert=True)
        return

    await _safe_edit(get_message(query), f"📢 Рассылка запущена... (0/{len(tg_ids)})")
    await query.answer()

    assert query.bot is not None
    sent, blocked, failed = 0, 0, 0
    for tg_id in tg_ids:
        try:
            await query.bot.send_message(tg_id, text)
            sent += 1
        except TelegramRetryAfter as e:
            # Flood control: ждём указанное время и дослыем один раз
            await asyncio.sleep(e.retry_after)
            try:
                await query.bot.send_message(tg_id, text)
                sent += 1
            except TelegramForbiddenError:
                blocked += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            # Юзер заблокировал бота — норма, не считаем ошибкой
            blocked += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    result = f"✅ Рассылка завершена.\nОтправлено: {sent}/{len(tg_ids)}"
    if blocked:
        result += f"\nЗаблокировали бота: {blocked}"
    if failed:
        result += f"\nНе доставлено: {failed}"
    await state.update_data(broadcast_text=None, broadcast_tg_ids=None)
    await get_message(query).answer(result, reply_markup=_back_kb())
