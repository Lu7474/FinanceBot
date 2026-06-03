"""
Tests for account CRUD operations and balances.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parent.parent))

from config import MAX_ACCOUNT_NAME_LENGTH
from core.database.models import Record
from core.database.requests import (
    MAX_ACCOUNTS_PER_USER,
    add_record,
    cancel_transfer,
    count_transfers,
    create_account,
    create_transfer,
    delete_account,
    get_account_balances,
    get_account_record_count,
    get_accounts,
    get_history_data,
    get_records,
    get_totals,
    get_transfer,
    get_transfers,
    rename_account,
    set_user,
)

# ==================== Helpers ====================
# Return plain ints to avoid accessing expired ORM objects after commits.


async def _make_user(session, tg_id: int = 111) -> int:
    user = await set_user(session, tg_id, name="Test")
    return user.id  # fresh after set_user's refresh


async def _make_account(session, user_id: int, name: str = "Наличные") -> int:
    acc = await create_account(session, user_id, name)
    return acc.id  # fresh after create_account's refresh


# ==================== name validation (handler-level limit) ====================


def test_max_account_name_length_constant():
    assert MAX_ACCOUNT_NAME_LENGTH == 40


@pytest.mark.asyncio
async def test_create_account_name_at_limit(session):
    """DB accepts a name exactly at the 40-char handler limit."""
    user_id = await _make_user(session)
    name = "А" * MAX_ACCOUNT_NAME_LENGTH
    acc = await create_account(session, user_id, name)
    assert acc is not None
    assert acc.name == name


@pytest.mark.asyncio
async def test_rename_account_name_at_limit(session):
    """DB accepts a renamed name exactly at the 40-char handler limit."""
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Старое")
    new_name = "Б" * MAX_ACCOUNT_NAME_LENGTH
    ok = await rename_account(session, acc_id, user_id, new_name)
    assert ok is True
    accounts = await get_accounts(session, user_id)
    assert accounts[0].name == new_name


# ==================== create_account ====================


@pytest.mark.asyncio
async def test_create_account_success(session):
    user_id = await _make_user(session)
    await create_account(session, user_id, "Наличные")

    accounts = await get_accounts(session, user_id)
    assert len(accounts) == 1
    assert accounts[0].name == "Наличные"


@pytest.mark.asyncio
async def test_create_account_duplicate_name(session):
    user_id = await _make_user(session)
    await _make_account(session, user_id, "Карта")
    duplicate = await create_account(session, user_id, "Карта")
    assert duplicate is None

    accounts = await get_accounts(session, user_id)
    assert len(accounts) == 1


@pytest.mark.asyncio
async def test_create_account_limit(session):
    user_id = await _make_user(session)
    for i in range(MAX_ACCOUNTS_PER_USER):
        acc = await create_account(session, user_id, f"Счёт {i}")
        assert acc is not None

    over_limit = await create_account(session, user_id, "Лишний")
    assert over_limit is None

    accounts = await get_accounts(session, user_id)
    assert len(accounts) == MAX_ACCOUNTS_PER_USER


# ==================== get_accounts ====================


@pytest.mark.asyncio
async def test_get_accounts_empty(session):
    user_id = await _make_user(session)
    accounts = await get_accounts(session, user_id)
    assert accounts == []


@pytest.mark.asyncio
async def test_get_accounts_order(session):
    user_id = await _make_user(session)
    await _make_account(session, user_id, "А")
    await _make_account(session, user_id, "Б")
    accounts = await get_accounts(session, user_id)
    assert [a.name for a in accounts] == ["А", "Б"]


# ==================== rename_account ====================


@pytest.mark.asyncio
async def test_rename_account_success(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Старое")
    ok = await rename_account(session, acc_id, user_id, "Новое")
    assert ok is True

    accounts = await get_accounts(session, user_id)
    assert accounts[0].name == "Новое"


@pytest.mark.asyncio
async def test_rename_account_duplicate(session):
    user_id = await _make_user(session)
    a1_id = await _make_account(session, user_id, "Один")
    await _make_account(session, user_id, "Два")
    ok = await rename_account(session, a1_id, user_id, "Два")
    assert ok is False

    accounts = await get_accounts(session, user_id)
    names = [a.name for a in accounts]
    assert "Один" in names


@pytest.mark.asyncio
async def test_rename_account_not_found(session):
    user_id = await _make_user(session)
    ok = await rename_account(session, 99999, user_id, "Что-то")
    assert ok is False


# ==================== delete_account ====================


@pytest.mark.asyncio
async def test_delete_account_success(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Удалить")
    ok = await delete_account(session, acc_id, user_id)
    assert ok is True

    accounts = await get_accounts(session, user_id)
    assert len(accounts) == 0


@pytest.mark.asyncio
async def test_delete_account_nullifies_records(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Карта")
    await add_record(session, user_id, "+", Decimal("100"), account_id=acc_id)

    await delete_account(session, acc_id, user_id)

    result = await session.execute(select(Record).where(Record.user_id == user_id))
    records = result.scalars().all()
    assert len(records) == 1
    assert records[0].account_id is None


@pytest.mark.asyncio
async def test_delete_account_wrong_user(session):
    user1_id = await _make_user(session, tg_id=1)
    user2_id = await _make_user(session, tg_id=2)
    acc_id = await _make_account(session, user1_id, "Карта")
    ok = await delete_account(session, acc_id, user2_id)
    assert ok is False

    accounts = await get_accounts(session, user1_id)
    assert len(accounts) == 1


# ==================== get_account_balances ====================


@pytest.mark.asyncio
async def test_get_account_balances_empty(session):
    user_id = await _make_user(session)
    balances = await get_account_balances(session, user_id)
    assert balances == []


@pytest.mark.asyncio
async def test_get_account_balances_correct(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Наличные")
    await add_record(session, user_id, "+", Decimal("1000"), account_id=acc_id)
    await add_record(session, user_id, "-", Decimal("300"), account_id=acc_id)

    balances = await get_account_balances(session, user_id)
    assert len(balances) == 1
    _, balance = balances[0]
    assert balance == Decimal("700")


@pytest.mark.asyncio
async def test_get_account_balances_zero_for_no_records(session):
    user_id = await _make_user(session)
    await _make_account(session, user_id, "Пустой")
    balances = await get_account_balances(session, user_id)
    assert balances[0][1] == Decimal("0")


@pytest.mark.asyncio
async def test_get_account_balances_multiple(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")
    await add_record(session, user_id, "+", Decimal("5000"), account_id=acc1_id)
    await add_record(session, user_id, "+", Decimal("3000"), account_id=acc2_id)
    await add_record(session, user_id, "-", Decimal("500"), account_id=acc2_id)

    balances = await get_account_balances(session, user_id)
    bal_map = {acc.name: bal for acc, bal in balances}
    assert bal_map["Наличные"] == Decimal("5000")
    assert bal_map["Карта"] == Decimal("2500")


# ==================== get_account_record_count ====================


@pytest.mark.asyncio
async def test_get_account_record_count(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Счёт")
    await add_record(session, user_id, "+", Decimal("100"), account_id=acc_id)
    await add_record(session, user_id, "-", Decimal("50"), account_id=acc_id)

    count = await get_account_record_count(session, acc_id)
    assert count == 2


@pytest.mark.asyncio
async def test_get_account_record_count_zero(session):
    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Пустой")
    count = await get_account_record_count(session, acc_id)
    assert count == 0


# ==================== create_transfer ====================


@pytest.mark.asyncio
async def test_create_transfer_success(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")
    await add_record(session, user_id, "+", Decimal("1000"), account_id=acc1_id)

    ok = await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("500"))
    assert ok is True

    balances = await get_account_balances(session, user_id)
    bal_map = {acc.name: bal for acc, bal in balances}
    assert bal_map["Наличные"] == Decimal("500")
    assert bal_map["Карта"] == Decimal("500")


@pytest.mark.asyncio
async def test_create_transfer_does_not_affect_global_balance(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")
    await add_record(session, user_id, "+", Decimal("2000"), account_id=acc1_id)

    income_before, expense_before = await get_totals(session, user_id)
    await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("500"))
    income_after, expense_after = await get_totals(session, user_id)

    net_before = income_before - expense_before
    net_after = income_after - expense_after
    assert net_before == net_after


@pytest.mark.asyncio
async def test_create_transfer_creates_two_records(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")

    ok = await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("300"))
    assert ok is True

    result = await session.execute(
        select(Record).where(Record.user_id == user_id).order_by(Record.operation)
    )
    records = result.scalars().all()
    assert len(records) == 2
    ops = {r.operation for r in records}
    assert ops == {"+", "-"}
    for r in records:
        assert r.category == "Перевод"


# ==================== transfer linking / history / cancel ====================


@pytest.mark.asyncio
async def test_create_transfer_links_pair(session):
    """Both records of a transfer share a non-null transfer_id."""
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")

    await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("300"))

    result = await session.execute(select(Record).where(Record.user_id == user_id))
    records = result.scalars().all()
    transfer_ids = {r.transfer_id for r in records}
    assert len(transfer_ids) == 1
    assert None not in transfer_ids


@pytest.mark.asyncio
async def test_count_and_get_transfers(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")

    await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("500"))
    await create_transfer(session, user_id, acc2_id, acc1_id, Decimal("100"))

    assert await count_transfers(session, user_id) == 2

    transfers = await get_transfers(session, user_id, limit=10, offset=0)
    assert len(transfers) == 2
    first = transfers[0]
    assert {"transfer_id", "amount", "date", "from_name", "to_name"} <= first.keys()
    # Every pair has both endpoints resolved.
    for t in transfers:
        assert t["from_name"] and t["to_name"]


@pytest.mark.asyncio
async def test_get_transfer_single(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")
    await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("500"))

    transfers = await get_transfers(session, user_id, limit=10, offset=0)
    tid = transfers[0]["transfer_id"]

    t = await get_transfer(session, user_id, tid)
    assert t is not None
    assert t["from_name"] == "Наличные"
    assert t["to_name"] == "Карта"
    assert t["amount"] == Decimal("500")


@pytest.mark.asyncio
async def test_cancel_transfer_restores_balance(session):
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")
    await add_record(session, user_id, "+", Decimal("1000"), account_id=acc1_id)
    await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("500"))

    transfers = await get_transfers(session, user_id, limit=10, offset=0)
    tid = transfers[0]["transfer_id"]

    ok = await cancel_transfer(session, user_id, tid)
    assert ok is True

    balances = await get_account_balances(session, user_id)
    bal_map = {acc.name: bal for acc, bal in balances}
    assert bal_map["Наличные"] == Decimal("1000")
    assert bal_map["Карта"] == Decimal("0")
    assert await count_transfers(session, user_id) == 0


@pytest.mark.asyncio
async def test_cancel_transfer_rejects_foreign_user(session):
    """IDOR guard: another user cannot cancel someone else's transfer."""
    owner_id = await _make_user(session, tg_id=111)
    acc1_id = await _make_account(session, owner_id, "Наличные")
    acc2_id = await _make_account(session, owner_id, "Карта")
    await create_transfer(session, owner_id, acc1_id, acc2_id, Decimal("500"))
    tid = (await get_transfers(session, owner_id, limit=10, offset=0))[0]["transfer_id"]

    attacker_id = await _make_user(session, tg_id=222)
    ok = await cancel_transfer(session, attacker_id, tid)
    assert ok is False
    assert await count_transfers(session, owner_id) == 1


