import sys
from pathlib import Path

# Добавляем путь к корню проекта
sys.path.append(str(Path(__file__).resolve().parent.parent))


from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from core.database.models import Base

import pytest_asyncio

# Создаем тестовый engine
test_engine = create_async_engine(url="sqlite+aiosqlite:///test_db.sqlite3")
test_session = async_sessionmaker(test_engine)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def session():
    async with test_session() as s:
        yield s
