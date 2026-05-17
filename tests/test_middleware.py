"""Tests for RateLimitMiddleware and UserMiddleware (review #7 — middleware coverage)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.middleware import RateLimiter, RateLimitMiddleware, UserMiddleware

# ==================== RateLimiter (unit) ====================


def test_rate_limiter_allows_under_limit():
    rl = RateLimiter(max_requests=60, window_seconds=60)
    for _ in range(60):
        assert rl.is_allowed(123) is True


def test_rate_limiter_blocks_after_limit():
    """60+1 запросов — последний должен быть отброшен."""
    rl = RateLimiter(max_requests=60, window_seconds=60)
    for _ in range(60):
        rl.is_allowed(42)
    assert rl.is_allowed(42) is False


def test_rate_limiter_isolated_per_user():
    rl = RateLimiter(max_requests=5, window_seconds=60)
    for _ in range(5):
        rl.is_allowed(1)
    assert rl.is_allowed(1) is False
    # другой юзер — счётчик отдельный
    assert rl.is_allowed(2) is True


def test_rate_limiter_window_slides():
    """После окна старые таймстемпы выбрасываются — новые запросы снова разрешены."""
    import time as time_module

    rl = RateLimiter(max_requests=3, window_seconds=60)
    base = time_module.time()

    # старые таймстемпы вне окна
    rl._requests[7] = [base - 120, base - 100, base - 80]
    assert rl.is_allowed(7) is True


def test_rate_limiter_cleanup_removes_inactive():
    import time as time_module

    rl = RateLimiter(max_requests=60, window_seconds=60)
    rl._requests[100] = [time_module.time() - 500]  # вне окна
    rl._requests[200] = [time_module.time()]  # активный
    rl._last_cleanup = 0  # форсируем cleanup

    rl._cleanup_inactive_users()

    assert 100 not in rl._requests
    assert 200 in rl._requests


# ==================== RateLimitMiddleware ====================


def _make_message(tg_id: int):
    from aiogram.types import Message

    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = tg_id
    msg.text = None
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_rate_limit_middleware_blocks_after_60(monkeypatch):
    """61-й запрос блокируется, хендлер не вызывается."""
    from core import middleware as mw

    rl = RateLimiter(max_requests=2, window_seconds=60)
    monkeypatch.setattr(mw, "rate_limiter", rl)
    monkeypatch.setattr("config.ADMIN_ID", 999_999)

    middleware = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")
    msg = _make_message(tg_id=42)

    assert await middleware(handler, msg, {}) == "ok"
    assert await middleware(handler, msg, {}) == "ok"
    # 3-й — блок
    result = await middleware(handler, msg, {})
    assert result is None
    assert handler.call_count == 2
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_rate_limit_middleware_admin_bypass(monkeypatch):
    """ADMIN_ID не подпадает под rate limit."""
    from core import middleware as mw

    rl = RateLimiter(max_requests=1, window_seconds=60)
    monkeypatch.setattr(mw, "rate_limiter", rl)
    monkeypatch.setattr("config.ADMIN_ID", 7777)

    middleware = RateLimitMiddleware()
    handler = AsyncMock(return_value="ok")
    msg = _make_message(tg_id=7777)

    # лимит = 1, но админ — без ограничений
    for _ in range(5):
        assert await middleware(handler, msg, {}) == "ok"
    assert handler.call_count == 5


# ==================== UserMiddleware ====================


class _AsyncSessionCM:
    """Минимальный async-context-manager, отдаёт фиктивную сессию."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_user_middleware_blocks_banned(monkeypatch):
    """Забаненный пользователь не пропускается дальше, хендлер не вызывается."""
    banned_user = MagicMock()
    banned_user.id = 5
    banned_user.is_banned = True

    fake_session = MagicMock()
    monkeypatch.setattr(
        "core.database.models.async_session",
        lambda: _AsyncSessionCM(fake_session),
    )
    monkeypatch.setattr(
        "core.database.requests.get_user_by_tg_id",
        AsyncMock(return_value=banned_user),
    )
    monkeypatch.setattr("config.ADMIN_ID", 999_999)

    middleware = UserMiddleware()
    handler = AsyncMock(return_value="should-not-be-called")
    msg = _make_message(tg_id=42)

    result = await middleware(handler, msg, {})
    assert result is None
    handler.assert_not_awaited()
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_user_middleware_admin_bypass_when_banned(monkeypatch):
    """Даже если ADMIN_ID почему-то is_banned=True — он не блокируется."""
    banned_admin = MagicMock()
    banned_admin.id = 5
    banned_admin.is_banned = True

    monkeypatch.setattr(
        "core.database.models.async_session",
        lambda: _AsyncSessionCM(MagicMock()),
    )
    monkeypatch.setattr(
        "core.database.requests.get_user_by_tg_id",
        AsyncMock(return_value=banned_admin),
    )
    monkeypatch.setattr("config.ADMIN_ID", 7777)

    middleware = UserMiddleware()
    handler = AsyncMock(return_value="ok")
    msg = _make_message(tg_id=7777)

    result = await middleware(handler, msg, {})
    assert result == "ok"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_middleware_passes_user_id_to_handler(monkeypatch):
    """Нормальный (не банный) юзер — kwargs["user_id"] прокидывается в хендлер."""
    normal_user = MagicMock()
    normal_user.id = 17
    normal_user.is_banned = False

    monkeypatch.setattr(
        "core.database.models.async_session",
        lambda: _AsyncSessionCM(MagicMock()),
    )
    monkeypatch.setattr(
        "core.database.requests.get_user_by_tg_id",
        AsyncMock(return_value=normal_user),
    )
    monkeypatch.setattr("config.ADMIN_ID", 0)

    middleware = UserMiddleware()
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)
        return "ok"

    msg = _make_message(tg_id=42)
    result = await middleware(handler, msg, {})

    assert result == "ok"
    assert captured.get("user_id") == 17
    assert captured.get("user_tg_id") == 42
