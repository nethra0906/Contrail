"""`ml/export/register.py`'s promotion decision against a real Postgres
container - requires Docker, see tests/integration/conftest.py. This is
the module whose entire job is "a new model must beat the incumbent on
MAE, and a failed promotion must never silently apply" (master spec §7);
it had no test coverage before this.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ml.export import register as register_module
from services.common.models.ml import ModelRegistry


def _report(version: str, mae: float, kind: str = "eta") -> dict:
    return {
        "kind": kind,
        "model_version": version,
        "trained_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC).isoformat(),
        "train_window": {"months": [[2024, 1]]},
        "baselines": {"scheduled": {"overall": {"mae_min": 9.0}}},
        "lightgbm": {"overall": {"mae_min": mae}},
        "artifact_path": f"data/models/{version}.txt",
    }


@pytest.fixture
def patch_sessionmaker(monkeypatch, db_sessionmaker):
    """Points the module under test at the SAME savepoint-wrapped
    sessionmaker the `db_session` fixture uses (see conftest.py's
    `db_sessionmaker` docstring) - not a fresh one bound to `db_engine` -
    so its real `session.commit()` calls are rolled back with everything
    else at the end of the test instead of leaking into later tests.
    """
    monkeypatch.setattr(register_module, "get_sessionmaker", lambda: db_sessionmaker)
    return db_sessionmaker


async def test_first_model_of_a_kind_is_always_promoted(patch_sessionmaker, db_session):
    promoted = await register_module.register_and_maybe_promote(_report("eta-v1", mae=3.0))
    assert promoted is True

    row = await db_session.get(ModelRegistry, "eta-v1")
    assert row.promoted is True


async def test_a_better_model_is_promoted_and_demotes_the_incumbent(patch_sessionmaker, db_session):
    await register_module.register_and_maybe_promote(_report("eta-v1", mae=3.0))

    promoted = await register_module.register_and_maybe_promote(_report("eta-v2", mae=2.5))
    assert promoted is True

    v1 = await db_session.get(ModelRegistry, "eta-v1")
    v2 = await db_session.get(ModelRegistry, "eta-v2")
    assert v1.promoted is False
    assert v2.promoted is True


async def test_a_worse_model_is_registered_but_not_promoted(patch_sessionmaker, db_session):
    await register_module.register_and_maybe_promote(_report("eta-v1", mae=3.0))

    promoted = await register_module.register_and_maybe_promote(_report("eta-v2", mae=4.0))
    assert promoted is False

    v1 = await db_session.get(ModelRegistry, "eta-v1")
    v2 = await db_session.get(ModelRegistry, "eta-v2")
    # The incumbent must be untouched - a failed challenger never demotes it.
    assert v1.promoted is True
    assert v2.promoted is False


async def test_promotion_is_scoped_per_model_kind(patch_sessionmaker, db_session):
    """A strong `eta` incumbent must not block a brand-new `trajectory`
    model kind from being promoted - promotion compares like against like.
    """
    await register_module.register_and_maybe_promote(_report("eta-v1", mae=1.0))

    promoted = await register_module.register_and_maybe_promote(
        _report("traj-v1", mae=99.0, kind="trajectory")
    )
    assert promoted is True


def test_register_and_maybe_promote_sync_returns_none_when_db_unreachable(monkeypatch):
    """The sync wrapper used by `make train` must swallow a DB-unreachable
    error rather than raising, so training can still succeed without
    Docker up (see the module's own docstring). Points `get_sessionmaker`
    at an engine with no listener on the far end, rather than touching any
    global settings/engine cache shared with other tests.

    Deliberately a plain `def`, not `async def`: `register_and_maybe_promote_sync`
    drives its own event loop via `asyncio.run()` internally (that's its whole
    point - training scripts are synchronous end to end), which raises if
    called from inside a pytest-asyncio-managed running loop instead of
    exercising the DB-unreachable path this test is actually about.
    """
    unreachable = async_sessionmaker(
        create_async_engine("postgresql+asyncpg://nobody:nobody@localhost:1/does_not_exist"),
        expire_on_commit=False,
    )
    monkeypatch.setattr(register_module, "get_sessionmaker", lambda: unreachable)

    result = register_module.register_and_maybe_promote_sync(_report("eta-unreachable", mae=1.0))
    assert result is None


async def test_equal_mae_does_not_promote_the_challenger(patch_sessionmaker, db_session):
    """Strictly-less-than, not less-or-equal: a tie keeps the existing
    incumbent rather than churning promotions on noise-level differences.
    """
    await register_module.register_and_maybe_promote(_report("eta-v1", mae=3.0))

    promoted = await register_module.register_and_maybe_promote(_report("eta-v2", mae=3.0))
    assert promoted is False

    v1 = await db_session.get(ModelRegistry, "eta-v1")
    assert v1.promoted is True