@pytest.mark.asyncio
async def test_cancel_transfer_half_deleted(session):
    """If one side was already deleted manually, cancel removes the leftover."""
    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")
    await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("500"))

    tid = (await get_transfers(session, user_id, limit=10, offset=0))[0]["transfer_id"]

    # Manually drop one side of the pair.
    result = await session.execute(
        select(Record).where(Record.transfer_id == tid, Record.operation == "+")
    )
    leftover_target = result.scalar_one()
    await session.delete(leftover_target)
    await session.flush()

    ok = await cancel_transfer(session, user_id, tid)
    assert ok is True
    assert await count_transfers(session, user_id) == 0


@pytest.mark.asyncio
async def test_history_annotates_transfer_direction(session):
    """В истории счёта перевод подписывается направлением → / ←."""
    from core.handlers.accounts import _annotate_transfer_direction

    user_id = await _make_user(session)
    acc1_id = await _make_account(session, user_id, "Наличные")
    acc2_id = await _make_account(session, user_id, "Карта")
    await create_transfer(session, user_id, acc1_id, acc2_id, Decimal("500"))

    # История «Наличные» (источник): «Перевод → Карта»
    records = await get_records(
        session, user_id, "all", account_id=acc1_id, include_transfers=True
    )
    await _annotate_transfer_direction(session, records, acc1_id)
    assert "Перевод → Карта" in [r.category for r in records]

    # История «Карта» (назначение): «Перевод ← Наличные»
    records2 = await get_records(
        session, user_id, "all", account_id=acc2_id, include_transfers=True
    )
    await _annotate_transfer_direction(session, records2, acc2_id)
    assert "Перевод ← Наличные" in [r.category for r in records2]


