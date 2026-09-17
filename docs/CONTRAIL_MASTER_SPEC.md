# CONTRAIL - MASTER IMPLEMENTATION SPECIFICATION

> Hand this file to a coding agent with the instruction: **"Build this, stage by stage.
> Do not begin a stage until the previous stage's Definition of Done passes."**

---

## 0. WHAT YOU ARE BUILDING

Contrail is a real-time digital twin of airspace. It ingests live ADS-B surveillance
data, assembles aircraft trajectories, predicts where aircraft will be with calibrated
uncertainty, models how delay propagates through the airport network, and - the flagship
capability - lets a user fork reality at any past timestamp, inject a disruption
(runway closure, capacity reduction, weather cell, ground stop), simulate forward, and
diff the counterfactual world against what actually happened.

Tagline: *Rewind the sky. Change one thing. Watch what happens.*

**Non-negotiable invariants:**

1. The system is runnable end-to-end after every stage.
2. Simulation runs are bit-reproducible from `(snapshot_id, scenario_spec_hash, model_versions, seed)`.
3. Feature computation has exactly ONE implementation, shared by training and serving.
4. Model validation is ALWAYS chronological. Never random splits. No exceptions.
5. No metric is ever written into docs by hand. Metrics come from the evaluation harness only.

---

## 1. TECH STACK (fixed - do not substitute)

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic |
| Streaming | Redpanda (Kafka API), `aiokafka` |
| Storage | PostgreSQL 16 + TimescaleDB + pgvector; Redis 7; MinIO (S3 API) |
| ML | PyTorch, LightGBM, scikit-learn, ONNX Runtime, pandas/polars |
| Domain | `openap` (performance/fuel/emissions), `h3`, `pyproj`, `shapely` |
| Frontend | Next.js 15 (App Router), TypeScript strict, deck.gl, MapLibre GL, TanStack Query, Zustand, Tailwind, Recharts |
| Infra | Docker Compose, GitHub Actions, Prometheus + Grafana, OpenTelemetry |
| Testing | pytest, pytest-asyncio, hypothesis, testcontainers, Vitest, Playwright, k6 |

**Forbidden without a written ADR justifying it:** Kubernetes, Neo4j, MongoDB,
Airflow, a standalone vector database, any ORM other than SQLAlchemy, any state
manager other than Zustand.

---

## 2. DATA SOURCES

| Source | Purpose | Notes |
|---|---|---|
| `api.adsb.lol` / `airplanes.live` | PRIMARY live ADS-B | ODbL, no API key, radius/bbox queries |
| OpenSky Network REST | SECONDARY / validation | OAuth2 client-credentials since 2026-03-18; 4,000 req/day registered. Cross-check only, NOT the firehose. |
| BTS TranStats On-Time Performance | delay ground truth for M3 training | monthly flight-level CSV, 1987–present, free |
| NOAA Aviation Weather Center | METAR / TAF / SIGMET | free REST |
| OurAirports + FAA NASR | airport, runway, airspace geometry | free CSV / shapefile |
| OpenAP | fuel burn, emissions, performance envelopes | pip package |

Every external client MUST implement: token-bucket rate limiting, exponential backoff
with jitter, a per-source circuit breaker, and automatic failover primary → secondary.

**Geographic scope: CONUS only for v1.** Community ADS-B coverage is strong over the
continental US and poor oceanically. Do not attempt global coverage.

---

## 3. REPOSITORY STRUCTURE

