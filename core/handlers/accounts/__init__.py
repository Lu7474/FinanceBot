"""Account management split into CRUD, transfers and history submodules.

Re-exports the aggregated ``router`` and ``_annotate_transfer_direction``
(used by tests) to keep ``core.handlers.accounts`` import-compatible.
"""

from aiogram import Router

from .common import router as _common_router
from .crud import router as _crud_router
from .history import _annotate_transfer_direction as _annotate_transfer_direction
from .history import router as _history_router
from .transfers import router as _transfers_router

router = Router()
router.include_router(_common_router)
router.include_router(_crud_router)
router.include_router(_transfers_router)
router.include_router(_history_router)
