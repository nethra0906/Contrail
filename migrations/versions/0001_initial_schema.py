"""initial schema: extensions, tables, hypertables, indexes, retention

Revision ID: 0001
Revises:
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- relational tables ----
    op.create_table(
        "airports",
        sa.Column("icao", sa.String(4), primary_key=True),
        sa.Column("iata", sa.String(3)),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("city", sa.String(64)),
        sa.Column("country", sa.String(2)),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("elevation_ft", sa.Float),
        sa.Column("tz", sa.String(64)),
        sa.Column("hub_rank", sa.Integer),
    )

    op.create_table(
        "runways",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("airport_icao", sa.String(4), sa.ForeignKey("airports.icao"), nullable=False),
        sa.Column("ident", sa.String(8), nullable=False),
        sa.Column("true_heading_deg", sa.Float),
        sa.Column("length_ft", sa.Float),
        sa.Column("lat", sa.Float),
        sa.Column("lon", sa.Float),
    )
    op.create_index("ix_runways_airport_icao", "runways", ["airport_icao"])

    op.create_table(
        "aircraft",
        sa.Column("icao24", sa.String(6), primary_key=True),
        sa.Column("registration", sa.String(16)),
        sa.Column("type_code", sa.String(8)),
        sa.Column("operator_icao", sa.String(8)),
        sa.Column("wtc", sa.String(1)),
        sa.Column("first_seen", sa.DateTime(timezone=True)),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "flights",
        sa.Column("flight_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("icao24", sa.String(6)),
        sa.Column("callsign", sa.String(8)),
        sa.Column("origin_icao", sa.String(4), sa.ForeignKey("airports.icao")),
        sa.Column("dest_icao", sa.String(4), sa.ForeignKey("airports.icao")),
        sa.Column("carrier", sa.String(4)),
        sa.Column("tail_number", sa.String(16)),
        sa.Column("sched_dep", sa.DateTime(timezone=True)),
        sa.Column("actual_dep", sa.DateTime(timezone=True)),
        sa.Column("sched_arr", sa.DateTime(timezone=True)),
        sa.Column("actual_arr", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("dep_delay_min", sa.Integer),
        sa.Column("arr_delay_min", sa.Integer),
        sa.Column("diverted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("cancelled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("prev_leg_flight_id", pg.UUID(as_uuid=True), sa.ForeignKey("flights.flight_id")),
    )
    op.create_index("ix_flights_icao24", "flights", ["icao24"])
    op.create_index("ix_flights_tail_number", "flights", ["tail_number"])
    op.create_index("ix_flights_origin_sched_dep", "flights", ["origin_icao", "sched_dep"])
    op.create_index("ix_flights_dest_sched_arr", "flights", ["dest_icao", "sched_arr"])
    op.execute(
        "CREATE INDEX ix_flights_status_airborne ON flights (status) "
        "WHERE status = 'airborne'"
    )

    op.create_table(
        "anomalies",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("flight_id", pg.UUID(as_uuid=True), sa.ForeignKey("flights.flight_id")),
        sa.Column("icao24", sa.String(6), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("evidence", pg.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_anomalies_icao24", "anomalies", ["icao24"])
    op.create_index("ix_anomalies_ts_score", "anomalies", ["ts", "score"])

    op.create_table(
        "trajectory_embeddings",
        sa.Column("flight_id", pg.UUID(as_uuid=True), sa.ForeignKey("flights.flight_id"), primary_key=True),
        sa.Column("embedding", sa.Text),  # created as vector(64) via raw SQL below
    )
    op.execute("ALTER TABLE trajectory_embeddings ALTER COLUMN embedding TYPE vector(64) USING NULL")
    op.execute(
        "CREATE INDEX ix_trajectory_embeddings_ivfflat ON trajectory_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "model_registry",
        sa.Column("model_version", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("trained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("train_window", pg.JSONB, nullable=False),
        sa.Column("metrics", pg.JSONB, nullable=False),
        sa.Column("artifact_uri", sa.String(256), nullable=False),
        sa.Column("promoted", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "snapshots",
        sa.Column("snapshot_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kafka_offsets", pg.JSONB, nullable=False),
        sa.Column("blob_uri", sa.String(256), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_snapshots_ts", "snapshots", ["ts"])

    op.create_table(
        "scenarios",
        sa.Column("scenario_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("spec", pg.JSONB, nullable=False),
        sa.Column("spec_hash", sa.String(32), nullable=False, unique=True),
        sa.Column("label", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scenarios_spec_hash", "scenarios", ["spec_hash"])

    op.create_table(
        "sim_runs",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", pg.UUID(as_uuid=True), sa.ForeignKey("snapshots.snapshot_id"), nullable=False),
        sa.Column("scenario_id", pg.UUID(as_uuid=True), sa.ForeignKey("scenarios.scenario_id"), nullable=False),
        sa.Column("seed", sa.BigInteger, nullable=False),
        sa.Column("model_versions", pg.JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("metrics", pg.JSONB),
        sa.Column("error", sa.String(512)),
        sa.Column("worker_id", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_sim_runs_status_lease", "sim_runs", ["status", "lease_expires_at"])

    # ---- hypertables ----
    op.create_table(
        "state_vectors",
        sa.Column("icao24", sa.String(6), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lon", sa.Float, nullable=False),
        sa.Column("baro_alt_ft", sa.Float),
        sa.Column("geo_alt_ft", sa.Float),
        sa.Column("velocity_kt", sa.Float),
        sa.Column("heading_deg", sa.Float),
        sa.Column("vert_rate_fpm", sa.Float),
        sa.Column("on_ground", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("squawk", sa.String(4)),
        sa.Column("h3_r5", sa.String(16)),
        sa.Column("source", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("icao24", "ts"),
    )
    op.execute("SELECT create_hypertable('state_vectors', 'ts', chunk_time_interval => INTERVAL '1 hour')")
    op.execute("CREATE INDEX ix_state_vectors_icao24_ts ON state_vectors (icao24, ts DESC)")
    op.execute("CREATE INDEX ix_state_vectors_h3_ts ON state_vectors (h3_r5, ts DESC)")
    op.execute(
        "ALTER TABLE state_vectors SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'icao24', timescaledb.compress_orderby = 'ts DESC')"
    )
    op.execute("SELECT add_compression_policy('state_vectors', INTERVAL '24 hours')")
    op.execute("SELECT add_retention_policy('state_vectors', INTERVAL '6 hours')")

    op.execute(
        """
        CREATE MATERIALIZED VIEW state_vectors_15s
        WITH (timescaledb.continuous) AS
        SELECT icao24,
               time_bucket('15 seconds', ts) AS bucket,
               avg(lat) AS lat, avg(lon) AS lon,
               avg(baro_alt_ft) AS baro_alt_ft, avg(velocity_kt) AS velocity_kt,
               last(heading_deg, ts) AS heading_deg, avg(vert_rate_fpm) AS vert_rate_fpm,
               bool_and(on_ground) AS on_ground
        FROM state_vectors
        GROUP BY icao24, bucket
        WITH NO DATA
        """
    )
    op.execute(
        "SELECT add_continuous_aggregate_policy('state_vectors_15s', "
        "start_offset => INTERVAL '2 hours', end_offset => INTERVAL '15 seconds', "
        "schedule_interval => INTERVAL '15 seconds')"
    )
    op.execute("SELECT add_retention_policy('state_vectors_15s', INTERVAL '30 days')")

    op.execute(
        """
        CREATE MATERIALIZED VIEW state_vectors_60s
        WITH (timescaledb.continuous) AS
        SELECT icao24,
               time_bucket('60 seconds', ts) AS bucket,
               avg(lat) AS lat, avg(lon) AS lon,
               avg(baro_alt_ft) AS baro_alt_ft, avg(velocity_kt) AS velocity_kt,
               last(heading_deg, ts) AS heading_deg, avg(vert_rate_fpm) AS vert_rate_fpm,
               bool_and(on_ground) AS on_ground
        FROM state_vectors
        GROUP BY icao24, bucket
        WITH NO DATA
        """
    )
    op.execute(
        "SELECT add_continuous_aggregate_policy('state_vectors_60s', "
        "start_offset => INTERVAL '2 hours', end_offset => INTERVAL '60 seconds', "
        "schedule_interval => INTERVAL '60 seconds')"
    )
    op.execute("SELECT add_retention_policy('state_vectors_60s', INTERVAL '365 days')")

    op.create_table(
        "weather_obs",
        sa.Column("station", sa.String(8), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wind_dir", sa.Float),
        sa.Column("wind_kt", sa.Float),
        sa.Column("gust_kt", sa.Float),
        sa.Column("vis_sm", sa.Float),
        sa.Column("ceiling_ft", sa.Float),
        sa.Column("wx_codes", pg.ARRAY(sa.String)),
        sa.Column("raw", sa.Text),
        sa.PrimaryKeyConstraint("station", "ts"),
    )
    op.execute("SELECT create_hypertable('weather_obs', 'ts', chunk_time_interval => INTERVAL '1 day')")

    op.create_table(
        "predictions",
        sa.Column("icao24", sa.String(6), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_s", sa.Integer, nullable=False),
        sa.Column("pred_lat", sa.Float, nullable=False),
        sa.Column("pred_lon", sa.Float, nullable=False),
        sa.Column("pred_alt_ft", sa.Float),
        sa.Column("sigma_h_km", sa.Float, nullable=False),
        sa.Column("q10_lat", sa.Float),
        sa.Column("q10_lon", sa.Float),
        sa.Column("q90_lat", sa.Float),
        sa.Column("q90_lon", sa.Float),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("icao24", "issued_at", "horizon_s", "model_version"),
    )
    op.execute("SELECT create_hypertable('predictions', 'issued_at', chunk_time_interval => INTERVAL '1 day')")

    op.create_table(
        "prediction_scores",
        sa.Column("icao24", sa.String(6), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon_s", sa.Integer, nullable=False),
        sa.Column("error_km", sa.Float, nullable=False),
        sa.Column("crps", sa.Float),
        sa.Column("in_80pct_interval", sa.Boolean),
        sa.Column("phase", sa.String(16)),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("icao24", "issued_at", "horizon_s", "model_version"),
    )
    op.execute(
        "SELECT create_hypertable('prediction_scores', 'issued_at', chunk_time_interval => INTERVAL '1 day')"
    )

    op.create_table(
        "sim_events",
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("sim_runs.run_id"), nullable=False),
        sa.Column("t_offset_s", sa.Integer, nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
    )
    op.create_index("ix_sim_events_run_id_t", "sim_events", ["run_id", "t_offset_s"])


def downgrade() -> None:
    op.drop_table("sim_events")
    op.drop_table("prediction_scores")
    op.drop_table("predictions")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS state_vectors_60s CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS state_vectors_15s CASCADE")
    op.drop_table("weather_obs")
    op.drop_table("state_vectors")
    op.drop_table("sim_runs")
    op.drop_table("scenarios")
    op.drop_table("snapshots")
    op.drop_table("model_registry")
    op.drop_table("trajectory_embeddings")
    op.drop_table("anomalies")
    op.drop_table("flights")
    op.drop_table("aircraft")
    op.drop_table("runways")
    op.drop_table("airports")