```
contrail/
├── docker-compose.yml
├── docker-compose.dev.yml
├── Makefile                    # up, down, seed, train, test, lint, bench
├── README.md
├── .github/workflows/ci.yml
│
├── services/
│   ├── common/                 # SHARED - imported by every service
│   │   ├── config.py           # pydantic-settings, all env vars
│   │   ├── models/             # SQLAlchemy ORM models
│   │   ├── schemas/            # Pydantic DTOs (incl. ScenarioSpec)
│   │   ├── db.py  cache.py  bus.py
│   │   ├── geo.py              # geodesy, ENU frames, H3 helpers
│   │   ├── features/           # THE single feature implementation
│   │   │   ├── trajectory.py   #   used by BOTH training and serving
│   │   │   ├── eta.py
│   │   │   └── network.py
│   │   ├── telemetry.py        # OTel + Prometheus setup
│   │   └── determinism.py      # seeded RNG context manager
│   │
│   ├── ingest/                 # external feeds -> Kafka raw topic
│   │   ├── sources/  adsb_lol.py  opensky.py  noaa.py
│   │   ├── normalizer.py  dedupe.py  ratelimit.py  main.py
│   │
│   ├── assembler/              # raw -> trajectories, flights, enrichment
│   │   ├── track_state.py      # per-aircraft state machine
│   │   ├── leg_detect.py       # takeoff/landing segmentation
│   │   ├── enrich.py           # phase, nearest airport, fuel (OpenAP)
│   │   ├── sinks/  timescale.py  redis_hot.py  parquet.py
│   │   └── main.py
│   │
│   ├── api/                    # FastAPI gateway
│   │   ├── routers/  aircraft.py airports.py anomalies.py
│   │   │              models.py snapshots.py scenarios.py simulations.py
│   │   ├── ws/  live.py  sim.py  protocol.py   # binary frame codec
│   │   ├── deps.py  ratelimit.py  errors.py  main.py
│   │
│   ├── inference/              # ONNX + LightGBM serving workers
│   │   ├── trajectory.py  eta.py  anomaly.py  conflict.py
│   │   ├── registry.py         # model version loading + promotion
│   │   └── main.py
│   │
│   └── simulator/              # *** THE FLAGSHIP ***
│       ├── snapshot.py         # materialize/restore world state
│       ├── spec.py             # ScenarioSpec validation + hashing
│       ├── des/                # discrete-event core
│       │   ├── clock.py  queue.py  runway.py  airspace.py
│       ├── propagate.py        # GNN-driven network delay spread
│       ├── reroute.py          # geometric detour + OpenAP fuel delta
│       ├── metrics.py          # delay-min, pax, fuel, CO2
│       ├── diff.py             # factual vs counterfactual
│       └── worker.py
│
├── ml/
│   ├── data/    loaders/  bts.py  adsb_history.py  weather.py
│   ├── datasets/  trajectory.py  eta.py  network.py
│   ├── models/   trajectory_gru.py  eta_lgbm.py  delay_gnn.py  traj_autoencoder.py
│   ├── train/    train_trajectory.py  train_eta.py  train_delay_gnn.py  train_autoencoder.py
│   ├── eval/     metrics.py  calibration.py  baselines.py  report.py
│   ├── export/   to_onnx.py  register.py
│   └── notebooks/            # EXPLORATION ONLY. Never imported by services.
│
├── frontend/
│   ├── app/  (map)/  (timeline)/  (sandbox)/  (scorecard)/  api/
│   ├── components/  map/  timeline/  scenario/  diff/  cascade/  charts/
│   ├── lib/  ws-client.ts  protocol.ts  store.ts  api-client.ts
│   └── tests/
│
├── infra/    grafana/  prometheus/  k6/  deploy/
├── scripts/  seed_reference_data.py  backfill.py  make_snapshot.py  demo_scenario.py
├── tests/    unit/  integration/  e2e/  golden/   # golden/ = determinism fixtures
└── docs/     architecture.md  adr/  data-sources.md  ml-report.md
             benchmarks.md  api.md  demo-script.md
```

---

## 4. DATABASE SCHEMA

Use Alembic. Enable the `timescaledb` and `vector` extensions in the first migration.

