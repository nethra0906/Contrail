# Contrail

**Rewind the sky. Change one thing. Watch what happens.**

Contrail is a real-time digital twin of U.S. airspace. It ingests live ADS-B
surveillance data, predicts where aircraft are going with calibrated
uncertainty, models how delay propagates through the airport network, and -
its flagship capability - lets you fork reality at any past timestamp, inject
a disruption (close a runway, drop capacity, inject a weather cell), simulate
forward, and diff the counterfactual world against what actually happened.

> **Status: early build.** Stage 1 (foundation) and Stage 2 (live map MVP)
> are implemented and tested. See [Build status](#build-status) below for
> exactly what's real today versus what's specified but not yet built. This
> section will be replaced with real screenshots and a demo link once
> further stages land - see `docs/CONTRAIL_MASTER_SPEC.md` for the full plan.

## Why this exists

Air traffic disruption is a network phenomenon - a single runway closure or
weather cell hours away can cascade into thousands of delayed flights across
the country. The tools to *see* that cascade, or to ask "what if we'd closed
that runway two hours later instead," are closed, expensive, and built for
operations centers, not for anyone curious enough to ask the question. See
`docs/CONTRAIL_MASTER_SPEC.md` §0 and §10 for the full problem statement and
an honest comparison against existing tools (Flightradar24, ADS-B Exchange,
the BlueSky ATC simulator, and the published delay-propagation-GNN
literature).

## Architecture

```
Next.js (deck.gl live map) ── REST/WS ──▶ FastAPI gateway
                                                │
                        ┌───────────────────────┼───────────────────────┐
                        ▼                        ▼                       ▼
                 Ingest workers          Inference workers        Simulator workers
              (adsb.lol, hub-tiled)      (trajectory/ETA/GNN)     (DES + GNN hybrid)
                        │                                                │
                        └──────────────┬─────────────────────────────────┘
                                        ▼
                    TimescaleDB (state, flights, predictions)
                    Redis (hot state) · MinIO (snapshots, Parquet)
                    Redpanda/Kafka (event log - Stage 3+)
```

Full architecture, technology justifications, database schema, API contract,
and the ML design (with baselines, metrics, and chronological validation
methodology for every model) are in
[`docs/CONTRAIL_MASTER_SPEC.md`](docs/CONTRAIL_MASTER_SPEC.md) - the document
this build follows stage by stage. Deviations discovered while implementing
it are recorded as ADRs in [`docs/adr/`](docs/adr/).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2 (async), Alembic | async I/O for ingest + WS fanout; one ORM, one migration tool |
| Database | PostgreSQL 16 + TimescaleDB + pgvector | time-series hypertables and vector similarity in one database, not three |
| Streaming | Redpanda (Kafka API) | the event log *is* the replay/time-machine mechanism (Stage 3+) |
| Cache | Redis | hot aircraft state, WS fanout pub/sub |
| Frontend | Next.js 15, TypeScript, deck.gl + MapLibre, TanStack Query, Zustand | GPU-rendered map for 10k+ aircraft; canvas/SVG do not scale to that |
| ML | PyTorch, LightGBM, scikit-learn, OpenAP | trajectory forecasting, ETA regression, delay-propagation GNN, aircraft performance |
| Infra | Docker Compose, GitHub Actions, Prometheus/Grafana | reproducible local stack, CI, observability |

Every inclusion is justified in `docs/CONTRAIL_MASTER_SPEC.md` §1 - including
what was *rejected* (Kubernetes, Neo4j, MongoDB, Airflow) and why.

## Build status

**Real and tested today:**

- Full repository scaffold, shared `services/common` (config, DB, Redis,
  Kafka client factories, geodesy, determinism, telemetry) - all imported by
  every service, none duplicated.
- Complete initial Alembic migration: every table in the spec's schema,
  TimescaleDB hypertables, compression + retention policies, continuous
  aggregates, pgvector extension.
- `services/ingest`: a real, working poller against the live
  [adsb.lol](https://api.adsb.lol) API - hub-centered tiling (see
  [ADR 0003](docs/adr/0003-hub-tiling.md)), token-bucket rate limiting,
  circuit breaker, retry with backoff, idempotent `(icao24, ts)` upsert.
  Verified live against real air traffic during development.
- `services/api`: FastAPI gateway with `/health`, `/health/ready`,
  `/api/v1/aircraft` (bbox query), `/api/v1/aircraft/{icao24}/track`,
  `/api/v1/airports`. Verified via `TestClient` - all routes register and
  respond correctly.
- `frontend`: a real Next.js app - a GPU-rendered live map (deck.gl +
  MapLibre) polling the API, with an aircraft detail panel showing track
  history. Builds, typechecks, and lints clean.
- `scripts/seed_reference_data.py`: pulls real airport + runway data from
  OurAirports, filtered to the CONUS bbox.
- 34 passing unit tests: property-based geodesy tests (hypothesis), real
  normalizer tests against a **recorded live API fixture**, and a full
  rejection-test suite for `ScenarioSpec` (every validation rule has a test).
- 3 integration tests (`tests/integration/`, require Docker + testcontainers)
  proving the ingest write path is idempotent and the bbox query returns only
  the latest position per aircraft.

**Specified, not yet built** (see `docs/CONTRAIL_MASTER_SPEC.md` §9 for the
full 10-stage plan): Kafka-backed streaming + WebSocket live push (Stage 3),
the ML models - trajectory forecasting, ETA, delay-propagation GNN, anomaly
detection (Stage 4–5), the timeline/time-machine (Stage 6), and the
counterfactual simulation sandbox - the flagship feature (Stage 7).

**Known deviations from the original spec**, discovered by actually running
the code against real services rather than assumed: `airplanes.live` is not
an open no-key API as originally assumed
([ADR 0002](docs/adr/0002-airplanes-live-not-open.md)); full-CONUS grid
polling was replaced with hub-centered tiling after empirically measuring
adsb.lol's rate limits ([ADR 0003](docs/adr/0003-hub-tiling.md)).

## Local development

### Prerequisites

- Python 3.11+, [`uv`](https://github.com/astral-sh/uv)
- Node.js 20+
- Docker Desktop (for Postgres/TimescaleDB, Redis, Redpanda, MinIO)

### Setup

```bash
cp .env.example .env
uv venv .venv
uv pip install --python .venv -e ".[dev]"

cd frontend && npm install && cd ..
```

### Run the backend (needs Docker for the database)

```bash
make up          # starts Postgres/Timescale, Redis, Redpanda, MinIO
make migrate      # applies the Alembic migration chain
make seed         # loads real CONUS airport/runway reference data
make api-dev       # FastAPI dev server on :8000
make ingest-dev    # poll live ADS-B traffic into the database
```

### Run the frontend

```bash
cp frontend/.env.local.example frontend/.env.local
make frontend-dev   # Next.js dev server on :3000
```

### Tests

```bash
make test-unit          # 34 tests, no external dependencies, ~1s
make test-integration    # requires Docker; testcontainers spins up real Timescale
```

### Lint / format / typecheck

```bash
make lint
make fmt
cd frontend && npm run lint && npm run typecheck
```

## Data sources & licensing

- Aircraft position data © [adsb.lol](https://adsb.lol) contributors,
  licensed under [ODbL](https://opendatacommons.org/licenses/odbl/).
  Attribution is required and shown in the app footer.
- Airport/runway reference data from
  [OurAirports](https://ourairports.com/data/), public domain.
- Historical delay ground truth (Stage 4+) from the
  [BTS TranStats Airline On-Time Performance](https://www.transtats.bts.gov/DatabaseInfo.asp?DB_ID=120)
  dataset, public domain.
- Weather (Stage 4+) from the
  [NOAA Aviation Weather Center](https://aviationweather.gov/data/api/).

See `docs/adr/0001-conus-only-scope.md` for why coverage is CONUS-only.

## Repository layout

```
services/common/    shared config, DB/Redis/Kafka clients, geodesy, determinism
services/ingest/     live ADS-B polling, rate limiting, normalization
services/api/        FastAPI gateway
services/assembler/  (Stage 3) trajectory assembly, Kafka sinks
services/inference/  (Stage 4+) ML model serving
services/simulator/  (Stage 7) the counterfactual sandbox engine
ml/                  training, evaluation, and export for every model
frontend/            Next.js app
migrations/          Alembic migration chain
scripts/             one-off operational scripts (seeding, snapshots)
tests/               unit / integration / e2e / golden (determinism)
docs/                the master spec, ADRs, and (as stages land) architecture,
                      API, and ML methodology docs
```

## License

MIT for the code in this repository. Third-party data retains its own
license as noted above.