@pytest.mark.asyncio
async def test_history_annotation_skips_plain_records(session):
    """Обычные записи (без transfer_id) не трогаются аннотацией."""
    from core.handlers.accounts import _annotate_transfer_direction

    user_id = await _make_user(session)
    acc_id = await _make_account(session, user_id, "Наличные")
    await add_record(
        session, user_id, "-", Decimal("100"), category="Еда", account_id=acc_id
    )

    records = await get_records(
        session, user_id, "all", account_id=acc_id, include_transfers=True
    )
    await _annotate_transfer_direction(session, records, acc_id)
    assert [r.category for r in records] == ["Еда"]


# ==================== account_history_keyboard ====================


def _kb_callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_account_history_keyboard_exits_and_filters():
    """Есть выходы и рабочие фильтры; мёртвых кнопок общей истории нет."""
    from core.keyboards import account_history_keyboard

    kb = account_history_keyboard(
        account_id=7, page=0, total_pages=1, operation_filter=None
    )
    cbs = _kb_callbacks(kb)
    # Выходы на два уровня
    assert "acc_history_select:7" in cbs  # ← К периодам
    assert "acc_back" in cbs  # ← К счетам
    # Рабочие фильтры по типу
    assert {
        "acc_hist_filter:all",
        "acc_hist_filter:expense",
        "acc_hist_filter:income",
    } <= set(cbs)
    # Мёртвых кнопок общей истории быть не должно (включая её пагинацию hist_page:)
    assert not any(c.startswith("hist_filter:") for c in cbs)
    assert not any(c.startswith("hist_page:") for c in cbs)
    assert "hist_open_record" not in cbs
    assert "hist_search:start" not in cbs
    assert "hist_show_all" not in cbs
    # Одна страница — без пагинации
    assert not any(c.startswith("acc_hist_page:") for c in cbs)