```sql
-- TIME SERIES (hypertables)
state_vectors(icao24 TEXT, ts TIMESTAMPTZ, lat, lon, baro_alt, geo_alt,
              velocity, heading, vert_rate, on_ground BOOL, squawk TEXT,
              h3_r5 TEXT, source TEXT, PRIMARY KEY(icao24, ts));
  -- create_hypertable(chunk_time_interval => 1 hour)
  -- compression after 24h, segmentby icao24, orderby ts DESC
  -- continuous aggregates: state_vectors_15s (30d retention),
  --                        state_vectors_60s (1y retention)
  -- raw retention: 6 hours
  -- BRIN index on ts; btree (icao24, ts DESC); btree (h3_r5, ts DESC)

weather_obs(station TEXT, ts TIMESTAMPTZ, wind_dir, wind_kt, gust_kt,
            vis_sm, ceiling_ft, wx_codes TEXT[], raw TEXT);  -- hypertable

predictions(icao24, issued_at, horizon_s INT, pred_lat, pred_lon, pred_alt,
            sigma_h_km, q10_lat, q10_lon, q90_lat, q90_lon,
            model_version TEXT);  -- hypertable

prediction_scores(icao24, issued_at, horizon_s, error_km, crps,
                  in_80pct BOOL, phase TEXT, model_version TEXT);  -- hypertable

-- RELATIONAL
aircraft(icao24 PK, registration, type_code, operator_icao, wtc, first_seen, last_seen)
airports(icao PK, iata, name, city, country, geom GEOGRAPHY(POINT),
         elevation_ft, tz, hub_rank INT)
runways(id PK, airport_icao FK, ident, true_heading, length_ft, geom GEOGRAPHY)
flights(flight_id PK, icao24, callsign, origin_icao, dest_icao,
        sched_dep, actual_dep, sched_arr, actual_arr, status,
        dep_delay_min, arr_delay_min, diverted BOOL, cancelled BOOL,
        tail_number, prev_leg_flight_id FK)   -- rotation edge
anomalies(id PK, flight_id FK, icao24, ts, kind, score, evidence JSONB)
trajectory_embeddings(flight_id PK, embedding VECTOR(64))  -- ivfflat, cosine

-- SIMULATION
snapshots(snapshot_id PK, ts, kafka_offsets JSONB, blob_uri, created_at, checksum)
scenarios(scenario_id PK, spec JSONB, spec_hash TEXT UNIQUE, label, created_at)
sim_runs(run_id PK, snapshot_id FK, scenario_id FK, seed BIGINT,
         model_versions JSONB, status, started_at, finished_at,
         metrics JSONB, error TEXT, worker_id TEXT, lease_expires_at)
sim_events(run_id FK, t_offset_s, entity_id, event_type, payload JSONB)
model_registry(model_version PK, kind, trained_at, train_window,
               metrics JSONB, artifact_uri, promoted BOOL)
```

Indexes that MUST exist: `flights(status) WHERE status='airborne'` (partial),
`flights(origin_icao, sched_dep)`, `flights(dest_icao, sched_arr)`,
`flights(prev_leg_flight_id)`, `anomalies(ts DESC, score DESC)`,
`sim_runs(status, lease_expires_at)`.

---

## 5. KAFKA TOPICS

| Topic | Key | Retention | Purpose |
|---|---|---|---|
| `adsb.raw` | icao24 | 7d | normalized state vectors - THE replay log |
| `adsb.enriched` | icao24 | 24h | + phase, airport, fuel |
| `flights.events` | flight_id | 30d | takeoff / landing / divert / cancel |
| `anomalies.detected` | flight_id | 30d | anomaly feed |
| `sim.jobs` | run_id | 7d | simulation work queue |
| `sim.frames.<run_id>` | - | 1h | simulation output frames |

Consumers commit offsets manually after a successful sink write. Writes are idempotent
upserts on `(icao24, ts)`. At-least-once delivery + idempotent writes. Do NOT attempt
exactly-once semantics.

---

## 6. API CONTRACT

