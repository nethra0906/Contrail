from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from services.api.routers import (
    aircraft,
    airports,
    anomalies,
    conflicts,
    health,
    models,
    simulations,
)
from services.api.ws.live import router as ws_live_router
from services.common.config import get_settings
from services.common.telemetry import configure_logging, get_logger

logger = get_logger(__name__)


def _warm_simulator_caches() -> None:
    """Builds services/simulator/network_ripple.py's cached BTS-month
    feature tensor and services/simulator/historical.py's reference data up
    front - both are real, one-time work (~15-20s combined, mostly pandas
    aggregation over a 547K-row month) that would otherwise make whichever
    request happens to arrive first at POST /api/v1/simulations pay for it,
    every other request already benefiting from the warm lru_cache.
    """
    from services.simulator.historical import load_simulation_reference_data
    from services.simulator.network_ripple import _cached_checkpoint, _cached_graph_and_arrays

    load_simulation_reference_data()
    _cached_graph_and_arrays()
    _cached_checkpoint()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("api_starting")
    # Fire-and-forget: runs in a worker thread so it never blocks the
    # event loop or delays readiness - the first simulations request
    # during this warmup window still works, just at the unwarmed cost.
    asyncio.create_task(asyncio.to_thread(_warm_simulator_caches))
    yield
    logger.info("api_stopping")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Contrail API",
        description="Real-time airspace digital twin with counterfactual simulation.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(aircraft.router, prefix="/api/v1")
    app.include_router(airports.router, prefix="/api/v1")
    app.include_router(anomalies.router, prefix="/api/v1")
    app.include_router(conflicts.router, prefix="/api/v1")
    app.include_router(models.router, prefix="/api/v1")
    app.include_router(simulations.router, prefix="/api/v1")
    app.include_router(ws_live_router)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
