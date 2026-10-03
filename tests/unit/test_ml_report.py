"""Unit tests for ml/eval/report.py - docs/ml-report.md generation. Verifies
the "never hand-write a metric" contract structurally: the renderer only
ever reads values out of a metrics dict, and re-running it replaces just the
one model-kind section it owns.
"""

from __future__ import annotations

from pathlib import Path

from ml.eval.report import render_eta_section, update_report

_REPORT = {
    "kind": "eta",
    "model_version": "eta-lgbm-test",
    "trained_at": "2026-01-01T00:00:00+00:00",
    "train_window": {
        "months": [[2024, 1]],
        "train_rows": 100,
        "val_rows": 20,
        "test_rows": 20,
        "train_end": "2024-01-22 00:00:00",
        "val_end": "2024-01-26 00:00:00",
    },
    "baselines": {
        "scheduled": {
            "overall": {"mae_min": 8.9, "p90_min": 20.0, "n": 20},
            "by_duration_bucket": {"<15min": {"mae_min": 5.0, "p90_min": 9.0, "n": 4}},
        },
        "departure_carryover": {
            "overall": {"mae_min": 3.4, "p90_min": 12.0, "n": 20},
            "by_duration_bucket": {},
        },
    },
    "lightgbm": {
        "overall": {"mae_min": 3.1, "p90_min": 9.5, "n": 20},
        "by_duration_bucket": {},
    },
    "artifact_path": "data/models/eta-lgbm-test.txt",
}


def test_render_eta_section_includes_every_metric_value():
    section = render_eta_section(_REPORT)
    assert "8.90" in section
    assert "3.40" in section
    assert "3.10" in section
    assert "eta-lgbm-test" in section
    assert "data/models/eta-lgbm-test.txt" in section


def test_update_report_creates_file_with_section_markers(tmp_path: Path):
    report_path = tmp_path / "ml-report.md"
    update_report(_REPORT, report_path=report_path)

    content = report_path.read_text()
    assert "<!-- ml-report:eta:start -->" in content
    assert "<!-- ml-report:eta:end -->" in content
    assert "eta-lgbm-test" in content


def test_update_report_replaces_only_its_own_section(tmp_path: Path):
    report_path = tmp_path / "ml-report.md"
    report_path.write_text(
        "# ML Report\n\n"
        "<!-- ml-report:m3:start -->\nM3 section, untouched\n<!-- ml-report:m3:end -->\n"
    )

    update_report(_REPORT, report_path=report_path)

    content = report_path.read_text()
    assert "M3 section, untouched" in content
    assert "<!-- ml-report:eta:start -->" in content


def test_update_report_is_idempotent_on_rerun(tmp_path: Path):
    report_path = tmp_path / "ml-report.md"
    update_report(_REPORT, report_path=report_path)
    first = report_path.read_text()

    update_report(_REPORT, report_path=report_path)
    second = report_path.read_text()

    assert first == second
    assert second.count("<!-- ml-report:eta:start -->") == 1