```
GET  /api/v1/aircraft?bbox=&alt_min=&alt_max=&operator=&limit=
GET  /api/v1/aircraft/{icao24}
GET  /api/v1/aircraft/{icao24}/track?from=&to=&resolution=
GET  /api/v1/aircraft/{icao24}/prediction
GET  /api/v1/aircraft/{icao24}/similar          # pgvector KNN
GET  /api/v1/airports/{icao}/state
GET  /api/v1/airports/{icao}/delay-forecast     # M3 output
GET  /api/v1/anomalies?since=&min_score=&kind=
GET  /api/v1/conflicts?bbox=&min_prob=
GET  /api/v1/models/scorecard?window=24h&model=

POST /api/v1/snapshots            {ts}                              -> 201 {snapshot_id}
POST /api/v1/scenarios            {spec}                            -> 201 {scenario_id, spec_hash}
POST /api/v1/scenarios/compile    {text}                            -> 200 {spec} | 422
POST /api/v1/simulations          {snapshot_id, scenario_id, seed?} -> 202 {run_id}
GET  /api/v1/simulations/{run_id}                                   -> {status, progress, metrics}
GET  /api/v1/simulations/{run_id}/diff                              -> {factual, counterfactual, delta, cascade}
GET  /api/v1/simulations/{run_id}/frames?from_t=&to_t=

WS   /ws/live         C->S {op:"subscribe", h3_cells:[], filters:{}}
                      S->C binary frames @ 2 Hz
WS   /ws/sim/{run_id} S->C simulation frames + progress
```

### Binary WebSocket frame (little-endian)

```
Header:  u8 version | u8 frame_type | u32 timestamp_ms_delta | u16 record_count
Record:  u32 icao24 | i32 lat_e7 | i32 lon_e7 | u16 alt_ft_div8
         | u16 heading_deci | i16 vert_rate_div8 | u16 velocity_kt | u8 flags
```

Full state on subscribe and on reconnect-after-gap > 60 s; deltas otherwise.
The server filters by the client's subscribed H3 cells. Never broadcast the whole world.

### ScenarioSpec (strict JSON Schema - the LLM's only permitted output)

```json
{
  "fork_ts": "2026-08-14T18:40:00Z",
  "horizon_minutes": 360,
  "perturbations": [
    {"type": "runway.close", "airport": "KJFK", "runway": "22L",
     "from": "2026-08-14T18:40:00Z", "duration_minutes": 90},
    {"type": "capacity.scale", "target": "KEWR", "factor": 0.7,
     "duration_minutes": 120},
    {"type": "weather.inject", "polygon": [[-73.9, 40.6]],
     "severity": "severe", "duration_minutes": 180},
    {"type": "ground_stop", "carrier": "UAL", "airport": "KORD",
     "duration_minutes": 60},
    {"type": "flight.cancel", "flight_ids": ["..."]}
  ]
}
```

Validation rejects: unknown airports/runways, non-convex or self-intersecting polygons,
factor outside (0,1], horizon > 720 min, more than 10 perturbations, fork_ts outside
snapshot retention. Validation happens in `simulator/spec.py` - NOT in the LLM layer.

---

## 7. ML SPECIFICATION

### M1 - Trajectory forecasting (probabilistic)

- Input: 12 resampled states over 60 s, in a local ENU frame anchored at t0.
  Features: position deltas, groundspeed, vertical rate, turn rate, phase one-hot,
  bearing-to-destination, wind aloft, aircraft WTC.
- Baseline (MUST implement first): constant-velocity dead reckoning.
- Model: 2-layer GRU (hidden 128) → quantile heads {0.1, 0.5, 0.9} for
  (dE, dN, dAlt) at horizons {60, 180, 300, 600, 900} s. Pinball loss.
- Metrics: median + P90 horizontal error (km) per horizon per phase; CRPS;
  PIT histogram; 80% interval coverage. ALWAYS reported beside the baseline.
- Chronological split: train on days 1..N-14, validate N-14..N-7, test N-7..N.

### M2 - ETA regression

- LightGBM. Features: distance-to-go, groundspeed, headwind component,
  destination arrival-rate over the last 30 min, destination ceiling/visibility,
  hour-of-day, day-of-week, carrier, historic taxi-in P50 for (airport, carrier).
