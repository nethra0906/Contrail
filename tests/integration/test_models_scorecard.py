"""`/api/v1/models/scorecard` against a real Postgres container - requires
Docker, see tests/integration/conftest.py.
"""

from __future__ import annotations

import datetime as dt

from services.api.routers.models import get_scorecard
from services.common.models.ml import ModelRegistry


async def test_scorecard_returns_only_promoted_models(db_session):
    db_session.add_all(
        [
            ModelRegistry(
                model_version="eta-lgbm-v1",
                kind="eta",
                trained_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                train_window={"months": [[2024, 1]]},
                metrics={"lightgbm": {"overall": {"mae_min": 5.0}}},
                artifact_uri="data/models/eta-lgbm-v1.txt",
                promoted=False,  # superseded - must not appear
            ),
            ModelRegistry(
                model_version="eta-lgbm-v2",
                kind="eta",
                trained_at=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                train_window={"months": [[2024, 1]]},
                metrics={"lightgbm": {"overall": {"mae_min": 2.9}}},
                artifact_uri="data/models/eta-lgbm-v2.txt",
                promoted=True,
            ),
        ]
    )
    await db_session.commit()

    result = await get_scorecard(model=None, session=db_session)

    assert len(result["models"]) == 1
    entry = result["models"][0]
    assert entry["model_version"] == "eta-lgbm-v2"
    assert entry["kind"] == "eta"
    assert entry["metrics"]["lightgbm"]["overall"]["mae_min"] == 2.9


async def test_scorecard_filters_by_model_kind(db_session):
    db_session.add_all(
        [
            ModelRegistry(
                model_version="eta-lgbm-v1",
                kind="eta",
                trained_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                train_window={},
                metrics={},
                artifact_uri="x",
                promoted=True,
            ),
            ModelRegistry(
                model_version="traj-gru-v1",
                kind="trajectory",
                trained_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                train_window={},
                metrics={},
                artifact_uri="y",
                promoted=True,
            ),
        ]
    )
    await db_session.commit()

    result = await get_scorecard(model="trajectory", session=db_session)

    assert len(result["models"]) == 1
    assert result["models"][0]["kind"] == "trajectory"


async def test_scorecard_is_empty_when_nothing_is_promoted(db_session):
    result = await get_scorecard(model=None, session=db_session)
    assert result == {"models": []}
