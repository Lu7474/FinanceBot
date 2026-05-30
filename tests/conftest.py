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


_exit_status = 0


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


def pytest_unconfigure(config):
    """Force a clean process exit so the CI step doesn't hang.

    App code uses a module-level production engine and a chart
    ThreadPoolExecutor; under pytest-asyncio every test runs on its own event
    loop, so some aiosqlite connection workers stay bound to already-closed
    loops and can't be disposed from a fresh loop. Those non-daemon threads
    keep Python 3.14 from exiting (the process hangs after the summary). We
    release what we can, then os._exit with pytest's status. unconfigure is
    the last hook, so the terminal summary is already printed. (Prod is
    unaffected — it has one loop and shuts engine + executor down cleanly.)
    """
    import asyncio
    import os
    import sys

    from core.charts import shutdown_executor
    from core.database.models import engine as prod_engine

    async def _close() -> None:
        await test_engine.dispose()
        await prod_engine.dispose()

    try:
        asyncio.run(_close())
        shutdown_executor()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_exit_status)