- Baselines: (a) scheduled arrival time, (b) great-circle / groundspeed.
- Metrics: MAE and P90 absolute error in minutes, bucketed by time-to-arrival
  {<15, 15–45, 45–120, >120 min}.

### M3 - Delay-propagation GNN  *** powers the flagship ***

- Graph: ~350 CONUS airport nodes. Edge types: (a) scheduled flow volume,
  (b) aircraft-rotation edges from `flights.prev_leg_flight_id`.
- Node features per 15-min bucket: mean dep delay, mean arr delay, ops count,
  cancellations, ceiling, visibility, wind, hour-of-day, day-of-week, holiday flag.
- Task: predict the delay distribution per airport for t+1h .. t+6h.
- Baselines (MUST implement both and report): (a) historical mean by
  (airport, hour, dow); (b) LightGBM on flat features INCLUDING neighbor delays.
- Model: diffusion graph convolution + temporal encoder (spatio-temporal GCN).
- Train on BTS historical, minimum 3 years. Chronological split by month.
- Metrics: MAE / RMSE per horizon, plus lift over each baseline.
- Explainability: edge importance scores → drives the cascade visualization.

### M4 - Anomaly detection

- Rules layer: squawk in {7500, 7600, 7700}; vertical rate < -4000 fpm below
  10,000 ft; go-around (descent below 3000 ft AGL near a runway then climb);
  holding (>= 2 consecutive 360-degree turns).
- Learned layer: resample track to 128 points → 1D conv autoencoder → 64-d
  embedding → IsolationForest score. Embeddings stored in pgvector.
- Evaluation: positives from BTS `Diverted`/`Cancelled` + squawk events.
  Report precision@k, PR-AUC, and the learned layer's lift over rules alone.

### M5 - Conflict probability

- Prune candidate pairs with an H3 k-ring at r5 + altitude band.
- Monte Carlo (N=200) sample from M1 quantiles → propagate → closest point of
  approach → P(horizontal < 5 NM AND vertical < 1000 ft).
- Deterministic under a fixed seed.

### M6 - Scenario compiler (LLM)

- Structured output against the ScenarioSpec JSON Schema. Temperature 0.
- The LLM output passes through `simulator/spec.py` validation before ANY use.
- Result narration is templated from computed metrics; the LLM only rephrases
  a template that already contains the numbers. It NEVER produces a number.

### Serving & retraining

- M1/M4 export to ONNX; M2/M3 serialized natively. Loaded in-process by
  `services/inference/`. Warm-load on boot, hot-swap on registry promotion.
- Nightly retrain M1/M2; weekly M3. Promotion gate: must beat the incumbent on a
  held-out chronological window. Failed promotions are logged, never silently applied.
- `prediction_scores` is populated by a job that joins predictions against the truth
  that arrives later. This powers the live scorecard.

---

## 8. THE FLAGSHIP: SIMULATION ENGINE

### Snapshot

`POST /snapshots {ts}` materializes: every airborne aircraft's state, every airport's
queue state, active weather, scheduled flights in [ts, ts+12h], and the Kafka offsets
at that instant. Serialized to MinIO with a checksum.

### Engine (hybrid - both halves are required)

1. **Discrete-event layer.** Event heap ordered by `(time, sequence_number)` - the
   sequence number breaks ties deterministically. Runways are servers with service-time
   distributions FITTED FROM HISTORICAL DATA per
   (airport, runway, configuration, wtc-pair) - never assumed or hardcoded.
   Airborne aircraft intersecting a weather polygon take a geometric detour;
   extra distance → extra time and extra fuel via OpenAP.
2. **Learned propagation layer.** At each 15-min tick, the current network state is fed
   to M3, which spreads delay through flow and rotation edges beyond the DES horizon.
   Rotation edges connect an aircraft's delayed arrival to its next leg's departure -
   this is the mechanism that makes cascades realistic.

