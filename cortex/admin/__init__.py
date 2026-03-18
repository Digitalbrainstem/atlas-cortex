"""Admin API package — domain routers assembled under /admin prefix."""

# Module ownership: Admin API domain routers

from __future__ import annotations

from fastapi import APIRouter

from cortex.admin.auth import router as auth_router
from cortex.admin.dashboard import router as dashboard_router
from cortex.admin.users import router as users_router
from cortex.admin.safety import router as safety_router
from cortex.admin.devices import router as devices_router
from cortex.admin.system import router as system_router
from cortex.admin.satellites import router as satellites_router
from cortex.admin.tts import router as tts_router
from cortex.admin.avatar import router as avatar_router
from cortex.admin.plugins import router as plugins_router
from cortex.admin.learning import router as learning_router
from cortex.admin.scheduling import router as scheduling_router
from cortex.admin.routines import router as routines_router
from cortex.admin.evolution import router as evolution_router
from cortex.admin.stories import router as stories_router
from cortex.admin.proactive import router as proactive_router

router = APIRouter(prefix="/admin", tags=["admin"])

router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(users_router)
router.include_router(safety_router)
router.include_router(devices_router)
router.include_router(system_router)
router.include_router(satellites_router)
router.include_router(tts_router)
router.include_router(avatar_router)
router.include_router(plugins_router)
router.include_router(learning_router)
router.include_router(scheduling_router)
router.include_router(routines_router)
router.include_router(evolution_router)
router.include_router(stories_router)
router.include_router(proactive_router)
