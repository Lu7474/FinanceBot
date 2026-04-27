"""Aggregates all handler routers. Import order matches the original handler registration order."""

from aiogram import Router

from .records import parse_record_line  # re-exported for tests and external use

from .menu import router as menu_router
from .records import router as records_router
from .history import router as history_router
from .reports import router as reports_router
from .delete import router as delete_router
from .accounts import router as accounts_router
from .fallback import router as fallback_router

router = Router()
router.include_router(menu_router)
router.include_router(records_router)
router.include_router(history_router)
router.include_router(reports_router)
router.include_router(delete_router)
router.include_router(accounts_router)
router.include_router(fallback_router)  # must be last
