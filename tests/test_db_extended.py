"""
Расширенные тесты БД: фильтры периодов, years_and_months, обработка ошибок.
"""
import sys
from pathlib import Path
import pytest

from decimal import Decimal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database.models import User, Record
from core.database.requests import (
    add_record,
    get_records,
    delete_record,
)
from core.utils import get_available_years_and_months


# ==================== get_available_years_and_months ====================

# Пользователь без записей → пустой словарь
@pytest.mark.asyncio
async def test_get_available_years_and_months_empty(session):
    tg_id = 999999
    user = User(tg_id=tg_id, name="Empty User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    result = await get_available_years_and_months(session, user.id)
    assert result == {}


# Записи за разные годы/месяцы → правильная группировка
@pytest.mark.asyncio
async def test_get_available_years_and_months_with_records(session):
    tg_id = 888888
    user = User(tg_id=tg_id, name="Test User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    user_id = user.id  # Сохраняем ID

    # Создаем записи за разные месяцы
    dates = [
        datetime(2023, 12, 15),  # Прошлый год
        datetime(2024, 1, 10),  # Январь
        datetime(2024, 6, 20),  # Июнь
        datetime(2024, 12, 5),  # Декабрь
    ]

    for i, date in enumerate(dates):
        record = Record(
            user_id=user_id,  # Используем сохраненный ID
            operation="+",
            amount=Decimal("100.00"),
            category=f"test_{i}",
            created_at=date,
        )
        session.add(record)

    await session.commit()

    result = await get_available_years_and_months(
        session, user_id
    )  # Используем сохраненный ID

    # Проверяем, что есть записи за 2023 и 2024
    assert 2023 in result
    assert 2024 in result

    # Проверяем месяцы
    assert result[2023] == [12]
    assert 1 in result[2024]
    assert 6 in result[2024]
    assert 12 in result[2024]


# Будущие месяцы должны отфильтровываться
@pytest.mark.asyncio
async def test_get_available_years_and_months_future_filtering(session):
    tg_id = 777777
    user = User(tg_id=tg_id, name="Future User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    user_id = user.id  # Сохраняем ID

    now = datetime.now(ZoneInfo("Europe/Moscow"))
    current_year = now.year
    current_month = now.month

    # Создаем записи: прошлый месяц, текущий месяц, будущий месяц
    past_month = current_month - 1 if current_month > 1 else 12
    past_year = current_year if current_month > 1 else current_year - 1

    future_month = current_month + 1 if current_month < 12 else 1
    future_year = current_year if current_month < 12 else current_year + 1

    records = [
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("100"),
            category="past",
            created_at=datetime(past_year, past_month, 15),
        ),
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("100"),
            category="current",
            created_at=datetime(current_year, current_month, 15),
        ),
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("100"),
            category="future",
            created_at=datetime(future_year, future_month, 15),
        ),
    ]

    for record in records:
        session.add(record)
    await session.commit()

    result = await get_available_years_and_months(
        session, user_id
    )  # Используем сохраненный ID

    # Будущие месяцы должны быть отфильтрованы
    if future_year in result:
        assert future_month not in result[future_year]


# ==================== get_records (расширенные) ====================

# Фильтр "day" — только сегодняшние записи
@pytest.mark.asyncio
async def test_get_records_day(session):
    tg_id = 666666
    user = User(tg_id=tg_id, name="Day User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    user_id = user.id  # Сохраняем ID

    # Создаем записи за сегодня и вчера
    today = datetime.now(ZoneInfo("Europe/Moscow"))
    yesterday = today - timedelta(days=1)

    records = [
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("100"),
            category="today",
            created_at=today,
        ),
        Record(
            user_id=user_id,
            operation="-",
            amount=Decimal("50"),
            category="today",
            created_at=today,
        ),
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("200"),
            category="yesterday",
            created_at=yesterday,
        ),
    ]

    for record in records:
        session.add(record)
    await session.commit()

    # Получаем записи за день
    day_records = await get_records(
        session, user_id, "day"
    )  # Используем сохраненный ID

    # Должны быть только записи за сегодня
    assert len(day_records) == 2
    assert all("today" in r.category for r in day_records)


