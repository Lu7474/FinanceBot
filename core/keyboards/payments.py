"""Keyboards for payment reminders: list, creation steps, card, edit."""

from decimal import Decimal
from functools import lru_cache

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import _payment_icon, format_money, today_msk


def payments_list_keyboard(payments: list) -> InlineKeyboardMarkup:
    """Main payments screen: a button per payment (opens card) + add/back."""
    builder = InlineKeyboardBuilder()
    today = today_msk()
    for p in payments:
        icon = _payment_icon(p.due_date, today)
        title = p.title if len(p.title) <= 40 else p.title[:39] + "…"
        builder.button(text=f"{icon} {title}", callback_data=f"pay:view:{p.id}")
    builder.button(text="➕ Добавить платёж", callback_data="pay:add")
    builder.button(text="← Назад", callback_data="pay:back")
    builder.adjust(1)
    return builder.as_markup()


def payment_reminder_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Buttons under a reminder message: mark paid / open payments."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Оплатил", callback_data=f"pay:done:{payment_id}"
                ),
                InlineKeyboardButton(text="Открыть платежи", callback_data="pay:open"),
            ]
        ]
    )


def payment_detail_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Payment card actions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Оплатил", callback_data=f"pay:done:{payment_id}"
                ),
                InlineKeyboardButton(
                    text="✏️ Изменить", callback_data=f"pay:edit_menu:{payment_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"pay:del:{payment_id}"
                )
            ],
            [InlineKeyboardButton(text="← Назад", callback_data="pay:open")],
        ]
    )


@lru_cache(maxsize=1)
def payment_amount_skip_keyboard() -> InlineKeyboardMarkup:
    """Creation step: skip amount (floating sum) or cancel."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сумма не задана", callback_data="pay:amt_skip"
                ),
                InlineKeyboardButton(text="Отмена", callback_data="pay:cancel"),
            ]
        ]
    )


@lru_cache(maxsize=1)
def payment_cancel_keyboard() -> InlineKeyboardMarkup:
    """Single cancel button for plain text-input creation steps."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="pay:cancel")]
        ]
    )


@lru_cache(maxsize=1)
def payment_period_keyboard() -> InlineKeyboardMarkup:
    """Final creation step: choose recurrence period."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Разовый", callback_data="pay:period:none"),
                InlineKeyboardButton(
                    text="Ежемесячно", callback_data="pay:period:month"
                ),
                InlineKeyboardButton(text="Ежегодно", callback_data="pay:period:year"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="pay:cancel")],
        ]
    )


def payment_edit_menu_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Edit menu: choose which field to change."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Название", callback_data=f"pay:edit:title:{payment_id}"
                ),
                InlineKeyboardButton(
                    text="Сумма", callback_data=f"pay:edit:amount:{payment_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Дата", callback_data=f"pay:edit:date:{payment_id}"
                ),
                InlineKeyboardButton(
                    text="Период", callback_data=f"pay:edit:period:{payment_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Категория", callback_data=f"pay:edit:category:{payment_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="← Назад", callback_data=f"pay:view:{payment_id}"
                )
            ],
        ]
    )


def payment_category_keyboard(
    categories: list, back_cb: str = "pay:cancel"
) -> InlineKeyboardMarkup:
    """Pick an expense category for a payment (creation or edit).

    callback_data: pay:setcat:<cat_id>, «Без категории» → pay:setcat:0.
    """
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.button(text=cat.name, callback_data=f"pay:setcat:{cat.id}")
    builder.adjust(4)
    builder.row(
        InlineKeyboardButton(text="Без категории", callback_data="pay:setcat:0"),
        InlineKeyboardButton(text="Отмена", callback_data=back_cb),
    )
    return builder.as_markup()


def payment_confirm_record_keyboard(
    payment_id: int, amount: Decimal, due: str
) -> InlineKeyboardMarkup:
    """Pay flow, fixed amount: confirm writing the expense to balance.

    `due` is the idempotency token (ISO due_date the buttons were built for):
    mark_paid rejects the tap when the payment was already paid meanwhile.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Да, −{format_money(amount)}",
                    callback_data=f"pay:rec_yes:{payment_id}:{due}",
                ),
                InlineKeyboardButton(
                    text="✏️ Другая сумма",
                    callback_data=f"pay:rec_amt:{payment_id}:{due}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Нет, только отметить",
                    callback_data=f"pay:rec_no:{payment_id}:{due}",
                )
            ],
        ]
    )


def payment_pay_amount_keyboard(payment_id: int, due: str) -> InlineKeyboardMarkup:
    """Pay flow, floating amount: skip writing the record."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить запись",
                    callback_data=f"pay:rec_no:{payment_id}:{due}",
                )
            ]
        ]
    )


def payment_pay_account_keyboard(
    payment_id: int, accounts: list, due: str
) -> InlineKeyboardMarkup:
    """Pay flow: pick the account for the expense record.

    Dedicated callback (pay:acc:<pay_id>:<acc_id>:<due>) — acc_select:* is
    taken by the regular record flow; <due> is the idempotency token.
    """
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(
            text=acc.name, callback_data=f"pay:acc:{payment_id}:{acc.id}:{due}"
        )
    builder.adjust(2)
    return builder.as_markup()


def payment_edit_period_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Pick a new period when editing an existing payment."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Разовый", callback_data=f"pay:setperiod:none:{payment_id}"
                ),
                InlineKeyboardButton(
                    text="Ежемесячно",
                    callback_data=f"pay:setperiod:month:{payment_id}",
                ),
                InlineKeyboardButton(
                    text="Ежегодно",
                    callback_data=f"pay:setperiod:year:{payment_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Назад", callback_data=f"pay:edit_menu:{payment_id}"
                )
            ],
        ]
    )


def payment_edit_amount_skip_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """When editing amount: allow clearing it (floating sum) or cancel."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Убрать сумму",
                    callback_data=f"pay:clear_amount:{payment_id}",
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"pay:edit_menu:{payment_id}"
                ),
            ]
        ]
    )


def payment_delete_confirm_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Confirmation for hard payment deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить", callback_data=f"pay:del_yes:{payment_id}"
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"pay:view:{payment_id}"
                ),
            ]
        ]
    )
