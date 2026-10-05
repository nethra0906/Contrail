# Contrail — Codebase walkthrough

A guided tour of the repository, starting from each service's entry point and working
outward. Pairs with [Architecture](ARCHITECTURE.md) (the *why*) and [Data flows](
DATA_FLOWS.md) (step-by-step traces). Paths are relative to the repo root.

## How to read this document

Each service gets: its entry point, the modules it depends on (in the order you'd read
them to understand the service), and what's deliberately *not* here yet. Pure-logic
modules (no I/O — no database, network, or filesystem calls) are called out explicitly,
because that's a deliberate, repeated pattern in this codebase: isolate the decision logic
from the I/O, so the logic is unit-testable with plain data and no running infrastructure.

---

## `services/common/` — shared by every service

Nothing in here is specific to ingest, assembler, or api. If two services both need it,
it lives here, once.

- **`config.py`** — the *only* place any service reads an environment variable. One
  Pydantic `Settings` class, loaded from `.env`, cached via `@lru_cache` so `get_settings()`
  is cheap to call repeatedly. Includes a fail-fast validator added during this session's
  hardening pass: if `ENVIRONMENT` isn't `development`/`test` and either `api_write_key` or
  `minio_secret_key` is still at its insecure default, `Settings()` construction raises
  immediately rather than silently running production with guessable credentials. Also
  defines `live_staleness_seconds`, a derived property (poll interval + 30s margin) that
  the REST and WS live-data queries both use — added this session after live testing showed
  a hardcoded 30-second staleness window made the live map go empty for most of every poll
  cycle once the real poll interval grew to 90s.
- **`db.py`** — the async SQLAlchemy engine/sessionmaker factory, and the `Base` class every
  ORM model inherits from. One engine per process (`@lru_cache`), created lazily.
- **`bus.py`** — Kafka (Redpanda) producer/consumer factories, and the single registry of
  topic names (`TOPIC_ADSB_RAW`, `TOPIC_ADSB_ENRICHED`, `TOPIC_FLIGHTS_EVENTS`,
  `TOPIC_ANOMALIES_DETECTED`, `TOPIC_SIM_JOBS`) so a typo never silently creates a stray
  topic. Producers use `acks="all"` and idempotent writes.
- **`cache.py`** — the Redis client factory.
- **`geo.py`** — haversine distance, bearing, ENU (local flat-earth) projection, and
  `latlon_to_h3` (the H3 cell lookup every live-feed subscription and nearest-airport query
  is built on).
- **`ws_protocol.py`** — the binary wire format for `/ws/live` (see
  [Data flows](DATA_FLOWS.md) for the byte layout). Pure encode/decode functions, no I/O.
  Its frontend mirror is `frontend/src/lib/ws-protocol.ts` — the two are cross-checked
  field-by-field and must stay in sync by hand (there's no shared schema file generating
  both sides).
