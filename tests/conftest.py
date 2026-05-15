"""
Фикстуры pytest для тестирования БД.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))


import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.models import Base

# Подключение к тестовой БД (отдельная от основной)
test_engine = create_async_engine(url="sqlite+aiosqlite:///test_db.sqlite3")
test_session = async_sessionmaker(test_engine)


# Создаёт таблицы перед тестами, удаляет после
@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# Предоставляет сессию БД для каждого теста
@pytest_asyncio.fixture
async def session():
    async with test_session() as s:
        yield s
