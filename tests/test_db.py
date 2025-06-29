import sys
from pathlib import Path
import pytest
import pytest_asyncio

from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo

# Добавляем путь к корню проекта, чтобы импорт сработал
sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import User, Record
from core.database.requests import (
    set_user,
    get_user_by_tg_id,
    add_record,
    get_records,
    delete_record,
)


@pytest.mark.asyncio
async def test_set_and_get_user(session):
    tg_id = 123456
    name = "Test User"
    phone = "999"

    # Создаем пользователя через сессию
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        user = User(tg_id=tg_id, name=name, phone=phone)
        session.add(user)
        await session.commit()
        await session.refresh(user)  # Обновляем объект после commit
    else:
        user.name = name
        user.phone = phone
        await session.commit()
        await session.refresh(user)  # Обновляем объект после commit

    # Получаем пользователя заново для проверки
    user = await get_user_by_tg_id(session, tg_id)
    assert user is not None
    assert user.tg_id == tg_id
    assert user.name == name
    assert user.phone == phone


@pytest.mark.asyncio
async def test_add_and_get_record(session):
    tg_id = 123456
    user = await get_user_by_tg_id(session, tg_id)
    
    # Создаем пользователя если его нет
    if not user:
        user = User(tg_id=tg_id, name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)  # Обновляем объект после commit
    else:
        await session.refresh(user)  # Обновляем объект

    user_id = user.id  # Сохраняем ID до использования
    ok = await add_record(session, tg_id, "+", 500.0, "зарплата")
    assert ok is True

    records = await get_records(session, user_id, "day")
    assert any(
        r.amount == Decimal("500.00") and r.category == "зарплата" for r in records
    )


@pytest.mark.asyncio
async def test_delete_record(session):
    tg_id = 123456
    user = await get_user_by_tg_id(session, tg_id)
    
    # Создаем пользователя если его нет
    if not user:
        user = User(tg_id=tg_id, name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)  # Обновляем объект после commit
    else:
        await session.refresh(user)  # Обновляем объект

    user_id = user.id  # Сохраняем ID до использования
    await add_record(session, tg_id, "-", 100.0, "еда")
    records = await get_records(session, user_id, "day")
    record_to_delete = next((r for r in records if r.amount == Decimal("100.00")), None)
    assert record_to_delete is not None

    deleted = await delete_record(session, tg_id, record_to_delete.id)
    assert deleted is True

    records_after = await get_records(session, user_id, "day")
    assert all(r.id != record_to_delete.id for r in records_after)


@pytest.mark.asyncio
async def test_get_records_month(session):
    tg_id = 123456
    user = await get_user_by_tg_id(session, tg_id)
    
    # Создаем пользователя если его нет
    if not user:
        user = User(tg_id=tg_id, name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)  # Обновляем объект после commit
    else:
        await session.refresh(user)  # Обновляем объект

    user_id = user.id  # Сохраняем ID до использования
    await add_record(session, tg_id, "-", 50.0, "кафе")

    records = await get_records(session, user_id, "month")
    assert any(r.category == "кафе" for r in records)


@pytest.mark.asyncio
async def test_get_records_range(session):
    tg_id = 123456
    user = await get_user_by_tg_id(session, tg_id)

    # Создаем пользователя если его нет
    if not user:
        user = User(tg_id=tg_id, name="Test User")
        session.add(user)
        await session.commit()
        await session.refresh(user)  # Обновляем объект после commit
    else:
        await session.refresh(user)  # Обновляем объект

    user_id = user.id  # Сохраняем ID до использования
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    records = await get_records(session, user_id, "range", start, end)
    assert isinstance(records, list)