# Фильтр "year" — только записи за текущий год
@pytest.mark.asyncio
async def test_get_records_year(session):
    tg_id = 555555
    user = User(tg_id=tg_id, name="Year User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    user_id = user.id  # Сохраняем ID
    current_year = datetime.now(ZoneInfo("Europe/Moscow")).year

    # Создаем записи за текущий и прошлый год
    records = [
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("100"),
            category="current_year",
            created_at=datetime(current_year, 6, 15),
        ),
        Record(
            user_id=user_id,
            operation="-",
            amount=Decimal("50"),
            category="current_year",
            created_at=datetime(current_year, 12, 15),
        ),
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("200"),
            category="past_year",
            created_at=datetime(current_year - 1, 6, 15),
        ),
    ]

    for record in records:
        session.add(record)
    await session.commit()

    # Получаем записи за год
    year_records = await get_records(
        session, user_id, "year"
    )  # Используем сохраненный ID

    # Должны быть только записи за текущий год
    assert len(year_records) == 2
    assert all("current_year" in r.category for r in year_records)


# Фильтр "date" — записи за конкретную дату
@pytest.mark.asyncio
async def test_get_records_date(session):
    tg_id = 444444
    user = User(tg_id=tg_id, name="Date User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    user_id = user.id  # Сохраняем ID
    target_date = datetime(2024, 6, 15)

    # Создаем записи за целевую дату и соседние дни
    records = [
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("100"),
            category="target_date",
            created_at=target_date,
        ),
        Record(
            user_id=user_id,
            operation="-",
            amount=Decimal("50"),
            category="target_date",
            created_at=target_date,
        ),
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("200"),
            category="other_date",
            created_at=target_date + timedelta(days=1),
        ),
    ]

    for record in records:
        session.add(record)
    await session.commit()

    # Получаем записи за конкретную дату
    date_records = await get_records(
        session, user_id, "date", target_date
    )  # Используем сохраненный ID

    # Должны быть только записи за целевую дату
    assert len(date_records) == 2
    assert all("target_date" in r.category for r in date_records)


# Фильтр "all" — все записи без ограничений
@pytest.mark.asyncio
async def test_get_records_all(session):
    tg_id = 333333
    user = User(tg_id=tg_id, name="All User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    user_id = user.id  # Сохраняем ID до использования

    # Создаем записи за разные периоды
    records = [
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("100"),
            category="record1",
            created_at=datetime(2023, 1, 15),
        ),
        Record(
            user_id=user_id,
            operation="-",
            amount=Decimal("50"),
            category="record2",
            created_at=datetime(2024, 6, 15),
        ),
        Record(
            user_id=user_id,
            operation="+",
            amount=Decimal("200"),
            category="record3",
            created_at=datetime(2024, 12, 15),
        ),
    ]

    for record in records:
        session.add(record)
    await session.commit()

    # Получаем все записи
    all_records = await get_records(
        session, user_id, "all"
    )  # Используем сохраненный ID

    # Должны быть все записи
    assert len(all_records) == 3


# ==================== Обработка ошибок ====================

# Добавление записи несуществующему пользователю
# Примечание: SQLite не применяет FK constraints по умолчанию,
# поэтому add_record возвращает True даже для несуществующего user_id.
# В продакшене с включёнными FK constraints это вернёт False.
@pytest.mark.asyncio
async def test_add_record_nonexistent_user(session):
    result = await add_record(session, 999999, "+", 100.0, "test")
    # SQLite без FK constraints позволяет добавить запись
    assert result is True


# Удаление несуществующей записи → False
@pytest.mark.asyncio
async def test_delete_record_nonexistent(session):
    tg_id = 222222
    user = User(tg_id=tg_id, name="Delete User")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    result = await delete_record(session, user.id, 999999)
    assert result is False


# Удаление чужой записи → False (защита)
@pytest.mark.asyncio
async def test_delete_record_wrong_user(session):
    # Создаем двух пользователей
    user1 = User(tg_id=111111, name="User 1")
    user2 = User(tg_id=222222, name="User 2")
    session.add(user1)
    session.add(user2)
    await session.commit()
    await session.refresh(user1)
    await session.refresh(user2)

    # Сохраняем user_id до использования
    user1_id = user1.id
    user2_id = user2.id

    # Создаем запись для первого пользователя
    record = Record(
        user_id=user1_id, operation="+", amount=Decimal("100"), category="test"
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    # Пытаемся удалить запись вторым пользователем
    result = await delete_record(session, user2_id, record.id)
    assert result is False


# Записи несуществующего пользователя → пустой список
@pytest.mark.asyncio
async def test_get_records_nonexistent_user(session):
    records = await get_records(session, 999999, "day")
    assert records == []