Neither half works alone. Pure DES cannot capture crew/aircraft rotation effects learned
from data; a pure GNN cannot respond to a runway closure it never saw in training.

### Determinism requirements (test these explicitly)

- A single seeded RNG, threaded through `common/determinism.py`. No module-level `random`.
- No wall-clock reads inside the engine. Time is the simulation clock only.
- All iteration over collections is sorted. No dict/set iteration affects outcomes.
- Float ops in a fixed order; no parallel reduction inside a run.
- Golden test: run the same triple 3 times, assert byte-identical event streams.

### Outputs

`metrics`: total delay minutes, flights affected, estimated passengers affected
(BTS load factors × seat counts), extra fuel kg, extra CO2 tonnes, cancellations.
`cascade`: ordered list of (airport, time, delay_transmitted, source_airport).
`frames`: aircraft positions at 30 s simulated intervals for both branches.

### Job lifecycle

`sim.jobs` topic → worker claims with a lease (`lease_expires_at`) → heartbeats →
publishes progress to `sim.frames.<run_id>` → writes terminal state. Expired leases are
reclaimed. Duplicate claims are prevented by a conditional UPDATE on status. Jobs
exceeding a wall-clock budget are cancelled with a partial-result status.

---

## 9. STAGE PLAN

Each stage lists Deliverables / Tests / Definition of Done.
**The application must run end-to-end at the close of every stage.**

### STAGE 1 - FOUNDATION

- Deliverables: Compose stack (postgres+timescale+pgvector, redpanda, redis, minio,
  prometheus, grafana); Alembic migrations for the full schema; `common/config.py`;
  `common/db.py`, `cache.py`, `bus.py`, `geo.py`, `telemetry.py`, `determinism.py`;
  reference-data seeder (airports, runways from OurAirports); FastAPI skeleton with
  `/health` and `/metrics`; Next.js skeleton; GitHub Actions CI (lint, typecheck, test).
- Tests: migrations up/down clean; `geo.py` property tests with hypothesis
  (great-circle distance symmetry, ENU round-trip within 1 cm); health checks green.
- DoD: `make up` brings up all services healthy; `make test` green; the frontend renders
  an empty map with basemap tiles.

### STAGE 2 - MVP (already a good project on its own)

- Deliverables: ADS-B polling ingester with rate limit + backoff + circuit breaker;
  normalizer; direct write to `state_vectors`; `GET /aircraft` bbox query;
  deck.gl `ScatterplotLayer` with 5 s polling; aircraft detail panel; track history.
- Tests: normalizer unit tests against recorded fixtures; idempotent upsert test
  (insert the same record twice → one row); bbox query integration test with
  testcontainers; Playwright test that aircraft render.
- DoD: open the app, see real aircraft moving over the US, click one, see its track.

### STAGE 3 - STREAMING BACKBONE

- Deliverables: ingest publishes to `adsb.raw`; assembler service (track state machine,
  leg detection, phase classification, OpenAP enrichment); sinks to Timescale + Redis hot
  state + Parquet; `flights.events` topic; binary WS protocol + `/ws/live` with H3
  viewport subscription; frontend switches from polling to WS; Timescale compression +
  continuous aggregates + retention policies; adaptive sampling (high-rate during
  maneuvers, low-rate in cruise).
- Tests: state-machine unit tests (coverage-gap handling, ground/air transitions);
  protocol codec round-trip property test; WS reconnect-after-gap integration test;
  consumer-lag metric exposed; compression policy verified on a populated chunk.
- DoD: 10k+ aircraft stream smoothly at 60 fps; consumer lag stays near zero; storage
  growth matches the documented downsampling policy.

### STAGE 4 - ML FOUNDATION

- Deliverables: BTS historical loader; Parquet training datasets; `common/features/`
  (the single shared implementation); M1 baseline + GRU; M2 baseline + LightGBM;
  evaluation harness producing `docs/ml-report.md` AUTOMATICALLY; ONNX export;
  inference workers; `predictions` + `prediction_scores` + the scoring join job;
  uncertainty cones on the map; `/models/scorecard` page with calibration plots.
