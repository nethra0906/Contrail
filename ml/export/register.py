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
# error, median horizontal error) - a model kind whose primary metric is
# "higher is better" would need its own comparison direction here, not a
# blanket assumption. One path per `report["kind"]` - keyed by kind rather
# than a single shared path, since M1's report shape (per-horizon errors,
# no single "mae_min") is genuinely different from M2's.
_PRIMARY_METRIC_PATHS: dict[str, tuple[str, ...]] = {
    "eta": ("lightgbm", "overall", "mae_min"),
    "trajectory": ("model", "overall_median_error_km"),
    "network": ("model", "overall_mae_min"),
    # Reconstruction error, not the rule-agreement PR-AUC/precision@k: those
    # are measured against a tiny, highly imbalanced positive count in the
    # current training window (see docs/adr/0004) and are too noisy to use
    # as a promotion gate; reconstruction MAE is always computed on the full
    # test set and lower is unambiguously better, consistent with every
    # other kind's primary metric.
    "anomaly": ("model", "reconstruction_mae"),
}


def _primary_metric(metrics: dict, kind: str) -> float:
    """`metrics` is either a full training-run report (which has extra keys
    like model_version/trained_at alongside the metric fields) or the
    narrower dict stored in model_registry.metrics (see _metrics_payload) -
    both shapes contain whatever `_PRIMARY_METRIC_PATHS[kind]` points at,
    so one function serves both the new-run and incumbent-lookup call
    sites. `kind` is passed explicitly rather than read from the dict
    itself because the stored incumbent.metrics payload deliberately
    doesn't duplicate `kind` (it's already a column on the row).
    """
    if kind not in _PRIMARY_METRIC_PATHS:
        raise ValueError(f"no primary-metric path registered for model kind {kind!r}")
    value: Any = metrics
    for key in _PRIMARY_METRIC_PATHS[kind]:
        value = value[key]
    return float(value)


# Columns already stored separately on the ModelRegistry row - excluded from
# the JSONB `metrics` payload so the metrics a kind actually reports (and
# nothing else) is what both _primary_metric and ml/eval/report.py's
# renderers see, without per-kind special-casing which report fields count
# as "metrics" (M2's report has "baselines"/"lightgbm"; M1's has
# "baseline"/"model"/"interval_coverage_80" - this generalizes over both).
_NON_METRIC_REPORT_FIELDS = {"model_version", "kind", "trained_at", "train_window", "artifact_path"}


def _metrics_payload(report: dict) -> dict:
    return {k: v for k, v in report.items() if k not in _NON_METRIC_REPORT_FIELDS}


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
    kind = report["kind"]
    new_metric = _primary_metric(report, kind)

    async with sessionmaker() as session:
        incumbent = await _incumbent(session, kind)

        session.add(
            ModelRegistry(
                model_version=report["model_version"],
                kind=kind,
                trained_at=dt.datetime.fromisoformat(report["trained_at"]),
                train_window=report["train_window"],
                metrics=_metrics_payload(report),
                artifact_uri=report["artifact_path"],
                promoted=False,
            )
        )
        await session.flush()

        if incumbent is None:
            promoted = True
            logger.info("model_promoted", version=report["model_version"], reason="no incumbent")
        else:
            incumbent_metric = _primary_metric(incumbent.metrics, kind)
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
            new_row = await session.get(ModelRegistry, report["model_version"])
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
