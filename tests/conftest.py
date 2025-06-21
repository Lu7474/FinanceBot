import sys
from pathlib import Path

# Добавляем путь к корню проекта
sys.path.append(str(Path(__file__).resolve().parent.parent))

# Теперь можно импортировать модули
from core.database.models import engine, async_session, Base

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def session():
    async with async_session() as s:
        yield s
