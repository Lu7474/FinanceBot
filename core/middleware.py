"""
Rate limiting and user context middleware.
"""

import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class RateLimiter:
    CLEANUP_INTERVAL = 300  # 5 минут

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: Dict[int, list] = {}
        self._last_cleanup = time.time()

    def _cleanup_inactive_users(self) -> None:
        now = time.time()
        if now - self._last_cleanup < self.CLEANUP_INTERVAL:
            return

        window_start = now - self.window_seconds
        inactive_users = [
            uid
            for uid, timestamps in self._requests.items()
            if not timestamps or max(timestamps) < window_start
        ]
        for uid in inactive_users:
            del self._requests[uid]

        self._last_cleanup = now
        if inactive_users:
            logging.debug(
                f"RateLimiter: очищено {len(inactive_users)} неактивных пользователей"
            )

    def is_allowed(self, user_id: int) -> bool:
        self._cleanup_inactive_users()

        now = time.time()
        window_start = now - self.window_seconds

        if user_id not in self._requests:
            self._requests[user_id] = []

        self._requests[user_id] = [
            ts for ts in self._requests[user_id] if ts > window_start
        ]

        if len(self._requests[user_id]) >= self.max_requests:
            return False

        self._requests[user_id].append(now)
        return True

    def get_retry_after(self, user_id: int) -> int:
        if user_id not in self._requests or not self._requests[user_id]:
            return 0

        now = time.time()
        oldest = min(self._requests[user_id])
        retry_after = int(oldest + self.window_seconds - now) + 1
        return max(0, retry_after)


rate_limiter = RateLimiter(max_requests=60, window_seconds=60)


class RateLimitMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        from config import ADMIN_ID

        if user_id and user_id == ADMIN_ID:
            return await handler(event, data)

        if user_id and not rate_limiter.is_allowed(user_id):
            retry_after = rate_limiter.get_retry_after(user_id)
            logging.warning(
                f"Rate limit для user_id={user_id}, retry_after={retry_after}s"
            )
            if isinstance(event, Message):
                await event.answer(
                    f"Слишком много запросов. Подождите {retry_after} сек."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(f"Подождите {retry_after} сек.", show_alert=True)
            return None

        return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Получает внутренний user_id из БД один раз и передаёт в хендлеры."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        from core.database.models import async_session
        from core.database.requests import get_user_by_tg_id

        tg_id = None
        if isinstance(event, Message) and event.from_user:
            tg_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_id = event.from_user.id

        if tg_id:
            from config import ADMIN_ID

            async with async_session() as session:
                user = await get_user_by_tg_id(session, tg_id)
                if user:
                    if user.is_banned and tg_id != ADMIN_ID:
                        if isinstance(event, Message):
                            await event.answer("⛔ Ваш аккаунт заблокирован.")
                        elif isinstance(event, CallbackQuery):
                            await event.answer(
                                "⛔ Ваш аккаунт заблокирован.", show_alert=True
                            )
                        return None
                    data["user_id"] = user.id
                    data["user_tg_id"] = tg_id
                else:
                    is_start_cmd = (
                        isinstance(event, Message)
                        and event.text
                        and event.text.startswith("/start")
                    )
                    if not is_start_cmd:
                        if isinstance(event, Message):
                            await event.answer("Для начала работы отправьте /start")
                        elif isinstance(event, CallbackQuery):
                            await event.answer(
                                "Отправьте /start для регистрации", show_alert=True
                            )
                        return None

        return await handler(event, data)