- Tests: train/serve feature parity test (compute features both ways on the same input,
  assert equality); chronological-split guard test that FAILS if any split is random;
  ONNX-vs-PyTorch output equivalence within tolerance; metric regression test.
- DoD: `make train` produces a versioned model and a report with real numbers; the live
  scorecard updates as ground truth arrives.

### STAGE 5 - NETWORK INTELLIGENCE

- Deliverables: airport graph builder (flow + rotation edges); M3 baselines + GNN;
  `/airports/{icao}/delay-forecast`; M4 rules + autoencoder + pgvector similarity;
  `anomalies.detected` topic + live anomaly feed UI; M5 conflict probability with H3
  pruning; airport detail page.
- Tests: graph construction unit tests; both M3 baselines implemented and reported;
  anomaly precision@k on labelled positives; conflict-detection determinism under seed;
  H3 pruning recall test (assert no missed conflicts vs brute force on a small set).
- DoD: the delay forecast beats both baselines by a measured, reported margin - or the
  report honestly states that it does not, and why.

### STAGE 6 - TIME MACHINE

- Deliverables: snapshot materialization + restore; historical replay by Kafka offset
  seek; timeline scrubber UI; playback controls (speed, pause, jump);
  `/aircraft/{icao24}/track` at selectable resolution.
- Tests: snapshot round-trip fidelity (restore == original within tolerance); replay
  determinism; scrubber performance at 30-day ranges.
- DoD: scrub to any point in the retention window and watch that day replay.

### STAGE 7 - *** COUNTERFACTUAL SANDBOX ***

- Deliverables: `ScenarioSpec` schema + strict validator; DES core (clock, event heap,
  runway servers with FITTED service-time distributions, airspace); weather detour +
  OpenAP fuel delta; M3 propagation coupling; metrics computation; diff endpoint; sim
  worker with lease-based job claiming; `sim.frames` streaming; split-screen diff map;
  animated cascade graph; scenario builder UI (draw polygon, pick runway, set window).
- Tests: **golden determinism test - same triple run 3x, byte-identical event stream**;
  duplicate-worker-claim test; lease-expiry reclaim test; validator rejection suite
  (every rejection rule has a test); runway queueing sanity test (closing a runway must
  increase delay monotonically); job-timeout partial-result test.
- DoD: pick a real past disruption, close a runway, hit simulate, see a side-by-side diff
  with a cascade animation. Re-running the permalink reproduces identical results.

### STAGE 8 - POLISH

- Deliverables: scenario permalinks (shareable URLs); a library of 5 curated demo
  scenarios; LLM scenario compiler with schema-constrained output + templated narration;
  onboarding tour; responsive layout; loading/empty/error states; 90-second demo video;
  `docs/demo-script.md`.
- Tests: permalink round-trip; LLM compiler output ALWAYS passes or is rejected by the
  validator (fuzz with 100 prompts, assert zero invalid specs reach the engine);
  Playwright end-to-end of the full sandbox flow.
- DoD: a stranger can land on the site and run a counterfactual within 60 seconds.

### STAGE 9 - HARDENING

- Deliverables: k6 load tests (WS fanout at 500 concurrent clients, API p99); query plan
  review + index tuning; Grafana dashboards (ingest lag, consumer lag, inference p99, sim
  duration, error rates); OTel traces across the request path; fault injection (kill a
  source feed, kill a worker mid-sim, fill the disk); `docs/benchmarks.md` generated from
  real runs.
- Tests: chaos tests for each injected fault; assert graceful degradation and automatic
  recovery; no data loss on worker kill (the job is reclaimed).
- DoD: benchmark numbers are measured and documented; killing any single worker does not
  lose data or corrupt a run.

### STAGE 10 - DOCS & DEPLOY

