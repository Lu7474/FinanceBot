"""
Фикстуры pytest для тестирования БД.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))


import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.database.models import Base, setup_sqlite_engine

# In-memory БД через shared-cache: все соединения процесса видят одну БД, даже на
# разных event-loop'ах (тесты без фикстуры `session` иначе ловят пустое :memory:).
# StaticPool держит одно соединение живым, чтобы shared-cache БД не обнулялась.
test_engine = create_async_engine(
    url="sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
setup_sqlite_engine(test_engine)  # Unicode lower() + FK, как на проде
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


def pytest_sessionfinish(session, exitstatus):
    """Dispose module-level engine at session end.

    StaticPool keeps one aiosqlite connection alive for the whole run; its
    worker is a non-daemon thread blocked on `while True: tx.get()`. Without
    dispose() the interpreter never exits and the CI step hangs at shutdown.
    """
    import asyncio

    asyncio.run(test_engine.dispose())
