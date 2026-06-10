"""Export, import and backup handlers."""

import asyncio
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import CHART_TIMEOUT_SECONDS, TIMEZONE
from core.charts import _chart_executor
from core.database.models import async_session
from core.database.requests import (
    bulk_insert_records,
    check_duplicates_batch,
    get_all_budgets_for_backup,
    get_all_records_for_export,
    get_latest_snapshot_for_backup,
    get_or_create_account,
    get_wealth_items_for_backup,
)
from core.export import (
    _build_backup_sync,
    _build_export_sync,
    _build_template_sync,
    parse_import_file,
)
from core.handlers.common import ExportImportStates, get_message, is_export, is_import
from core.keyboards import (
    export_period_keyboard,
    export_type_keyboard,
    import_confirm_keyboard,
)
from core.utils import log_exceptions

router = Router()

_PERIOD_LABELS = {
    "month": "этот месяц",
    "3m": "3 месяца",
    "year": "этот год",
    "all": "всё время",
}


def _resolve_date_range(period: str) -> tuple[date | None, date | None]:
    """Return (date_from, date_to) for the given period key. Uses Moscow TZ."""
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    if period == "month":
        return today.replace(day=1), today
    if period == "3m":
        return (today - timedelta(days=90)), today
    if period == "year":
        return today.replace(month=1, day=1), today
    return None, None  # "all"


def build_summary(records) -> dict:
    total_income = Decimal("0")
    total_expense = Decimal("0")
    count_income = 0
    count_expense = 0
    expense_cats: dict[str, Decimal] = {}
    income_cats: dict[str, Decimal] = {}
    max_expense = Decimal("0")
    dates = []

    for r in records:
        dates.append(r.created_at.date())
        if r.operation == "+":
            total_income += r.amount
            count_income += 1
            income_cats[r.category] = (
                income_cats.get(r.category, Decimal("0")) + r.amount
            )
        else:
            total_expense += r.amount
            count_expense += 1
            expense_cats[r.category] = (
                expense_cats.get(r.category, Decimal("0")) + r.amount
            )
            if r.amount > max_expense:
                max_expense = r.amount

    def _top5_pct(cat_map: dict, total: Decimal) -> list:
        items = sorted(cat_map.items(), key=lambda x: -x[1])[:5]
        return [
            (
                cat,
                float(amt),
                round(float(amt) / float(total) * 100, 1) if total else 0.0,
            )
            for cat, amt in items
        ]

    n_days = max(1, (max(dates) - min(dates)).days + 1) if dates else 1

    return {
        "total_income": float(total_income),
        "total_expense": float(total_expense),
        "balance": float(total_income - total_expense),
        "count_income": count_income,
        "count_expense": count_expense,
        "top5_expense": _top5_pct(expense_cats, total_expense),
        "top5_income": _top5_pct(income_cats, total_income),
        "max_expense": float(max_expense),
        "avg_daily_expense": float(total_expense) / n_days,
        "avg_daily_income": float(total_income) / n_days,
        "first_date": min(dates).strftime("%d.%m.%Y") if dates else None,
        "last_date": max(dates).strftime("%d.%m.%Y") if dates else None,
    }


def records_to_rows(records) -> list[dict]:
    return [
        {
            "Дата": r.created_at.strftime("%d.%m.%Y"),
            "Тип": "Доход" if r.operation == "+" else "Расход",
            "Сумма": float(r.amount),
            "Категория": r.category,
            "Описание": r.description or "",
            "Счёт": r.account.name if r.account else "—",
        }
        for r in records
    ]


async def build_export_buffer(records) -> tuple[BytesIO, dict]:
    """records → (xlsx BytesIO, summary). Wraps executor + CHART_TIMEOUT."""
    rows = records_to_rows(records)
    summary = build_summary(records)
    loop = asyncio.get_running_loop()
    buf = await asyncio.wait_for(
        loop.run_in_executor(_chart_executor, _build_export_sync, rows, summary),
        timeout=CHART_TIMEOUT_SECONDS,
    )
    return buf, summary


# ==================== Экспорт ====================


@router.message(is_export)
async def handle_export(message: Message, state: FSMContext) -> None:
    await state.set_state(ExportImportStates.waiting_for_export_period)
    await message.answer(
        "Выберите период для экспорта:", reply_markup=export_period_keyboard()
    )


@router.callback_query(
    StateFilter(ExportImportStates.waiting_for_export_period),
    F.data.startswith("export_period:"),
)
async def handle_export_period(callback: CallbackQuery, state: FSMContext) -> None:
    period = (callback.data or "").split(":")[1]
    await state.update_data(export_period=period)
    await state.set_state(ExportImportStates.waiting_for_export_type)
    await callback.answer()
    await get_message(callback).edit_text(
        f"Период: {_PERIOD_LABELS.get(period, period)}\nВыберите тип записей:",
        reply_markup=export_type_keyboard(),
    )


