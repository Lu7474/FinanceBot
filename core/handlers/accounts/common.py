"""Shared helpers for the accounts package: balances text, back navigation."""

import html
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from core.database.models import async_session
from core.database.requests import get_account_balances, get_free_to_spend
from core.keyboards import accounts_menu_keyboard
from core.utils import log_exceptions

from ..common import get_message, get_user_id_from_event

router = Router()


def _build_accounts_text(balances: list[tuple], free: Decimal | None = None) -> str:
    """Формирует текст с балансами по счетам.

    free — если задан, под «Всего:» добавляется строка «Свободно: X ₽».
    """
    if not balances:
        return "💳 <b>Мои счета</b>\n\nСчетов нет. Нажмите ➕ Создать."

    lines = ["💳 <b>Мои счета</b>\n"]
    total = Decimal("0")
    for acc, balance in balances:
        sign = "-" if balance < 0 else ""
        formatted = f"{sign}{abs(balance):,.0f}₽".replace(",", " ")
        lines.append(f"<b>{html.escape(acc.name)}</b>  —  {formatted}")
        total += balance

    sign = "-" if total < 0 else ""
    total_str = f"{sign}{abs(total):,.0f}₽".replace(",", " ")
    lines.append(f"\n<b>Всего:  {total_str}</b>")
    if free is not None:
        free_sign = "-" if free < 0 else ""
        free_str = f"{free_sign}{abs(free):,.0f}₽".replace(",", " ")
        lines.append(f"💸 Свободно:  {free_str}")
    return "\n".join(lines)


async def _back_to_accounts(
    callback: CallbackQuery, state: FSMContext, kwargs: dict
) -> None:
    """Рендерит балансы + меню Счетов, чистит state."""
    user_id = await get_user_id_from_event(callback, kwargs)
    if user_id:
        async with async_session() as session:
            balances = await get_account_balances(session, user_id)
            fts = await get_free_to_spend(session, user_id, balances=balances)
        await get_message(callback).edit_text(
            _build_accounts_text(balances, free=fts.free),
            reply_markup=accounts_menu_keyboard(),
            parse_mode="HTML",
        )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "acc_back")
@log_exceptions("Ошибка при возврате к списку счетов")
async def handle_acc_back(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    """Возврат в раздел Счета из любого подсценария."""
    await _back_to_accounts(callback, state, kwargs)
