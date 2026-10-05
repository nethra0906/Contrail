"""GET /api/v1/anomalies - master spec Stage 5 deliverable. Reads what the
assembler's now-wired M4 rules layer (services/inference/anomaly_rules.py,
services/assembler/main.py) has actually detected and persisted - this
endpoint is a thin read over real data, not a second detection path.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.db import get_session
from services.common.models import Anomaly

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.get("")
async def list_anomalies(
    since_minutes: int = Query(60, ge=1, le=1440, description="Look back this many minutes"),
    kind: str | None = Query(None, description="Filter to one anomaly kind, e.g. 'rapid_descent'"),
    limit: int = Query(200, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=since_minutes)
    query = select(Anomaly).where(Anomaly.ts >= since).order_by(Anomaly.ts.desc()).limit(limit)
    if kind:
        query = query.where(Anomaly.kind == kind)

    rows = (await session.execute(query)).scalars().all()
    return [
        {
            "id": str(a.id),
            "icao24": a.icao24,
            "ts": a.ts.isoformat(),
            "kind": a.kind,
            "score": a.score,
            "evidence": a.evidence,
            "flight_id": str(a.flight_id) if a.flight_id else None,
        }
        for a in rows
    ]