def test_account_history_keyboard_pagination():
    """Многостраничная история показывает пагинацию acc_hist_page:*."""
    from core.keyboards import account_history_keyboard

    kb = account_history_keyboard(
        account_id=7, page=1, total_pages=3, operation_filter="-"
    )
    cbs = _kb_callbacks(kb)
    assert "acc_hist_page:0" in cbs  # ◀ Назад
    assert "acc_hist_page:2" in cbs  # Вперёд ▶
    assert "acc_hist_page:noop" in cbs  # индикатор страницы


def test_account_history_keyboard_pagination_edges():
    """Крайние страницы: на первой нет «◀ Назад», на последней нет «Вперёд ▶»."""
    from core.keyboards import account_history_keyboard

    first = _kb_callbacks(account_history_keyboard(7, 0, 2, None))
    assert "acc_hist_page:-1" not in first  # нет «назад» с первой
    assert "acc_hist_page:1" in first  # есть «вперёд»

    last = _kb_callbacks(account_history_keyboard(7, 1, 2, None))
    assert "acc_hist_page:0" in last  # есть «назад»
    assert "acc_hist_page:2" not in last  # нет «вперёд» с последней


def test_account_history_keyboard_active_filter_marker():
    """Активный фильтр помечен галочкой."""
    from core.keyboards import account_history_keyboard

    kb = account_history_keyboard(7, 0, 1, operation_filter="-")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "✓ Расходы" in labels
    assert "Все" in labels and "✓ Все" not in labels


# ==================== get_history_data: история счёта ====================


@pytest.mark.asyncio
async def test_acc_history_data_includes_transfers(session):
    """include_transfers=True показывает переводы в истории счёта; default — нет."""
    user_id = await _make_user(session)
    acc1 = await _make_account(session, user_id, "Наличные")
    acc2 = await _make_account(session, user_id, "Карта")  # приёмник перевода
    await add_record(
        session, user_id, "-", Decimal("100"), category="Еда", account_id=acc1
    )
    await create_transfer(session, user_id, acc1, acc2, Decimal("500"))

    total_with, *_ = await get_history_data(
        session, user_id, "all", account_id=acc1, include_transfers=True
    )
    total_without, *_ = await get_history_data(session, user_id, "all", account_id=acc1)
    # С переводами: «Еда» + исходящий перевод = 2; без — только «Еда».
    assert total_with == 2
    assert total_without == 1


