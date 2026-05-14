"""Aggregates all handler routers. Import order matches the original handler registration order."""

from aiogram import Router

from .accounts import router as accounts_router
from .admin import router as admin_router
from .categories import router as categories_router
from .delete import router as delete_router
from .fallback import router as fallback_router
from .history import router as history_router
from .menu import router as menu_router
from .records import parse_record_line as parse_record_line
from .records import router as records_router
from .records_edit import router as records_edit_router
from .reports import router as reports_router
from .savings import router as savings_router

router = Router()
router.include_router(admin_router)
router.include_router(menu_router)
router.include_router(categories_router)
router.include_router(records_router)
router.include_router(history_router)
router.include_router(records_edit_router)
router.include_router(reports_router)
router.include_router(delete_router)
router.include_router(accounts_router)
router.include_router(savings_router)
router.include_router(fallback_router)  # must be last
