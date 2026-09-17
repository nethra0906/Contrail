"""Integration test fixtures: a real TimescaleDB container, migrated with
Alembic, torn down after the session. These tests require a working Docker
daemon - they are skipped (not failed) when one isn't reachable, so `make
test-unit` and CI's fast path never depend on Docker being up, while
`make test-integration` gives you the real thing.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False


if not _docker_available():
    pytest.skip("Docker daemon not reachable - skipping integration tests", allow_module_level=True)


@pytest.fixture(scope="session")
def timescale_container():
    with PostgresContainer("timescale/timescaledb-ha:pg16", driver="asyncpg") as container:
        yield container


@pytest_asyncio.fixture
async def db_engine(timescale_container):
    url = timescale_container.get_connection_url()
    engine = create_async_engine(url)

    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS timescaledb")
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")

    # Run the real Alembic migration chain against the container, not a
    # metadata.create_all() shortcut - this is what actually proves the
    # migration works, hypertable conversion included. DATABASE_URL is read
    # by services.common.config.Settings (pydantic-settings), which
    # migrations/env.py uses to build its connection string - passing the
    # full inherited environment plus this override keeps PATH etc. intact
    # for the subprocess. Invoked as `sys.executable -m alembic` rather than
    # a bare "alembic" - on Windows, subprocess with a list of args and no
    # shell can't resolve a PATH-only script name (WinError 2), even though
    # the exact same command works fine typed directly into a shell.
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, cwd="."
    )

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
