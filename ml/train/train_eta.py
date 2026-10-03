"""Trains M2 (ETA / arrival-delay regression): the baseline predictors plus
a LightGBM model, evaluated on a strict chronological split (never random -
execution rule 4). `make train` runs this (see Makefile). Produces a
versioned model artifact under data/models/ and a metrics JSON that
ml/eval/report.py turns into docs/ml-report.md's M2 section - no metric in
that report is ever typed by hand (execution rule 3).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from ml.data.loaders.bts import load_months
from ml.data.split import chronological_split
from ml.datasets.eta import TARGET_COLUMN, TaxiInLookup, build_feature_table, exclude_non_arrivals
from ml.eval.baselines import baseline_departure_carryover, baseline_scheduled
from ml.eval.metrics import eta_metrics
from ml.eval.report import update_report
from ml.export.register import register_and_maybe_promote_sync
from services.common.features.eta import FEATURE_COLUMNS
from services.common.telemetry import configure_logging, get_logger

logger = get_logger(__name__)

# A single month is enough data for a credible baseline-vs-LightGBM
# comparison (500k+ flights) while keeping `make train`'s runtime
# reasonable; ml/data/loaders/bts.load_months supports adding more months
# once a longer training window matters.
DEFAULT_MONTHS: list[tuple[int, int]] = [(2024, 1)]
MODEL_DIR = Path("data/models")


def train(year_months: list[tuple[int, int]] = DEFAULT_MONTHS) -> dict:
    logger.info("eta_training_start", months=year_months)

    raw = load_months(year_months)
    raw = exclude_non_arrivals(raw)
    split = chronological_split(raw, date_col="flight_date")
    logger.info(
        "eta_split",
        train_rows=len(split.train),
        val_rows=len(split.val),
        test_rows=len(split.test),
        train_end=str(split.train_end),
        val_end=str(split.val_end),
    )

    # Historic taxi-in medians are computed from the training fold only and
    # then applied read-only to val/test - see TaxiInLookup's docstring.
    taxi_in = TaxiInLookup(split.train)
    train_tbl = build_feature_table(split.train, taxi_in)
    val_tbl = build_feature_table(split.val, taxi_in)
    test_tbl = build_feature_table(split.test, taxi_in)

    baseline_metrics = {
        name: eta_metrics(test_tbl[TARGET_COLUMN], preds(test_tbl), test_tbl["sched_elapsed_min"])
        for name, preds in (
            ("scheduled", baseline_scheduled),
            ("departure_carryover", baseline_departure_carryover),
        )
    }
    for name, m in baseline_metrics.items():
        mae_min = m["overall"]["mae_min"]
        p90_min = m["overall"]["p90_min"]
        logger.info("eta_baseline", name=name, mae_min=mae_min, p90_min=p90_min)

    x_train, y_train = train_tbl[FEATURE_COLUMNS], train_tbl[TARGET_COLUMN]
    x_val, y_val = val_tbl[FEATURE_COLUMNS], val_tbl[TARGET_COLUMN]
    x_test, y_test = test_tbl[FEATURE_COLUMNS], test_tbl[TARGET_COLUMN]

    model = lgb.LGBMRegressor(
        objective="mae",  # MAE is the primary reported metric - optimize for it directly
        n_estimators=500,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=50,
        random_state=42,
    )
    model.fit(
        x_train,
        y_train,
        eval_set=[(x_val, y_val)],
        eval_metric="mae",
        callbacks=[lgb.early_stopping(stopping_rounds=20, verbose=False)],
    )

    test_pred = pd.Series(model.predict(x_test), index=y_test.index)
    lgbm_metrics = eta_metrics(y_test, test_pred, test_tbl["sched_elapsed_min"])
    logger.info(
        "eta_lgbm",
        mae_min=lgbm_metrics["overall"]["mae_min"],
        p90_min=lgbm_metrics["overall"]["p90_min"],
        best_iteration=model.best_iteration_,
    )

    version = f"eta-lgbm-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / f"{version}.txt"
    model.booster_.save_model(str(model_path))

    report = {
        "kind": "eta",
        "model_version": version,
        "trained_at": dt.datetime.now(dt.UTC).isoformat(),
        "train_window": {
            "months": year_months,
            "train_rows": len(split.train),
            "val_rows": len(split.val),
            "test_rows": len(split.test),
            "train_end": str(split.train_end),
            "val_end": str(split.val_end),
        },
        "baselines": baseline_metrics,
        "lightgbm": lgbm_metrics,
        "artifact_path": str(model_path),
    }
    metrics_path = MODEL_DIR / f"{version}.metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2))

    update_report(report)
    register_and_maybe_promote_sync(report)

    logger.info("eta_training_complete", version=version, artifact=str(model_path))
    return report


if __name__ == "__main__":
    configure_logging()
    train()
