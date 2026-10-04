# Contrail

**Rewind the sky. Change one thing. Watch what happens.**

Contrail is a real-time digital twin of U.S. airspace. It ingests live ADS-B
surveillance data, predicts where aircraft are going with calibrated
uncertainty, models how delay propagates through the airport network, and -
its flagship capability - lets you fork reality at any past timestamp, inject
a disruption (close a runway, drop capacity, inject a weather cell), simulate
forward, and diff the counterfactual world against what actually happened.

> **Status: early build.** Stage 1 (foundation), Stage 2 (live map MVP), Stage
> 3 (streaming backbone, end to end including the frontend's switch to the
> binary `/ws/live` feed), and Stage 4's M2 ETA-regression model (trained on
> real BTS data, served via `/api/v1/models/scorecard`) are implemented and
> tested. See [Build status](#build-status) below for exactly what's real
> today versus what's specified but not yet built. This
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
  MapLibre) with an aircraft detail panel showing track history. Builds,
  typechecks, and lints clean. Switched from the Stage 2 REST-polling path
  to the Stage 3 `/ws/live` binary feed: `frontend/src/lib/ws-client.ts`
  subscribes to the H3 cells covering the current viewport, decodes the
  binary frames, and keeps a live `icao24 -> AircraftState` map, replacing
  the old polling loop entirely.
- Stage 3 streaming backbone, now complete end to end: `services/ingest`
  publishes every normalized state vector to the `adsb.raw` Kafka (Redpanda)
  topic, keyed by `icao24`, alongside its existing idempotent DB upsert;
  `services/assembler` consumes that topic, runs the track-state machine
  (ground/climb/cruise/descent/approach) and leg detector on each aircraft's
  stream, resolves the nearest airport for takeoff/landing events, persists
  opened/closed legs to `flights`, enriches each report with OpenAP-derived
  fuel-flow estimates, and fans live position deltas out over Redis pub/sub,
  one channel per H3 (resolution-5) cell. `services/assembler/sampling.py`
  applies adaptive per-phase sampling to the live fanout and the Parquet
  sink - every report during ground/climb/descent/approach, thinned to one
  every 15s in steady cruise - while the Kafka log and the direct Timescale
  write stay unthinned. `services/assembler/sinks/parquet.py` buffers state
  vectors and periodically flushes them to MinIO as Parquet files, the
  full-resolution archive once `state_vectors`' 6-hour Timescale retention
  rolls off, which Stage 4's training loaders read from. `/ws/live` on the
  API subscribes a client to its viewport's cells, sends a full snapshot
  from `state_vectors` on connect, then forwards deltas as binary frames
  using the wire protocol in `services/common/ws_protocol.py`. Verified
  end-to-end against live ADS-B traffic with Docker up: ingest to Kafka to
  assembler to Redis to a real WebSocket client, consumer lag holding at
  zero across 800+ real messages, and real takeoff events correctly
  resolving their departure airport (e.g. KATL, KORD).
- `scripts/seed_reference_data.py`: pulls real airport + runway data from
  OurAirports, filtered to the CONUS bbox.
- Stage 4's M2 ETA regression (arrival delay) model, trained end to end on
  real BTS TranStats data: a LightGBM model trained on 354,632 rows with a
  chronological train/val/test split (84,840 test rows), beating both the
  zero-delay baseline (8.89 min overall MAE) and the propagate-departure-delay
  baseline (3.41 min overall MAE) with an overall test MAE of **2.92 minutes**
  (P90 absolute error 10.03 min). Full bucketed metrics are in
  [`docs/ml-report.md`](docs/ml-report.md), regenerated from `ml/eval/`
  training-run metrics, never hand-entered. The model is live-served via
  `GET /api/v1/models/scorecard` (`services/api/routers/models.py`), which
  returns the currently-promoted model per kind with its metrics, and
  rendered on the `/scorecard` frontend page.
- 163 passing unit tests (`tests/unit/`, no external dependencies): property-based
  geodesy tests (hypothesis), real normalizer tests against a **recorded live
  API fixture**, a full rejection-test suite for `ScenarioSpec` (every
  validation rule has a test), the WS binary protocol's round-trip property
  test, and the assembler's takeoff/landing/coverage-gap decision logic.
- 6 integration tests (`tests/integration/`, require Docker + testcontainers):
  3 proving the ingest write path is idempotent and the bbox query returns
  only the latest position per aircraft, plus 3 covering the models-scorecard
  endpoint (promoted-only filtering, filtering by model kind, and the
  empty-scorecard case).
- 3 golden determinism tests (`tests/golden/`): the simulator's scenario
  output is byte-identical across repeated runs with the same seed, different
  seeds produce different jitter, and landing order is deterministic and
  matches arrival order.

**Specified, not yet built** (see `docs/CONTRAIL_MASTER_SPEC.md` §9 for the
full 10-stage plan): the remaining ML models - trajectory forecasting (M1)
and the delay-propagation GNN (M3), plus anomaly detection (Stage 4-5), the
timeline/time-machine (Stage 6), and the counterfactual simulation sandbox -
the flagship feature (Stage 7).

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
make test-unit          # 163 tests, no external dependencies, ~1s
make test-integration    # 6 tests, requires Docker; testcontainers spins up real Timescale
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

## Documentation

- [`docs/LEARNING_GUIDE.md`](docs/LEARNING_GUIDE.md) — start here: what this project does,
  why, how it works, and an honest accounting of what's built versus what isn't.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — every major technology choice, with the
  alternatives considered and why.
- [`docs/CODEBASE_WALKTHROUGH.md`](docs/CODEBASE_WALKTHROUGH.md) — file-by-file tour.
- [`docs/DATA_FLOWS.md`](docs/DATA_FLOWS.md) — step-by-step traces of the live map,
  ML training, and flight-lifecycle paths.
- [`docs/INTERVIEW_PREP.md`](docs/INTERVIEW_PREP.md) — how to explain this project out loud.
- [`docs/RUNNING_GUIDE.md`](docs/RUNNING_GUIDE.md) — every setup/run/test/build command,
  verified against a real run of the full stack.
- [`docs/CONTRAIL_MASTER_SPEC.md`](docs/CONTRAIL_MASTER_SPEC.md) — the original full design
  spec and 10-stage build plan.
- [`docs/adr/`](docs/adr/) — Architecture Decision Records for real deviations made during
  development.

## License

MIT for the code in this repository. Third-party data retains its own
license as noted above.
