"""Registers a training run's result into `model_registry` and decides
promotion: "must beat the incumbent on a held-out chronological window.
Failed promotions are logged, never silently applied" (master spec §7,
"Serving & retraining").

Best-effort by design: `make train`'s DoD is "produces a versioned model and
a report with real numbers" - that must succeed even when no Postgres is
reachable (e.g. running training standalone, without `docker compose up`).
A registration failure is logged and swallowed here, never raised past
`train()`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.db import get_sessionmaker
from services.common.models.ml import ModelRegistry
from services.common.telemetry import get_logger

logger = get_logger(__name__)

# Lower is better for every metric this project reports (MAE, P90 abs
# error) - a model kind whose primary metric is "higher is better" would
# need its own comparison direction here, not a blanket assumption.
_PRIMARY_METRIC_PATH = ("lightgbm", "overall", "mae_min")


def _primary_metric(report: dict) -> float:
    value: Any = report
    for key in _PRIMARY_METRIC_PATH:
        value = value[key]
    return float(value)


async def _incumbent(session: AsyncSession, kind: str) -> ModelRegistry | None:
    result = await session.execute(
        select(ModelRegistry)
        .where(ModelRegistry.kind == kind, ModelRegistry.promoted.is_(True))
        .order_by(ModelRegistry.trained_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def register_and_maybe_promote(report: dict) -> bool:
    """Inserts the new model as a non-promoted row, then promotes it (and
    demotes the previous incumbent) only if it beats the current promoted
    model on the primary metric. Returns whether it was promoted.
    """
    sessionmaker = get_sessionmaker()
    new_metric = _primary_metric(report)

    async with sessionmaker() as session:
        incumbent = await _incumbent(session, report["kind"])

        session.add(
            ModelRegistry(
                model_version=report["model_version"],
                kind=report["kind"],
                trained_at=dt.datetime.fromisoformat(report["trained_at"]),
                train_window=report["train_window"],
                metrics={"baselines": report["baselines"], "lightgbm": report["lightgbm"]},
                artifact_uri=report["artifact_path"],
                promoted=False,
            )
        )
        await session.flush()

        if incumbent is None:
            promoted = True
            logger.info(
                "model_promoted", version=report["model_version"], reason="no incumbent"
            )
        else:
            incumbent_metric = _primary_metric(incumbent.metrics)
            promoted = new_metric < incumbent_metric
            logger.info(
                "model_promotion_decision",
                version=report["model_version"],
                promoted=promoted,
                new_metric=new_metric,
                incumbent_version=incumbent.model_version,
                incumbent_metric=incumbent_metric,
            )
            if promoted:
                incumbent.promoted = False

        if promoted:
            new_row = await session.get(
                ModelRegistry, report["model_version"]
            )
            assert new_row is not None
            new_row.promoted = True

        await session.commit()

    return promoted


def register_and_maybe_promote_sync(report: dict) -> bool | None:
    """Sync-friendly wrapper for training scripts (lightgbm/pandas code is
    synchronous end to end) - returns None rather than raising when no
    database is reachable, so `make train` still succeeds without Docker up.
    """
    import asyncio

    try:
        return asyncio.run(register_and_maybe_promote(report))
    except Exception:
        logger.warning("model_registry_unreachable", model_version=report["model_version"])
        return None