@pytest.mark.asyncio
async def test_acc_history_data_operation_filter(session):
    """operation_filter в истории счёта режет по типу, учитывая стороны перевода."""
    user_id = await _make_user(session)
    acc1 = await _make_account(session, user_id, "Наличные")
    acc2 = await _make_account(session, user_id, "Карта")
    await add_record(
        session, user_id, "+", Decimal("1000"), category="ЗП", account_id=acc1
    )
    await add_record(
        session, user_id, "-", Decimal("100"), category="Еда", account_id=acc1
    )
    await create_transfer(session, user_id, acc1, acc2, Decimal("500"))  # acc1: расход
    # Шум на другом счёте: не должен попасть в суммы/выборку по acc1.
    await add_record(
        session, user_id, "-", Decimal("999"), category="Прочее", account_id=acc2
    )

    # Только расходы счёта acc1: «Еда» + исходящая сторона перевода (acc2-шум не входит).
    total_exp, _, expense_exp, recs_exp = await get_history_data(
        session,
        user_id,
        "all",
        account_id=acc1,
        include_transfers=True,
        operation_filter="-",
    )
    assert total_exp == 2
    assert expense_exp == Decimal("600")  # 100 + 500, без 999 с acc2
    assert all(r.operation == "-" for r in recs_exp)

    # Только доходы счёта acc1: «ЗП» (перевод сюда не входит — он расход acc1).
    total_inc, income_inc, _, recs_inc = await get_history_data(
        session,
        user_id,
        "all",
        account_id=acc1,
        include_transfers=True,
        operation_filter="+",
    )
    assert total_inc == 1
    assert income_inc == Decimal("1000")
    assert all(r.operation == "+" for r in recs_inc)


@pytest.mark.asyncio
async def test_acc_history_data_empty_under_filter(session):
    """Фильтр без совпадений возвращает пустой результат (граничный случай)."""
    user_id = await _make_user(session)
    acc1 = await _make_account(session, user_id, "Наличные")
    acc2 = await _make_account(session, user_id, "Карта")
    await create_transfer(session, user_id, acc1, acc2, Decimal("500"))

    # У acc2 только входящий перевод (доход). Фильтр «расходы» → пусто.
    total, income, expense, records = await get_history_data(
        session,
        user_id,
        "all",
        account_id=acc2,
        include_transfers=True,
        operation_filter="-",
    )
    assert total == 0
    assert income == Decimal("0")
    assert expense == Decimal("0")
    assert records == []


# ==================== клавиатуры переводов ====================


def _make_transfer_dict(tid: int = 1):
    from datetime import datetime

    return {
        "transfer_id": tid,
        "amount": Decimal("5000"),
        "date": datetime(2026, 6, 1, 14, 30),
        "from_name": "Наличные",
        "to_name": "Карта",
    }


def test_transfers_list_keyboard_contract():
    """Каждый перевод — кнопка acc_tr_view:{id}; есть выход; пагинация при >1 стр."""
    from core.keyboards import transfers_list_keyboard

    transfers = [_make_transfer_dict(11), _make_transfer_dict(22)]
    kb = transfers_list_keyboard(transfers, page=0, total_pages=2)
    cbs = _kb_callbacks(kb)
    assert "acc_tr_view:11" in cbs and "acc_tr_view:22" in cbs
    assert "acc_back" in cbs  # выход
    # На первой странице есть «вперёд», но нет «назад».
    assert "acc_tr_page:1" in cbs
    assert "acc_tr_page:-1" not in cbs
    # Label кнопки несёт дату, направление и сумму (защита форматирования).
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any(
        "Наличные → Карта" in t and "01.06" in t and "5 000₽" in t for t in labels
    )


def test_transfers_list_keyboard_single_page_no_pagination():
    from core.keyboards import transfers_list_keyboard

    kb = transfers_list_keyboard([_make_transfer_dict(7)], page=0, total_pages=1)
    cbs = _kb_callbacks(kb)
    assert "acc_tr_view:7" in cbs
    assert not any(c.startswith("acc_tr_page:") for c in cbs)
    assert "acc_back" in cbs


def test_transfer_card_keyboard():
    """Карточка перевода: отмена + возврат к списку."""
    from core.keyboards import transfer_card_keyboard

    card = _kb_callbacks(transfer_card_keyboard(42))
    assert "acc_tr_cancel:42" in card
    assert "acc_transfers" in card  # ← к списку


def test_confirm_transfer_cancel_keyboard():
    """Подтверждение отмены: да → acc_tr_del, нет → возврат к карточке."""
    from core.keyboards import confirm_transfer_cancel_keyboard

    confirm = _kb_callbacks(confirm_transfer_cancel_keyboard(42))
    assert "acc_tr_del:42" in confirm  # да, отменить
    assert "acc_tr_view:42" in confirm  # нет → назад к карточке