@router.callback_query(F.data == "export_back_period")
@log_exceptions("Ошибка при возврате к выбору периода экспорта")
async def handle_export_back_to_period(
    callback: CallbackQuery, state: FSMContext
) -> None:
    """Шаг назад: с выбора типа обратно к выбору периода."""
    await state.set_state(ExportImportStates.waiting_for_export_period)
    await get_message(callback).edit_text(
        "Выберите период для экспорта:",
        reply_markup=export_period_keyboard(),
    )
    await callback.answer()


@router.callback_query(
    StateFilter(ExportImportStates.waiting_for_export_type),
    F.data.startswith("export_type:"),
)
async def handle_export_type(
    callback: CallbackQuery, state: FSMContext, user_id: int
) -> None:
    await callback.answer()
    type_key = (callback.data or "").split(":")[1]
    data = await state.get_data()
    period = data.get("export_period", "all")
    await state.clear()

    op_filter = None if type_key == "all" else ("+" if type_key == "income" else "-")
    date_from, date_to = _resolve_date_range(period)

    await get_message(callback).edit_text("⏳ Генерирую файл...")

    try:
        async with async_session() as session:
            records = await get_all_records_for_export(
                session,
                user_id,
                operation=op_filter,
                date_from=date_from,
                date_to=date_to,
            )

        buf, summary = await build_export_buffer(records)

        if date_from and date_to:
            fn_from = date_from.strftime("%d-%m-%Y")
            fn_to = date_to.strftime("%d-%m-%Y")
        elif summary["first_date"] and summary["last_date"]:
            fn_from = summary["first_date"].replace(".", "-")
            fn_to = summary["last_date"].replace(".", "-")
        else:
            fn_from = fn_to = date.today().strftime("%d-%m-%Y")
        filename = f"export_{fn_from}_{fn_to}.xlsx"

        inc, exp, bal = (
            summary["total_income"],
            summary["total_expense"],
            summary["balance"],
        )
        caption = (
            f"📊 Экспорт за {_PERIOD_LABELS.get(period, period)}: {len(records)} записей\n"
            f"📈 Доходы: {inc:.0f} ₽  |  📉 Расходы: {exp:.0f} ₽  |  💰 Баланс: {bal:+.0f} ₽"
        )
        await get_message(callback).answer_document(
            BufferedInputFile(buf.read(), filename=filename),
            caption=caption,
        )
    except Exception:
        logging.exception("Ошибка при создании экспорта")
        await get_message(callback).answer(
            "❌ Ошибка при создании файла. Попробуйте позже."
        )


# ==================== Бэкап ====================


@router.message(Command("backup"))
async def handle_backup(message: Message, user_id: int) -> None:
    await message.answer("⏳ Генерирую резервную копию...")

    try:
        async with async_session() as session:
            records = await get_all_records_for_export(session, user_id)
            budgets_db = await get_all_budgets_for_backup(session, user_id)
            snapshot = await get_latest_snapshot_for_backup(session, user_id)
            wealth_db = await get_wealth_items_for_backup(session, user_id)

        records_rows = records_to_rows(records)
        budgets_rows = [
            {
                "Категория": b.category,
                "Лимит": float(b.amount),
                "Активен": "Да" if b.is_active else "Нет",
            }
            for b in budgets_db
        ]
        snapshot_rows = (
            [
                {"Название": item.name, "Сумма": float(item.amount)}
                for item in snapshot.items
            ]
            if snapshot
            else []
        )
        wealth_rows = [
            {
                "Тип": "Актив" if w.type == "A" else "Пассив",
                "Название": w.name,
                "Сумма": float(w.amount),
                "Заметка": w.note or "",
            }
            for w in wealth_db
        ]

        loop = asyncio.get_running_loop()
        buf = await asyncio.wait_for(
            loop.run_in_executor(
                _chart_executor,
                _build_backup_sync,
                records_rows,
                budgets_rows,
                snapshot_rows,
                wealth_rows,
            ),
            timeout=CHART_TIMEOUT_SECONDS,
        )

        filename = f"backup_{date.today().isoformat()}.xlsx"
        await message.answer_document(
            BufferedInputFile(buf.read(), filename=filename),
            caption=f"📦 Резервная копия создана: {len(records)} записей. Сохраните файл в надёжном месте.",
        )
    except Exception:
        logging.exception("Ошибка при создании бэкапа")
        await message.answer("❌ Ошибка при создании файла. Попробуйте позже.")


# ==================== Импорт ====================


@router.message(is_import)
async def handle_import(message: Message, state: FSMContext) -> None:
    loop = asyncio.get_running_loop()
    try:
        buf = await asyncio.wait_for(
            loop.run_in_executor(_chart_executor, _build_template_sync),
            timeout=CHART_TIMEOUT_SECONDS,
        )
        await message.answer_document(
            BufferedInputFile(buf.read(), filename="import_template.xlsx"),
            caption=(
                "📥 Вот шаблон для импорта. Заполните его и отправьте обратно.\n\n"
                "Обязательные столбцы: Дата, Тип, Сумма, Категория\n"
                "Необязательные: Счёт\n"
                "Максимум: 1000 строк, файл до 5 МБ"
            ),
        )
    except Exception:
        logging.exception("Ошибка при создании шаблона")
        await message.answer("❌ Не удалось создать шаблон. Попробуйте позже.")
        return

    await state.set_state(ExportImportStates.waiting_for_import_file)


