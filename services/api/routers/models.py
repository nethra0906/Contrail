"""`/api/v1/models/scorecard`: the model(s) currently being served, with the
metrics `ml/eval/` computed for them at training/promotion time (see
ml/export/register.py). This is Stage 4's live scorecard endpoint - "live"
in the sense that it always reflects the current promoted model, not a
cached report; the metrics themselves update whenever a new model is
promoted, not continuously in real time (that needs the `prediction_scores`
scoring-join job, which depends on M1 being trained against real live data -
see ml/train/train_trajectory.py's docstring for why that isn't wired up
yet).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.db import get_session
from services.common.models.ml import ModelRegistry

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/scorecard")
async def get_scorecard(
    model: str | None = Query(None, description="Filter to one model kind, e.g. 'eta'"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The currently PROMOTED model per kind - what's actually being served,
    not a list of every training run ever made. A kind with no promoted
    model yet (never trained, or every run so far failed to beat the
    incumbent) is simply absent from the response, not backfilled with a
    stale or synthetic entry.
    """
    query = select(ModelRegistry).where(ModelRegistry.promoted.is_(True))
    if model:
        query = query.where(ModelRegistry.kind == model)
    rows = (await session.execute(query.order_by(ModelRegistry.kind))).scalars().all()

    return {
        "models": [
            {
                "kind": row.kind,
                "model_version": row.model_version,
                "trained_at": row.trained_at.isoformat(),
                "train_window": row.train_window,
                "metrics": row.metrics,
            }
            for row in rows
        ]
    }