- Deliverables: `docs/architecture.md` with the real diagram; ADRs for every major
  decision INCLUDING the rejections (no Neo4j, no k8s, no Airflow, at-least-once over
  exactly-once, CONUS-only scope); `docs/data-sources.md` with licences and attribution
  (ODbL attribution is REQUIRED for adsb.lol data); `docs/api.md`; README with
  architecture diagram, demo GIF, and quickstart; public deployment.
- DoD: `git clone && make up` works on a clean machine; the public demo is live; the
  README makes a technical reader want to read the code.

---

## 10. SECURITY

- API keys for write endpoints (`/snapshots`, `/scenarios`, `/simulations`); read
  endpoints are public but rate-limited per IP with a token bucket in Redis.
- `ScenarioSpec` is validated against a strict schema BEFORE any engine use.
  Reject: >10 perturbations, horizon >720 min, self-intersecting polygons, polygons with
  >500 vertices, unknown ICAO codes, fork_ts outside retention.
- Simulation jobs have a hard wall-clock budget and a memory cap. A scenario is untrusted
  input - treat resource exhaustion as the primary threat.
- All outbound fetches go to an allowlist of hosts. No user-supplied URLs anywhere.
  An explicit SSRF guard lives in the HTTP client factory.
- Parameterized queries only. Bbox and time-range parameters are bounds-checked before
  reaching SQL.
- WebSocket: per-connection subscription cap, message size cap, idle timeout.
- Secrets from environment only, never committed. `.env.example` documents every var.
- LLM prompt-injection: the compiler's output is structurally constrained and validated;
  it cannot cause an action the schema does not permit.
- ODbL attribution for adsb.lol data must appear in the UI footer and the README.

---

## 11. TESTING STRATEGY

| Layer | Tool | What |
|---|---|---|
| Unit | pytest | geo math, state machines, codecs, feature functions, validators |
| Property | hypothesis | geodesy invariants, protocol round-trips, spec validation |
| Integration | pytest + testcontainers | real Postgres/Timescale, real Redpanda, real Redis |
| **Golden** | pytest | **simulation determinism - byte-identical event streams** |
| Contract | schemathesis | OpenAPI conformance |
| ML | pytest | train/serve feature parity, chronological-split guard, metric regression |
| Frontend | Vitest | store logic, protocol decoding |
| E2E | Playwright | map render, scrubber, full sandbox flow |
| Load | k6 | WS fanout, API p99 under concurrency |
| Chaos | custom | feed death, worker kill, disk pressure |

Two tests are non-negotiable and must exist before Stage 7 is considered done:

1. **Determinism golden test** - the same `(snapshot, spec, seed)` produces identical output.
2. **Train/serve parity test** - features computed for training equal features computed at inference.

---

## 12. DEPLOYMENT

- Single VPS (8 GB RAM minimum) or Fly.io / Railway. Docker Compose in production.
- Caddy or Traefik for TLS termination and reverse proxy.
- Named volumes for Postgres and MinIO; nightly `pg_dump` of relational tables
  (hypertables are reconstructible from the Kafka log and Parquet).
- GitHub Actions: lint → typecheck → unit → integration → build images → deploy.
- Grafana exposed behind auth; Prometheus internal only.
- Cost target: under $20/month. Every data source is free.

---

## 13. EXECUTION RULES FOR THE CODING AGENT

1. Implement stages in order. Do not start a stage until the previous DoD passes.
2. Write the baseline before the advanced model. Always. Report both.
3. Never write a metric by hand. Metrics come from `ml/eval/` only.
4. Never use a random train/test split. Chronological only. There is a guard test.
5. Never duplicate feature logic. `common/features/` is the single source.
6. Never introduce a technology not listed in section 1 without writing an ADR.
7. Never read the wall clock inside the simulation engine.
8. If a stage's scope threatens the "runnable after every stage" invariant, cut scope
   within the stage rather than leaving the system broken.
9. Write the ADR for a decision at the time you make it, not at Stage 10.
10. If ADS-B coverage or a data source proves unworkable, STOP and report it rather than
    substituting synthetic data silently. Synthetic data must always be labelled as such
    in the UI.
