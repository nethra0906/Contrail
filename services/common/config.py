"""Single source of configuration for every service. All env vars live here.

Nothing outside this module should call `os.environ` directly - that rule keeps
every service configurable the same way and makes `.env.example` the honest
list of everything the system needs to run.
"""

from functools import lru_cache

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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
