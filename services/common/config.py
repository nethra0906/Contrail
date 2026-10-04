"""Single source of configuration for every service. All env vars live here.

Nothing outside this module should call `os.environ` directly - that rule keeps
every service configurable the same way and makes `.env.example` the honest
list of everything the system needs to run.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres / TimescaleDB
    database_url: str = "postgresql+asyncpg://contrail:contrail@localhost:5432/contrail"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Redpanda / Kafka
    kafka_bootstrap_servers: str = "localhost:19092"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "contrail"
    minio_secret_key: str = "changeme"
    minio_bucket: str = "contrail"
    minio_secure: bool = False

    # ADS-B sources
    adsb_lol_base_url: str = "https://api.adsb.lol/v2"
    airplanes_live_base_url: str = "https://api.airplanes.live/v2"
    opensky_client_id: str = ""
    opensky_client_secret: str = ""

    # NOAA
    noaa_awc_base_url: str = "https://aviationweather.gov/api/data"

    # Ingest scope - CONUS bounding box (see docs/adr for why CONUS-only)
    ingest_bbox_min_lat: float = 24.5
    ingest_bbox_max_lat: float = 49.5
    ingest_bbox_min_lon: float = -125.0
    ingest_bbox_max_lon: float = -66.5
    ingest_poll_interval_seconds: float = 5.0

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_write_key: str = "dev-only-change-me"
    cors_origins: str = "http://localhost:3000"

    # LLM (optional)
    anthropic_api_key: str = ""

    # Observability
    otel_exporter_otlp_endpoint: str = ""
    log_level: str = "INFO"
    environment: str = "development"

    @model_validator(mode="after")
    def _forbid_insecure_defaults_outside_dev(self) -> "Settings":
        if self.environment not in ("development", "test"):
            if self.api_write_key == "dev-only-change-me":
                raise ValueError(
                    "api_write_key is still the insecure default 'dev-only-change-me' "
                    f"while environment={self.environment!r}; set API_WRITE_KEY to a real "
                    "secret before deploying outside development/test."
                )
            if self.minio_secret_key == "changeme":
                raise ValueError(
                    "minio_secret_key is still the insecure default 'changeme' while "
                    f"environment={self.environment!r}; set MINIO_SECRET_KEY to a real "
                    "secret before deploying outside development/test."
                )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def live_staleness_seconds(self) -> float:
        """How old a `state_vectors` row can be and still count as "live"
        for the REST bbox query (`/api/v1/aircraft`) and the WS feed's
        full-snapshot frame (`/ws/live`).

        Tied to the actual ingest cadence rather than a fixed constant:
        both call sites used to hardcode 30s, inherited from an early
        assumption about how long one poll cycle takes. ADR 0003's
        rate-limit retuning later pushed the real default poll interval to
        90s (see `.env`'s `INGEST_POLL_INTERVAL_SECONDS`), which a fixed
        30s staleness window doesn't know about - the live map would then
        show empty or near-empty for roughly two-thirds of every poll
        cycle even with ingest fully healthy (confirmed by running the
        real stack end to end). The +30s margin absorbs a poll cycle
        itself taking longer than the nominal interval (rate-limit
        retries, a slow upstream response), which is routine in practice.
        """
        return self.ingest_poll_interval_seconds + 30.0

    @property
    def conus_bbox(self) -> tuple[float, float, float, float]:
        """(min_lat, max_lat, min_lon, max_lon)"""
        return (
            self.ingest_bbox_min_lat,
            self.ingest_bbox_max_lat,
            self.ingest_bbox_min_lon,
            self.ingest_bbox_max_lon,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