- **`determinism.py`** — `Rng` (a seeded wrapper around `random.Random`, with a `.spawn()`
  method for deterministically-derived child generators) and `spec_hash`. This exists for
  the not-yet-built counterfactual simulator: *"the entire product claim... is that
  (snapshot_id, scenario_spec_hash, model_versions, seed) reproduces byte-identical
  output"* (the module's own docstring). Nothing in the simulator consumes this yet
  (the simulator itself isn't built — see [Limitations](LEARNING_GUIDE.md#17-limitations-honestly)),
  but the rule is already enforced by convention: *"grep for `import random` outside this
  file is a code-review red flag."* Added test coverage this session
  (`tests/unit/test_determinism.py`) since it had none before, despite its own docstring
  calling it "the entire product claim."
- **`telemetry.py`** — `structlog` JSON logging setup, shared by every service, plus every
  Prometheus metric the system defines (deliberately a short, curated list — "a handful of
  metrics the stage plan actually uses... rather than an instrument-everything approach
  that nobody reads"). `ANOMALIES_DETECTED_TOTAL` was added this session when the anomaly
  rules layer was wired into the live pipeline.
- **`models/`** — every SQLAlchemy ORM class, one file per logical group: `aircraft.py`
  (`Aircraft`, `StateVector`), `airports.py` (`Airport`, `Runway`), `anomalies.py`
  (`Anomaly`), `flights.py` (`Flight`), `ml.py` (`ModelRegistry`, `Prediction`,
  `PredictionScore`), `simulation.py` (`ScenarioRow`, `SimRun`, `Snapshot`, and — added
  this session — `SimEvent`), `weather.py` and `trajectory.py` (also added this session,
  for `weather_obs` and `trajectory_embeddings` — see [below](#a-schema-gap-this-session-closed)).
- **`schemas/`** — Pydantic request/response/validation models, separate from the ORM
  models above (`aircraft.py`'s `StateVectorIn`, `scenario.py`'s `ScenarioSpec`).
- **`features/`** — the shared feature-computation functions used identically by training
  and (eventually) live serving: `eta.py`'s `compute_eta_features` (M2, actually used
  today) and `trajectory.py`'s `compute_trajectory_features` (M1, computed but not yet
  consumed by a trained model).

### A schema gap this session closed

Three tables created directly in the Alembic migration (`weather_obs`,
`trajectory_embeddings`, `sim_events`) had no corresponding SQLAlchemy ORM class. Since
`migrations/env.py` uses the ORM models' metadata to detect schema drift for
autogeneration, the next `alembic revision --autogenerate` would have proposed *dropping
all three tables* — nobody had hit this yet because nothing writes to them yet either, but
it was a live trap. Fixed by adding the three missing models and a new migration
(`migrations/versions/0002_sim_events_pk.py`) giving `sim_events` a real primary key (the
original migration created it with an index but no `PRIMARY KEY` at all — SQLAlchemy's ORM
requires one column to map a class).

---

## `services/ingest/` — Stage 2/3: get data in

**Entry point: `main.py`'s `run()`.** An infinite asyncio loop: poll adsb.lol for every
configured tile, normalize what comes back, write it to Postgres *and* publish it to
Kafka, sleep for the remainder of the poll interval, repeat. Runs with no HTTP server at
all — just a long-lived process (`python -m services.ingest.main`, see the `ingest-dev`
Make target).

- **`sources/adsb_lol.py`** — the only ADS-B source actually wired in (see
  [`docs/adr/0002`](adr/0002-airplanes-live-not-open.md): `airplanes.live` was assumed free
  in the original spec but turned out to require an approval email, so it was deferred;
  OpenSky remains a documented-but-unimplemented secondary source).
- **`sources/tiling.py`** — splits the CONUS bounding box into per-hub query tiles.
  `core_hub_tiles()` (8 major hubs) is the current default, down from an original 30-tile
  full-CONUS sweep — see [`docs/adr/0003`](adr/0003-hub-tiling.md) for the three-round
  rate-limit investigation that led here.
- **`ratelimit.py`** — `TokenBucket` (smooth outbound request pacing) and `CircuitBreaker`
  (stop hammering a feed that's clearly down, back off, try again later). Shared by every
  source adapter. Had zero test coverage before this session despite being hand-tuned
  three times per ADR 0003 — `tests/unit/test_ratelimit.py` closes that gap.
- **`normalizer.py`** — turns adsb.lol's raw JSON into the shared `StateVectorIn` schema,
  and `dedupe_latest` (collapses duplicate reports for the same aircraft within one poll
  cycle, keeping the most recent).

**Why the direct database write *and* the Kafka publish, not just one or the other:** see
`main.py`'s own module docstring — the direct write keeps `/api/v1/aircraft` working even
if Kafka has a hiccup, since losing the live map entirely over a broker blip would be worse
than the minor redundancy of writing twice.

---

## `services/assembler/` — Stage 3: turn raw reports into everything else

**Entry point: `main.py`'s `run()`.** Consumes `adsb.raw` from Kafka, and for every message
runs a pipeline of pure decision functions, then performs whatever I/O each decision calls
for. The module docstring is explicit about the design: *"the pieces... decide, kept pure
and unit-tested; this module is just the I/O around them."*

The pure decision layer (no I/O, fully unit-tested, importable and testable with plain
Python values):

- **`track_state.py`** — the per-aircraft phase classifier (`ground` / `climb` / `cruise` /
  `descent` / `approach`), and takeoff/landing transition detection. Rules-based and
  deliberately inspectable (not a model) — see its own docstring on why: it feeds ground-
  truth phase labels the trajectory model (M1) will eventually train and evaluate against,
  so "the classifier must be inspectable, not a black box grading its own homework."
- **`leg_detect.py`** — turns a takeoff/landing transition into an `OpenLeg`/`ClosedLeg`,
  with a sanity bound (`is_plausible_leg_duration`, 2 minutes to 8 hours) rejecting
  landing/takeoff pairs that clearly aren't the same flight.
- **`enrich.py`** — OpenAP-based fuel-flow estimation, only produced when the aircraft's
  type code is known (which is most aircraft not today, since there's no registry-backfill
  job yet — see its docstring: *"never substitute a fabricated default in place of
  genuinely missing input"*).
- **`sampling.py`** — `should_sample()`: every report during a maneuvering phase, thinned to
  one per 15 seconds during steady cruise. Applies only to the live-fanout and Parquet
  sinks, never to the Kafka log or the direct Timescale write, which stay the complete,
  unthinned record.
- **`pipeline.py`** — composes the above into `advance_track`, `open_leg_if_takeoff`,
  `close_leg_if_landing` — the per-message orchestration, still with no I/O (nearest-airport
  resolution, a DB lookup, is explicitly the caller's job).

**The anomaly-detection layer:**

- **`services/inference/anomaly_rules.py`** — M4's rules-based layer (emergency squawk codes,
  rapid uncontrolled-looking descent, go-arounds). Wired into `handle_message()` (right
  after track-state advancement, before the enriched-record publish), persisting detected
  events to the `anomalies` table via `services/assembler/sinks/anomalies.py`, incrementing
  an `ANOMALIES_DETECTED_TOTAL` Prometheus counter, and publishing to the
  `anomalies.detected` Kafka topic (`services/common/bus.py`'s topic registry) so a
  consumer doesn't have to filter the full `adsb.enriched` stream to find them — the
  `GET /api/v1/anomalies` endpoint reads the persisted table, not the topic directly.
  `ml/train/train_autoencoder.py` (M4's learned layer) also replays this exact function
  during evaluation, so "does the live system flag this" and "does the offline evaluation
  flag this" are answered identically.

**`services/inference/`'s three other modules are live-serving, not detection logic** —
each loads a promoted model from `model_registry` (cached by `model_version`, cleared on
a promotion change) and answers one API endpoint's question from real current data:
`trajectory.py` (M1's ONNX session, `/prediction`), `network.py` (M3's PyTorch checkpoint
plus its trained graph, `/delay-forecast`), and `conflict.py` (M5's pure pruning + Monte
Carlo sampling, consumed by `/conflicts` — this one has no model of its own to load, it
consumes M1's live output).

The I/O layer (`sinks/`), one file per external system touched:

- **`live_fanout.py`** — Redis publish, one channel per H3 cell (`live_channel(cell)`).
- **`parquet.py`** — buffers sampled state vectors in memory, flushes to MinIO as Parquet
  once a size/time threshold is hit. Explicitly a *secondary, catch-up-able* sink — a slow
  object-store write runs off the event loop (`asyncio.to_thread`) so it never stalls Kafka
  offset commits.
- **`enriched.py`** — publishes the phase/fuel-enriched record to the `adsb.enriched` Kafka
  topic (a complete, unthinned record — enrichment happens before sampling, not after).
- **`flights.py`** — persists opened/closed flight legs to the `flights` table.
- **`anomalies.py`** — (added this session) persists detected anomaly events.
- **`aircraft_registry.py`** — in-process cache of `icao24 -> type_code` lookups.
- **`nearest_airport.py`** — bbox-prefiltered haversine nearest-airport lookup, used for
  resolving a takeoff/landing's origin/destination airport.

---

## `services/api/` — Stage 2/3: answer questions from the outside

**Entry point: `main.py`.** A FastAPI app factory: CORS setup, mounts every router, exposes
`/metrics` (Prometheus) directly on the app. No lifespan hook runs migrations on startup —
`alembic upgrade head` is a separate, explicit step (`make migrate`), by design, so multiple
API replicas starting simultaneously can never race each other to apply a migration.

- **`routers/health.py`** — `GET /health` (pure liveness, no dependencies checked) and
  `GET /health/ready` (checks Postgres and Redis are actually reachable).
- **`routers/aircraft.py`** — `GET /api/v1/aircraft` (bbox query, `DISTINCT ON (icao24)` for
  latest-per-aircraft, filtered to the `live_staleness_seconds` recency window),
  `GET /api/v1/aircraft/{icao24}` (single aircraft's latest state), and
  `GET /api/v1/aircraft/{icao24}/track` (historical position history, time-ranged).
- **`routers/airports.py`** — `GET /api/v1/airports` and `GET /api/v1/airports/{icao}`,
  serving the seeded reference data, plus `GET /api/v1/airports/{icao}/delay-forecast`
  (M3's live serving, via `services/inference/network.py`).
- **`routers/anomalies.py`** — `GET /api/v1/anomalies`: a thin read over the `anomalies`
  table the now-wired rules layer actually writes to, not a second detection path.
- **`routers/conflicts.py`** — `GET /api/v1/conflicts`: M5's candidate conflict pairs,
  pruned by `services/inference/conflict.py` and scored via Monte Carlo sampling from
  live M1 predictions for every aircraft in the requested bbox.
- **`routers/models.py`** — `GET /api/v1/models/scorecard`: the currently-*promoted* model
  per kind (not every training run ever made — see its own docstring on why a kind with no
  promoted model is simply absent, not backfilled with a stale/synthetic entry).
- **`ws/live.py`** — the `/ws/live` WebSocket endpoint. `build_full_frame()` queries the
  latest state per aircraft within the client's subscribed H3 cells, encodes it as a
  binary frame, then the connection forwards whatever Redis publishes to those cells'
  channels as binary deltas, until the client disconnects (viewport changes mean
  reconnecting with a new subscribe message, not resubscribing mid-stream — see the
  module docstring for the concurrency reason).

Also note `aircraft.py` has one more route beyond the three above:
`GET /api/v1/aircraft/{icao24}/prediction` (M1's live serving, via
`services/inference/trajectory.py`).

**What's conspicuously still not here:** no `snapshots.py` or `scenarios.py`/
`simulations.py` router — the counterfactual sandbox and time-machine features (Stages
6-7 of the master spec) have no API surface yet, matching that none of their backend logic
is built either (see [Limitations](LEARNING_GUIDE.md#17-limitations-honestly)).

---

## `ml/` — training, evaluation, and the model registry

Not a running service — these are scripts, run on demand (`make train`).

- **`data/loaders/bts.py`** — downloads and parses the US DOT's BTS "On-Time Performance"
  monthly data (real government flight-delay data, no API key needed). Feeds M2 and M3.
- **`data/loaders/adsbx_hist.py`** — downloads ADS-B Exchange's free historical
  `readsb-hist` samples (global airborne-traffic snapshots, cached to disk per-snapshot).
  Feeds M1 and M4, which need real continuous position history BTS doesn't have and this
  project's own live ingest hasn't accumulated enough of yet — see
  [ADR 0004](adr/0004-historical-trajectory-data-source.md).
- **`data/split.py`** — `chronological_split`: splits by *time*, never randomly. This is a
  project-wide rule (the master spec's "execution rule 4" — chronological-only ML
  validation) enforced by a dedicated guard test
  (`tests/unit/test_chronological_split.py`), because a model that's trained on data from
  *after* the moment it's meant to predict is cheating in a way that looks like good
  accuracy until it meets real traffic. M3's dataset builder splits by time-bucket index
  directly (a dense, evenly-spaced axis) rather than routing through this DataFrame-based
  helper, but follows the identical never-random rule.
- **`datasets/eta.py`** — turns a raw BTS row into the feature table M2 actually trains on,
  using the *same* `compute_eta_features` function `services/common/features/eta.py`
  exports for live serving — `tests/unit/test_eta_train_serve_parity.py` is the test that
  guards this never silently diverges.
- **`datasets/trajectory.py`** — turns loaded ADS-B Exchange position data into M1's
  12-point training windows: splits each aircraft's stream into continuous (gap-free)
  segments, slides a window across each, and applies `compute_trajectory_features` (the
  same function live serving uses). Finding each window's real future position at every
  horizon uses a precomputed-array-plus-`searchsorted` lookup, not a naive per-horizon
  linear scan — an earlier version of this file measured that scan as the dominant cost
  building 627K windows and was rewritten for it.
- **`datasets/network.py`** — builds M3's airport graph (`build_graph`: flow edges from
  scheduled route volume, rotation edges from the same tail number's consecutive BTS
  legs) and its dense per-bucket node feature grid (`build_arrays`), applying
  `compute_network_features` (`services/common/features/network.py`) per bucket.
- **`datasets/anomaly.py`** — resamples each continuous ADS-B Exchange segment to exactly
  128 points (linear interpolation, only for segments with enough native points to be a
  real downsampling) — the input M4's autoencoder reconstructs.
- **`eval/baselines.py`** — M2's two honest baselines: predict-zero-delay, and
  propagate-the-scheduled/departure-delay-forward.
- **`eval/metrics.py`** — M2's MAE/P90, overall and broken down by scheduled-duration
  bucket. The single source of truth for every M2 metric number — never computed ad hoc
  elsewhere.
- **`eval/trajectory_baselines.py`** — M1's required baseline: constant-velocity dead
  reckoning, projecting the last observed velocity vector linearly forward.
- **`eval/trajectory_metrics.py`** — M1's metrics: median/P90 horizontal error per
  horizon, 80% interval coverage, and a 3-quantile approximation of CRPS and of a PIT
  calibration histogram — honestly labeled as approximations of what a full predictive
  distribution would give, not the exact scores.
- **`eval/network_baselines.py`** — M3's two required baselines: historical mean by
  (airport, hour, day-of-week), and LightGBM on each node's own features plus its
  flow-graph-neighbors' current delay — what the GNN's extra propagation machinery has to
  beat to be worth it.
- **`eval/report.py`** — regenerates `docs/ml-report.md`'s machine-generated sections from
  a metrics JSON, one renderer function per model kind (`render_eta_section`,
  `render_trajectory_section`, `render_network_section`, `render_anomaly_section`). "No
  metric in that report is ever typed by hand" (execution rule 3) is enforced
  structurally: each kind's report has its own marked block
  (`<!-- ml-report:{kind}:start/end -->`) that only that kind's renderer touches.
- **`export/register.py`** — `register_and_maybe_promote`: inserts a new training run into
  `model_registry`, promotes it over the incumbent *only if it beats it* on that kind's
  primary metric (demoting the old one), and never raises past `train()` on a DB failure.
  Generalized to support four different report shapes via `_PRIMARY_METRIC_PATHS` (a path
  per kind — M2 compares LightGBM MAE, M1 compares median km error, M3 compares GNN MAE,
  M4 compares reconstruction MAE, deliberately not the noisy rule-agreement PR-AUC on a
  tiny positive sample). `tests/integration/test_model_registry_promotion.py` covers
  first-promotion, a better/worse/tied challenger, per-kind scoping, and the
  DB-unreachable fallback.
- **`train/train_eta.py`** — M2, real and complete: load BTS data → chronological split →
  shared feature computation → LightGBM → evaluate against baselines → write a versioned
  model artifact to `data/models/` → regenerate the report → register/maybe-promote.
- **`train/train_trajectory.py`** — M1, real and complete: load ADS-B Exchange history →
  build windows → chronological split → train a 2-layer GRU with masked pinball loss
  against {0.1, 0.5, 0.9} quantiles → evaluate against the constant-velocity baseline →
  ONNX-export → report/register. `EPOCHS`/`BATCH_SIZE` are tuned for CPU training at this
  volume (627K windows) with a documented rationale, not arbitrary numbers.
- **`train/train_delay_gnn.py`** — M3, real and complete: load one BTS month → build the
  graph and bucket grid → fit both baselines → train the diffusion-GCN + temporal GRU with
  masked MAE → evaluate per-horizon → report/register. Real result: the GNN beats both
  baselines, including the LightGBM-plus-neighbor-delay one — see `docs/ml-report.md`.
- **`train/train_autoencoder.py`** — M4, real and complete: load ADS-B Exchange history →
  resample to 128-point windows → train the conv autoencoder → fit an `IsolationForest` on
  the embeddings → evaluate score-vs-rules-layer agreement (replaying each test segment
  through the exact same `anomaly_rules.check_all_rules` wired into the live pipeline) →
  report/register. The real result is honestly weak (near-chance PR-AUC on only 6 positive
  test examples) and reported as such — see
  [ADR 0004](adr/0004-historical-trajectory-data-source.md) for why the evaluation
  methodology itself had to deviate from the spec, not just the data source.
- **`models/trajectory_gru.py`** — M1's `TrajectoryGRU` (2-layer GRU + quantile heads) and
  its masked pinball loss function, factored out of the train script (unlike M2's inline
  LightGBM construction) so any future PyTorch-side tooling shares one definition with
  training. Live serving itself (`services/inference/trajectory.py`) runs the exported
  ONNX graph via `onnxruntime`, not this class directly — see the Architecture doc for why.
- **`models/delay_gnn.py`** — M3's `DiffusionGraphConv` (one graph-propagation layer,
  hand-written as a few `torch.einsum` calls rather than a graph-learning library) and
  `DelayGNN` (stacks per-timestep graph convolution with a shared-weight temporal GRU).
  `services/inference/network.py` loads this exact class to run live inference.
- **`models/traj_autoencoder.py`** — M4's `TrajectoryAutoencoder`: 1D-convolutional
  encoder/decoder around a 64-dimensional embedding bottleneck.

---

## `frontend/src/` — the live map and scorecard

**Entry points: `app/layout.tsx`** (root layout: fonts, global CSS, the custom cursor, the
React Query provider, the splash-screen gate) **and `app/page.tsx`** (`/`, the live map).

- **`app/providers.tsx`** — the TanStack Query client. Previously set a global 5-second
  `refetchInterval` default left over from the pre-WS polling era; removed this session
  since nothing currently needs it (the live map gets pushed updates over WebSocket, not
  polling; the scorecard and track-history queries don't need second-by-second refresh).
- **`app/scorecard/page.tsx`** → **`components/scorecard/ScorecardView.tsx`** — fetches
  `GET /api/v1/models/scorecard` via React Query, with real loading/error/empty states
  (the empty state specifically tells you to run `make train` — verified live against a
  freshly-migrated database during this session's end-to-end testing).
- **`components/map/LiveMap.tsx`** — the MapLibre base map plus a deck.gl `ScatterplotLayer`
  overlay. Colors aircraft by altitude (a deliberate visual encoding — warm near the
  ground, cool at cruise — not decoration, per the code's own comment). Keeps the
  subscribed viewport bbox in sync via `map.on("moveend", ...)`.
- **`components/map/AircraftPanel.tsx`** — the selected-aircraft detail panel, fetching
  `GET /api/v1/aircraft/{icao24}/track` for its recent history.
- **`components/map/AnomalyFeed.tsx`** — a collapsible panel on the live map polling
  `GET /api/v1/anomalies` every 15s (deliberately a poll, not a second WebSocket, for an
  event that fires a handful of times an hour) and listing recent rule-flagged anomalies.
- **`app/airports/page.tsx`** — a searchable list of every seeded airport, linking to each
  one's detail page.
- **`app/airports/[icao]/page.tsx`** — one airport's detail: static info (name, runways)
  plus a live call to `GET /api/v1/airports/{icao}/delay-forecast`, with a real empty
  state when no "network" model has been promoted yet or this airport wasn't in the
  training graph.
- **`lib/ws-client.ts`** — `useLiveAircraftFeed(bbox)`: converts the viewport to H3 cells
  (`bboxToH3Cells`, memoized and hardened against degenerate zero-area bboxes), opens the
  WebSocket, decodes frames, maintains the running `icao24 -> AircraftState` map, and
  reconnects with exponential backoff on drop. WebSocket/decode failures log
  (`console.error` for a corrupt frame — a real protocol bug; `console.warn` for a
  routine connection drop that self-heals via reconnect) instead of failing silently.
- **`lib/ws-protocol.ts`** — the binary frame decoder, the frontend mirror of
  `services/common/ws_protocol.py`. Covered by round-trip tests (`ws-protocol.test.ts`).
- **`lib/api-client.ts`** — the typed REST client, one typed function per backend
  endpoint — `listAirports`/`getAirport`/`getDelayForecast`/`getAnomalies` were added
  alongside the Stage 5 endpoints they call. The scorecard types
  (`ScorecardEntry.metrics`) are deliberately a loose `Record<string, unknown>` rather
  than one fixed shape, since each model kind's report genuinely looks different —
  `ScorecardView.tsx` narrows per kind at render time instead of forcing one schema.
- **`lib/store.ts`** — the Zustand store (viewport bbox, selected aircraft).
- **`components/ui/Nav.tsx`** — the top nav. "Timeline" and "Sandbox" are shown as real,
  explicitly-labeled "coming soon" items, not broken or hidden links — honest UI for
  features that genuinely aren't built yet.
- **`app/error.tsx`** — (added this session) an App Router render-error boundary, styled
  with the app's existing design tokens, so a render-time exception (e.g. from the deck.gl
  layer) shows a branded recovery screen instead of Next's generic default.

---

## `tests/`

- **`tests/unit/`** — pure-logic tests, no external services, run via `make test-unit`
  (`pytest tests/unit`). 195 tests, including `test_conflict.py` (M5's candidate pruning
  and Monte Carlo determinism — 13 tests), `test_ratelimit.py`, and `test_determinism.py`.
- **`tests/integration/`** — tests against a real, ephemeral TimescaleDB container
  (`testcontainers`), skipped automatically (not failed) when Docker isn't reachable. An
  earlier pass here found and fixed a real isolation bug: the container is shared across
  every test in the session for speed, but there was no per-test rollback, so data written
  by one test leaked into the next. Fixed in `tests/integration/conftest.py` with a
  per-test SAVEPOINT-based transaction that's rolled back after every test, regardless of
  how many times the test code itself calls `.commit()`.
- **`tests/golden/`** — determinism tests for the (partially-built) simulation primitives —
  byte-identical output across repeated runs with the same seed.
- **`tests/e2e/`** — exists as a directory, currently empty. No end-to-end browser test
  tooling (Playwright/Cypress) is set up yet.

## See also

- [Architecture](ARCHITECTURE.md) — the system-level *why*.
- [Data flows](DATA_FLOWS.md) — step-by-step traces through these files for specific
  user actions.
- [Learning guide](LEARNING_GUIDE.md) — start here for the full picture.
