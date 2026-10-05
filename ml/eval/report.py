"""Renders docs/ml-report.md from a training run's metrics JSON (the
`report` dict `ml/train/train_eta.py` writes to `data/models/*.metrics.json`)
- no number in the output is typed by hand, per execution rule 3. Each
model's section is generated independently and appended/replaced by model
kind, so M1/M3's future sections don't require rewriting this module.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

REPORT_PATH = Path("docs/ml-report.md")

_SECTION_START = "<!-- ml-report:{kind}:start -->"
_SECTION_END = "<!-- ml-report:{kind}:end -->"


def _fmt_metrics_table(metrics: dict) -> str:
    overall = metrics["overall"]
    rows = [f"| overall | {overall['mae_min']:.2f} | {overall['p90_min']:.2f} | {overall['n']} |"]
    for label, m in metrics["by_duration_bucket"].items():
        rows.append(f"| {label} | {m['mae_min']:.2f} | {m['p90_min']:.2f} | {m['n']} |")
    header = "| bucket | MAE (min) | P90 abs error (min) | n |\n|---|---|---|---|"
    return header + "\n" + "\n".join(rows)


def render_eta_section(report: dict) -> str:
    train_window = report["train_window"]
    lines = [
        "## M2 - ETA regression (arrival delay)",
        "",
        f"Model version: `{report['model_version']}` - trained {report['trained_at']}",
        "",
        f"Training window: months {train_window['months']}, "
        f"{train_window['train_rows']} train / {train_window['val_rows']} val / "
        f"{train_window['test_rows']} test rows (chronological split, train ends "
        f"{train_window['train_end']}, val ends {train_window['val_end']}).",
        "",
        "### Baseline: predict zero arrival delay",
        "",
        _fmt_metrics_table(report["baselines"]["scheduled"]),
        "",
        "### Baseline: propagate departure delay to arrival",
        "",
        _fmt_metrics_table(report["baselines"]["departure_carryover"]),
        "",
        "### LightGBM",
        "",
        _fmt_metrics_table(report["lightgbm"]),
        "",
        f"Artifact: `{report['artifact_path']}`",
        "",
    ]
    return "\n".join(lines)


def _fmt_trajectory_table(per_horizon: dict) -> str:
    rows = []
    for horizon, m in per_horizon.items():
        if m["median_error_km"] is None:
            rows.append(f"| {horizon}s | - | - | 0 |")
        else:
            rows.append(
                f"| {horizon}s | {m['median_error_km']:.2f} | {m['p90_error_km']:.2f} | {m['n']} |"
            )
    header = "| horizon | median error (km) | P90 error (km) | n |\n|---|---|---|---|"
    return header + "\n" + "\n".join(rows)


def render_trajectory_section(report: dict) -> str:
    train_window = report["train_window"]
    model = report["model"]
    baseline = report["baseline"]
    lines = [
        "## M1 - Trajectory forecasting (probabilistic)",
        "",
        f"Model version: `{report['model_version']}` - trained {report['trained_at']}",
        "",
        f"Training data: {train_window['source']}, "
        f"{train_window['train_windows']} train / {train_window['val_windows']} val / "
        f"{train_window['test_windows']} test windows (chronological split by window "
        f"anchor time, train ends {train_window['train_end']}, val ends "
        f"{train_window['val_end']}). See docs/adr/0004 for why this trains on ADS-B "
        "Exchange historical samples rather than this project's own live-ingested history.",
        "",
        "### Baseline: constant-velocity dead reckoning",
        "",
        _fmt_trajectory_table(baseline["per_horizon"]),
        "",
        "### GRU (quantile regression, median/q50)",
        "",
        _fmt_trajectory_table(model["per_horizon"]),
        "",
        "### 80% interval coverage (fraction of true positions inside [q10, q90])",
        "",
        "| horizon | coverage |\n|---|---|\n"
        + "\n".join(
            f"| {h}s | {c:.1%} |" if c is not None else f"| {h}s | n/a |"
            for h, c in model["interval_coverage_80"].items()
        ),
        "",
        f"Artifact: `{report['artifact_path']}` (ONNX)",
        "",
    ]
    return "\n".join(lines)


def _fmt_network_table(per_horizon_hours: dict) -> str:
    rows = []
    for h in sorted(per_horizon_hours):
        m = per_horizon_hours[h]
        if m["mae_min"] is None:
            rows.append(f"| t+{h}h | - | - | 0 |")
        else:
            rows.append(f"| t+{h}h | {m['mae_min']:.2f} | {m['rmse_min']:.2f} | {m['n']} |")
    header = "| horizon | MAE (min) | RMSE (min) | n |\n|---|---|---|---|"
    return header + "\n" + "\n".join(rows)


def render_network_section(report: dict) -> str:
    train_window = report["train_window"]
    lines = [
        "## M3 - Delay-propagation GNN",
        "",
        f"Model version: `{report['model_version']}` - trained {report['trained_at']}",
        "",
        f"Training data: {train_window['source']}, {train_window['nodes']} airport nodes, "
        f"{train_window['train_sequences']} train / {train_window['val_sequences']} val / "
        f"{train_window['test_sequences']} test sequences (chronological split by time bucket). "
        "See docs/adr/0004 for why this trains on one BTS month and omits live-weather node "
        "features.",
        "",
        "### Baseline: historical mean by (airport, hour, day-of-week)",
        "",
        _fmt_network_table(report["historical_mean_baseline"]["per_horizon_hours"]),
        "",
        "### Baseline: LightGBM on flat features + neighbor delay",
        "",
        _fmt_network_table(report["lgbm_baseline"]["per_horizon_hours"]),
        "",
        "### Diffusion-GCN + temporal GRU",
        "",
        _fmt_network_table(report["model"]["per_horizon_hours"]),
        "",
        f"Artifact: `{report['artifact_path']}`",
        "",
    ]
    return "\n".join(lines)


def render_anomaly_section(report: dict) -> str:
    train_window = report["train_window"]
    model = report["model"]
    ra = model["rule_agreement"]
    pr_auc = f"{ra['pr_auc_vs_rules']:.4f}" if ra["pr_auc_vs_rules"] is not None else "n/a"
    precision_at_k = (
        f"{ra['precision_at_k_vs_rules']:.4f}"
        if ra["precision_at_k_vs_rules"] is not None
        else "n/a"
    )
    lines = [
        "## M4 - Learned anomaly layer",
        "",
        f"Model version: `{report['model_version']}` - trained {report['trained_at']}",
        "",
        f"Training data: {train_window['source']}, {train_window['train_segments']} train / "
        f"{train_window['test_segments']} test segments (each resampled to 128 points). See "
        "docs/adr/0004 for why this trains on ADS-B Exchange historical samples and evaluates "
        "against rules-layer agreement rather than the spec's BTS-incident-based precision.",
        "",
        f"Reconstruction MAE (normalized units): {model['reconstruction_mae']:.4f}",
        "",
        "### Agreement with the rules layer (services/inference/anomaly_rules.py)",
        "",
        f"- {ra['n_rule_flagged']} / {ra['n_test_segments']} test segments flagged by the rules "
        "layer",
        f"- PR-AUC of the learned anomaly score vs. rule-flagged segments: {pr_auc}",
        f"- Precision@k (k = number of rule-flagged segments): {precision_at_k}",
        "",
        f"*{ra['note']}*",
        "",
        f"Artifact: `{report['artifact_path']}`",
        "",
    ]
    return "\n".join(lines)


_RENDERERS = {
    "eta": render_eta_section,
    "trajectory": render_trajectory_section,
    "network": render_network_section,
    "anomaly": render_anomaly_section,
}


def update_report(report: dict, report_path: Path = REPORT_PATH) -> None:
    """Replaces the section for `report['kind']` in docs/ml-report.md
    (creating the file, or the section, if it doesn't exist yet) with freshly
    rendered content, leaving every other model's section untouched.
    """
    kind = report["kind"]
    if kind not in _RENDERERS:
        raise ValueError(f"no report renderer registered for model kind {kind!r}")

    section_body = _RENDERERS[kind](report)
    start_marker = _SECTION_START.format(kind=kind)
    end_marker = _SECTION_END.format(kind=kind)
    section = f"{start_marker}\n{section_body}\n{end_marker}"

    existing = report_path.read_text() if report_path.exists() else _header()

    if start_marker in existing and end_marker in existing:
        pre, _, rest = existing.partition(start_marker)
        _, _, post = rest.partition(end_marker)
        new_content = pre + section + post
    else:
        separator = "" if existing.endswith("\n\n") else "\n"
        new_content = existing + separator + section + "\n"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(new_content)


def _header() -> str:
    return (
        "# ML Report\n\n"
        "Generated by `ml/eval/report.py` from training-run metrics JSON. "
        f"Every number below comes from `ml/eval/`, never hand-entered. "
        f"Last regenerated section timestamp: {dt.datetime.now(dt.UTC).isoformat()}\n\n"
    )


def update_report_from_file(metrics_path: Path, report_path: Path = REPORT_PATH) -> None:
    report = json.loads(metrics_path.read_text())
    update_report(report, report_path)
