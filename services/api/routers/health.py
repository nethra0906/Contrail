from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.cache import get_redis
from services.common.db import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness check - no dependencies, must always return fast."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(session: AsyncSession = Depends(get_session)) -> dict:
    """Readiness check - verifies Postgres and Redis are actually reachable.
    Used by orchestration health checks, not by the browser.
    """
    checks = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - deliberately broad, this IS the check
        checks["postgres"] = f"error: {exc}"

    try:
        redis = get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return {"status": "ok" if healthy else "degraded", "checks": checks}