@router.message(StateFilter(ExportImportStates.waiting_for_import_file), F.document)
@log_exceptions("Ошибка при импорте файла")
async def handle_import_file(message: Message, state: FSMContext, user_id: int) -> None:
    doc = message.document
    assert doc is not None
    if not (doc.file_name or "").endswith(".xlsx"):
        await message.answer("Пожалуйста, отправьте файл в формате .xlsx")
        return

    if (doc.file_size or 0) > 5 * 1024 * 1024:
        await message.answer("❌ Файл слишком большой. Максимум 5 МБ.")
        return

    assert message.bot is not None
    file_bytes_io = await message.bot.download(doc)
    assert file_bytes_io is not None
    file_bytes = file_bytes_io.read()

    loop = asyncio.get_running_loop()
    valid_rows, errors, _ = await loop.run_in_executor(
        _chart_executor, parse_import_file, file_bytes, 1000
    )

    if not valid_rows and not errors:
        await message.answer("📋 Файл пустой или не содержит данных.")
        return

    # Считаем потенциальные дубли (не блокируем — только предупреждаем)
    async with async_session() as session:
        duplicates = len(await check_duplicates_batch(session, user_id, valid_rows))

    total = len(valid_rows) + len(errors)
    errors_count = len(errors)
    errors_preview = "\n".join(errors[:10])
    if len(errors) > 10:
        errors_preview += f"\n... и ещё {len(errors) - 10} ошибок"

    preview = (
        f"📋 Найдено {total} записей для импорта:\n"
        f"✅ Корректных: {len(valid_rows)}\n"
        f"⚠️ С ошибками: {errors_count}"
    )
    if duplicates:
        preview += f"\n⚠️ Похожих на дубли: {duplicates} (будут импортированы)"
    if errors_preview:
        preview += f"\n\n{errors_preview}"

    if not valid_rows:
        await message.answer(preview + "\n\nНечего импортировать.")
        await state.clear()
        return

    preview += f"\n\nИмпортировать {len(valid_rows)} записей?"

    # Сериализуем в primitive-типы для FSM storage
    serialized = [
        {
            "date": row["date"].isoformat(),
            "operation": row["operation"],
            "amount": float(row["amount"]),
            "category": row["category"],
            "account_name": row.get("account_name"),
            "description": row.get("description"),
        }
        for row in valid_rows
    ]
    await state.update_data(import_rows=serialized)
    await state.set_state(ExportImportStates.waiting_for_import_confirm)
    await message.answer(preview, reply_markup=import_confirm_keyboard())


@router.callback_query(
    StateFilter(ExportImportStates.waiting_for_import_confirm),
    F.data == "import_confirm:yes",
)
async def handle_import_confirm(
    callback: CallbackQuery, state: FSMContext, user_id: int
) -> None:
    await callback.answer()
    data = await state.get_data()
    serialized = data.get("import_rows", [])
    await state.clear()

    try:
        async with async_session() as session:
            rows_with_acc: list[dict] = []
            skipped_accounts: set[str] = set()
            for row in serialized:
                acc_id = None
                acc_name = row.get("account_name")
                if acc_name:
                    acc = await get_or_create_account(session, user_id, acc_name)
                    if acc:
                        acc_id = acc.id
                    else:
                        skipped_accounts.add(acc_name)

                rows_with_acc.append(
                    {
                        "date": date.fromisoformat(row["date"]),
                        "operation": row["operation"],
                        "amount": Decimal(str(row["amount"])),
                        "category": row["category"],
                        "account_id": acc_id,
                        "description": row.get("description"),
                    }
                )

            inserted = await bulk_insert_records(session, user_id, rows_with_acc)
            await session.commit()

        result_text = f"✅ Импортировано {inserted} записей."
        if skipped_accounts:
            names = ", ".join(sorted(skipped_accounts))
            if len(names) > 200:
                names = names[:200] + "…"
            result_text += f"\n⚠️ Следующие счета не удалось привязать (достигнут лимит 10): {names}."
        await get_message(callback).edit_text(result_text)
    except Exception:
        logging.exception("Ошибка при импорте записей")
        await get_message(callback).edit_text(
            "❌ Ошибка при импорте. Попробуйте позже."
        )


@router.callback_query(
    StateFilter(ExportImportStates.waiting_for_import_confirm),
    F.data == "import_confirm:cancel",
)
async def handle_import_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await get_message(callback).edit_text("Импорт отменён.")


@router.callback_query(
    StateFilter(
        ExportImportStates.waiting_for_export_period,
        ExportImportStates.waiting_for_export_type,
    ),
    F.data == "cancel",
)
async def handle_export_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await get_message(callback).edit_text("Отменено.")
