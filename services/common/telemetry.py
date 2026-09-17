"""Structured logging + Prometheus metrics setup, shared across services.

Kept deliberately small: structlog for JSON logs (so log aggregation doesn't
need a custom parser) and a handful of Prometheus metrics that the stage plan
actually uses (consumer lag, inference latency, sim duration) rather than an
instrument-everything approach that nobody reads.
"""

from __future__ import annotations

import logging
import sys

import structlog
from prometheus_client import Counter, Gauge, Histogram

from services.common.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str):
    return structlog.get_logger(name)


# ---- Metrics used by the ingest -> assembler -> API pipeline ----
INGEST_MESSAGES_TOTAL = Counter(
    "contrail_ingest_messages_total", "Raw state-vector messages received", ["source"]
)
INGEST_ERRORS_TOTAL = Counter(
    "contrail_ingest_errors_total", "Ingest source errors", ["source", "kind"]
)
CONSUMER_LAG = Gauge(
    "contrail_consumer_lag_messages", "Kafka consumer lag in messages", ["topic", "group"]
)
INFERENCE_LATENCY_SECONDS = Histogram(
    "contrail_inference_latency_seconds", "Model inference latency", ["model"]
)
SIM_DURATION_SECONDS = Histogram(
    "contrail_sim_duration_seconds", "Wall-clock duration of a simulation run"
)
SIM_RUNS_TOTAL = Counter(
    "contrail_sim_runs_total", "Simulation runs by terminal status", ["status"]
)
WS_CONNECTED_CLIENTS = Gauge("contrail_ws_connected_clients", "Live WebSocket connections")
