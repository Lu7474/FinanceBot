"""Goal handlers package: list/create/deposit/withdraw/manage/edit flows.

Aggregates per-feature routers into a single `router`. Re-exports a couple of
internals kept for backward-compatible imports (`from core.handlers.goals import ...`)
used by the test suite.
"""

from aiogram import Router

from . import create, deposit, edit, listing, manage, withdraw
from ._shared import _GOAL_ERROR_MESSAGES, _notify_family_goal_move

router = Router()
router.include_router(listing.router)
router.include_router(create.router)
router.include_router(deposit.router)
router.include_router(withdraw.router)
router.include_router(manage.router)
router.include_router(edit.router)

__all__ = ["router", "_GOAL_ERROR_MESSAGES", "_notify_family_goal_move"]
