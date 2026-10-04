# Contrail — The project, from zero

This document assumes you know nothing about this codebase and walks you through it the
way you'd explain it to someone technically capable but new to the project — simple
language first, technical depth second, always tied back to the actual code. Every claim
here was verified against the real codebase and, where possible, the real running system
(Docker Compose, real live adsb.lol data, a real browser) during the session that wrote
this document — nothing here is aspirational or guessed.

For deeper dives: [Architecture](ARCHITECTURE.md) (tech choices, in depth),
[Codebase walkthrough](CODEBASE_WALKTHROUGH.md) (file by file), [Data flows](DATA_FLOWS.md)
(step-by-step traces), [Interview prep](INTERVIEW_PREP.md), [Running guide](RUNNING_GUIDE.md).

---

## 1. What is this project, in one sentence?

Contrail watches live airplane traffic over the United States and predicts how delayed
flights will be — and it's being built, stage by stage, toward a bigger goal: letting you
rewind to a past moment, simulate "what if a runway had closed two hours earlier," and see
how that ripples through the airport network.

## 2. Why does this project exist?

Air traffic delay is a *network* phenomenon. One runway closure or storm cell, hours away
from you, can cascade into thousands of delayed flights across the country by the end of
the day — the same way one accident on a highway backs up traffic for miles behind it. The
tools that let you actually *see* that cascade, or ask "what if we'd closed that runway two
hours later instead," exist — but they're closed, expensive, and built for airline
operations centers, not for anyone curious enough to ask the question. Contrail's long-term
goal (the "flagship capability" the README opens with) is a version of that tool anyone can
use: fork reality at a past timestamp, inject a disruption, simulate forward, and diff the
counterfactual against what actually happened.

**Where the project actually is today:** that counterfactual sandbox is designed (in
detail — see `docs/CONTRAIL_MASTER_SPEC.md`) but not yet built. What *is* built and
genuinely working is the foundation underneath it: real live data ingestion, a streaming
pipeline, and the first trained ML model (arrival-delay prediction). Section 17 is honest
about exactly where that line is.

## 3. What does a user actually do with it?

Open the app and you land on a live map of the continental US (`/`). Aircraft currently
visible to community ADS-B receivers appear as colored dots — color encodes altitude
(amber near the ground, cyan at cruise), so you can read climb/descent patterns across the
whole map at a glance. Pan and zoom like any map; the data subscription follows your
viewport. Click an aircraft to see its recent track in a side panel.

A second page (`/scorecard`) shows how good the current delay-prediction model actually is
— real accuracy numbers (mean error in minutes) against two honest baselines, not a vague
accuracy claim.

Two more nav items — "Timeline" and "Sandbox" — are visibly present but marked "coming
soon": the time-machine and counterfactual-sandbox features from the big-picture vision
above. They're not broken links; they're honest placeholders for work genuinely not done
yet.

## 4. How does the system work?

### The simple version

Think of it like a newsroom. A **reporter** (`ingest`) constantly calls up an airplane-
tracking data source and files short reports: "plane X is here, going this fast, at this
altitude." Those reports go out over a **newswire** (a message queue called Kafka/Redpanda)
that anyone can subscribe to without the reporter needing to know who's listening. An
**editor** (`assembler`) reads every report off the wire and does three things with each
one: figures out what's actually happening (did a plane just take off? land? is it cruising
normally or descending dangerously fast?), decides what's urgent enough to push to readers
*right now* versus what just goes in the permanent archive, and keeps the official record
(a database) up to date. A **front desk** (`api`) is the only thing allowed to answer
questions from the public — "what's flying over Texas right now?" — and it's also the thing
that pushes live updates out to anyone currently reading. The **newspaper's website**
(the frontend) is what you actually look at.

### The technical version

Four long-running processes, each a separate Docker container:

1. **`ingest`** polls `adsb.lol` (a free, community-run ADS-B aggregator) roughly every 90
   seconds, for 8 major-hub-centered regions (not the whole country at once — the feed's
   real-world rate limits forced that choice, documented in `docs/adr/0003`). Every
   position report gets written to Postgres directly *and* published to a Kafka topic
   (`adsb.raw`).
2. **`assembler`** consumes that Kafka topic. For each report, it runs a small pipeline of
   pure (no I/O) decision functions — classify the flight phase, detect takeoff/landing,
   estimate fuel burn, check anomaly rules — and then does whatever I/O each decision calls
   for: publish a live update to Redis (for the map), buffer a sampled copy for eventual
   Parquet archival, write flight/anomaly rows to Postgres.
3. **`api`** is a FastAPI web server. It answers REST queries (aircraft in a bounding box,
   an aircraft's track history, airport data, the model scorecard) and serves the
   WebSocket live feed (`/ws/live`) that the map actually uses — a compact, hand-rolled
   binary protocol (not JSON) because a human-readable format for 10,000+ aircraft updates
   a second would be wastefully slow to parse in a browser.
4. **The frontend** is a Next.js/React app. It opens a WebSocket connection scoped to your
   current map viewport (using a hexagonal spatial grid called H3, so you only receive
   updates for the geographic area you're actually looking at) and renders the result with
   deck.gl, a GPU-accelerated layer that draws thousands of points in one render call
   instead of thousands of individual DOM elements.

A fifth piece runs on demand, not continuously: `make train` trains the delay-prediction
model from real historical US government flight data (BTS — the Bureau of Transportation
Statistics), evaluates it against honest baselines, and — only if it's actually better than
whatever's currently live — promotes it to be the one the scorecard page reports on.

See [Data flows](DATA_FLOWS.md) for the exact function-by-function trace of each of these
paths, and [Architecture](ARCHITECTURE.md) for *why* each piece of infrastructure
(Postgres+TimescaleDB, Kafka/Redpanda, Redis, MinIO) exists and what it would look like
without it.

## 5. Architecture, at a glance

```
adsb.lol → ingest → Kafka (Redpanda) → assembler → Redis (live) → api → WebSocket → frontend
                        ↓                   ↓                        ↑
                    Postgres          MinIO (Parquet)            REST (api) ← frontend
                  (+TimescaleDB,                                      ↓
                    +pgvector)                                   Postgres
```

Full diagram and the reasoning behind every arrow: [Architecture §2](ARCHITECTURE.md#2-the-same-shape-technically).

## 6. Technologies, briefly (full depth in Architecture)

| Layer | Technology | One-line why |
|---|---|---|
| Backend language | Python 3.11 | Shares code directly with the ML stack (LightGBM, OpenAP); async-native. |
| Web framework | FastAPI | Async, auto-validates requests from type hints, native WebSocket support. |
| Database | PostgreSQL 16 + TimescaleDB + pgvector | One system covers relational, time-series, and vector-similarity needs instead of three. |
| Messaging | Redpanda (Kafka API) | Kafka's durable-replayable-log semantics, without a JVM/ZooKeeper to operate. |
| Live fanout | Redis pub/sub | Cheap, disposable, fast — exactly right for "broadcast to whoever's listening right now." |
| Object storage | MinIO (S3-compatible) | Local, free, and code written against it works unmodified against real S3 later. |
| Frontend framework | Next.js 16 / React 19 / TypeScript (strict) | deck.gl's ecosystem is React-first; strict types catch cross-language protocol drift. |
| Map rendering | MapLibre GL + deck.gl | GPU-batched rendering of thousands of points, not thousands of DOM markers. |
| Spatial indexing | H3 (hexagonal grid) | O(1) "who's subscribed to this update" instead of checking every client's bbox. |
| ML (delay model) | LightGBM | Tabular features, small dataset — gradient-boosted trees beat neural nets here, reliably. |
| Fuel estimation | OpenAP | A published physics model — zero training data needed to be physically grounded. |
| Observability | Prometheus + Grafana, structlog | Pull-based metrics; structured JSON logs are queryable without custom parsing. |
| Orchestration | Docker Compose | Single-host scale; Kubernetes would add real operational weight for no current benefit. |

Full reasoning, including what was considered and rejected for each: [Architecture §3](ARCHITECTURE.md#3-every-major-technology-what-why-and-the-road-not-taken).

## 7. Folder structure

```
services/
  api/          the FastAPI web server (REST + the /ws/live WebSocket)
  ingest/       polls adsb.lol, writes to Postgres + Kafka
  assembler/    consumes Kafka, runs the phase/anomaly/enrichment pipeline
  inference/    the anomaly-detection rules engine (now wired into assembler)
  simulator/    early primitives only (a deterministic event clock, a runway queue model) —
                the counterfactual simulator itself isn't built yet
  common/       shared config, DB/Kafka/Redis clients, schemas, the binary WS protocol,
                feature functions shared between training and serving
ml/             training scripts, evaluation, the model registry/promotion logic
migrations/     Alembic schema migrations (2, after this session added one)
frontend/       the Next.js app
infra/          Docker deploy files, and (as of this session) real Prometheus/Grafana config
docs/           this guide, the architecture/spec docs, ADRs, the auto-generated ML report
scripts/        one-off operational scripts (currently just reference-data seeding)
tests/          unit / integration / golden / e2e(empty) test suites
data/           trained model artifacts, a sample month of raw BTS data (gitignored content)
```

## 8. Important files, briefly (full detail in the walkthrough)

The files worth knowing by name:

- `services/common/config.py` — every environment variable, in one place, validated.
- `services/common/ws_protocol.py` (+ its frontend twin `frontend/src/lib/ws-protocol.ts`)
  — the binary live-feed format. The single most "if this drifts, everything breaks
  silently" file pair in the project.
- `services/assembler/pipeline.py` + `track_state.py` + `leg_detect.py` — the pure decision
  logic that turns a raw position stream into "this is a flight, it took off here, it's
  climbing."
- `ml/train/train_eta.py` — the one genuinely trained, working ML model, end to end.
- `services/api/ws/live.py` — the live map's server-side WebSocket handler.
- `frontend/src/lib/ws-client.ts` — the live map's client-side counterpart.

Every other file, organized by directory, with what depends on what: [Codebase
walkthrough](CODEBASE_WALKTHROUGH.md).

## 9. Important functions and classes, briefly

- `track_state.advance()` — the phase-classification state machine. Pure function:
  `(new report, previous state) -> new state`. No model, deliberately rules-based and
  inspectable, because it produces the ground-truth labels a future trajectory model would
  train against.
- `ws_protocol.encode_frame` / `decode_frame` — pack/unpack a list of aircraft into the
  binary wire format. Mirrored exactly in TypeScript.
- `ml.export.register.register_and_maybe_promote` — the gatekeeper deciding whether a newly
  trained model actually becomes "the one being served." Strictly-better-than-incumbent
  only; ties and regressions are recorded but never promoted.
- `services.common.determinism.Rng` — the seeded-randomness wrapper the (not-yet-built)
  simulator is designed around, so that re-running the same scenario with the same seed
  always produces byte-identical output.

## 10. APIs

All endpoints are unauthenticated today (see [§12](#12-authentication--security) for why,
and what that means).

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Liveness check, no dependencies. |
| GET | `/health/ready` | Confirms Postgres and Redis are actually reachable. |
| GET | `/api/v1/aircraft?min_lat&max_lat&min_lon&max_lon` | Latest position per aircraft inside a bounding box, within the live-staleness window. |
| GET | `/api/v1/aircraft/{icao24}` | One aircraft's latest known state. |
| GET | `/api/v1/aircraft/{icao24}/track?from&to` | Historical position history, time-ranged. |
| GET | `/api/v1/airports` | All seeded CONUS airports. |
| GET | `/api/v1/airports/{icao}` | One airport plus its runways. |
| GET | `/api/v1/models/scorecard?model=` | The currently-promoted model(s) and their real evaluation metrics. |
| GET | `/metrics` | Prometheus scrape target. |
| WS | `/ws/live` | Subscribe (`{"op":"subscribe","h3_cells":[...]}`), receive binary FULL then DELTA frames. |

Every one of these was hit against the real running stack (not just read from source)
during this session's verification pass — including a real WebSocket client decoding real
binary frames of real aircraft over San Antonio.

## 11. Database

PostgreSQL 16, with TimescaleDB (time-series hypertables) and pgvector (similarity search)
extensions. One migration chain, currently at revision `0002`.

**Core relational tables:** `airports`, `runways` (→ airports), `aircraft`, `flights`
(→ airports ×2, self-referential `prev_leg_flight_id` for tracking an aircraft's rotation
through multiple legs — not populated yet, reserved for the delay-propagation model),
`anomalies` (→ flights, now actively written to), `model_registry` (one row per training
run, `promoted` boolean), `snapshots` / `scenarios` / `sim_runs` / `sim_events` (schema
exists for the not-yet-built simulator).

**Hypertables** (TimescaleDB-managed, automatically chunked by time, with compression and
retention policies): `state_vectors` (every position report — the highest-volume table by
far, with two pre-computed rollups, `state_vectors_15s` and `state_vectors_60s`, for cheap
aggregate queries), `weather_obs` (schema exists, nothing writes to it yet — no weather
ingestion is built), `predictions` / `prediction_scores` (schema exists for live model
serving, which doesn't exist yet either).

**Why this matters for understanding the project's actual maturity:** the presence of a
table in the schema is not evidence a feature is built. `weather_obs`, `predictions`,
`sim_events`, and `trajectory_embeddings` all exist in the database today with real columns
and, since this session, real ORM models — and zero rows, because nothing writes to them
yet. That's intentional forward schema design (per the master spec), not a hidden gap, but
it's worth being precise about the difference between "the table exists" and "the feature
works."

## 12. Authentication & security

**Honestly: there is none, right now, and that's currently fine.** Every endpoint is
unauthenticated. There are zero write (POST/PUT/DELETE) endpoints anywhere in the system —
the entire API surface is read-only, and the only thing that writes to the database is the
backend pipeline itself (`ingest`/`assembler`), which never receives untrusted input from
the public internet (its only external input is adsb.lol's response data, which is parsed
through a strict Pydantic schema, not executed or trusted as code).

There *is* a reserved `api_write_key` setting (`services/common/config.py`), for whenever a
write endpoint gets built — it wasn't actually enforced anywhere until this session, which
added a fail-fast check: if the app is configured for a non-development environment and
this key (or `minio_secret_key`) is still at its insecure default value, the app refuses to
start rather than silently running production with a guessable credential. That's the right
amount of security posture for a read-only API with no real write surface yet — building
actual authentication middleware for endpoints that don't exist would be premature.

CORS is configured (`services/api/main.py`) to allow `GET`/`POST` from configured origins
— the `POST` allowance is itself a small tell that write endpoints were anticipated in the
original design, even though none exist yet.

## 13. Error handling

What's actually implemented and tested:

- **Rate limiting + circuit breaking** on the one external dependency that can fail
  (`services/ingest/ratelimit.py`'s `TokenBucket`/`CircuitBreaker`) — a feed that starts
  erroring gets backed off from automatically, rather than hammered. Verified live during
  this session: the real adsb.lol feed returned real `429 Too Many Requests` responses for
  some tiles on the first poll cycle, and the pipeline logged them, retried per its policy,
  and kept the other tiles' data flowing rather than failing the whole cycle.
- **Idempotent writes** everywhere data can be redelivered: `(icao24, ts)` as a natural
  primary key with `ON CONFLICT DO UPDATE`, Kafka offsets committed only after a successful
  downstream write.
- **Graceful degradation on a landing with no matching takeoff** (an aircraft already
  airborne when ingest started, or an implausible gap) — logged, not fabricated into a fake
  flight record.
- **A failed model-promotion never applies** — `register_and_maybe_promote` only promotes a
  strictly-better model; a worse or DB-unreachable training run is recorded (or silently
  skipped, for unreachable) but never breaks what's currently being served.
- **Frontend WebSocket reconnection** with exponential backoff on any drop, and (since this
  session) actual logging on both a connection error and a corrupt/malformed frame, instead
  of silently going stale with no signal to a developer.
- **A render-error boundary** (`frontend/src/app/error.tsx`, added this session) so a
  frontend exception shows a branded recovery screen instead of a blank page.

What's honestly not handled: no API-level rate limiting on `api` itself (nothing stops a
client from hammering `/api/v1/aircraft`), no retry/backoff on the frontend's one-shot REST
calls beyond React Query's default single retry, and no circuit breaker around Postgres/
Redis from the API's side (a database outage would surface as a generic 500, not a graceful
degraded state).

## 14. Performance

What's actually optimized, and why it mattered enough to:

- **A binary WebSocket protocol, not JSON** — 21 bytes per aircraft record versus a JSON
  object's much larger (and parse-cost-heavier) representation, for a feed meant to carry
  thousands of aircraft multiple times a second.
- **H3-cell-scoped subscriptions** — a client only receives updates for the geographic area
  it's actually displaying, and the server-side fanout is an O(1) channel lookup, not an
  O(clients) bbox comparison per update.
- **Adaptive sampling** (`services/assembler/sampling.py`) — full resolution during
  maneuvering phases, thinned to one report per 15 seconds during steady cruise, for the
  live-fanout and Parquet sinks specifically (never for the Kafka log or the direct
  database write, which stay the complete record).
- **TimescaleDB continuous aggregates** (`state_vectors_15s`/`60s`) — common rollup queries
  don't scan raw rows.
- **Off-event-loop Parquet flushes** (`asyncio.to_thread`) — a slow object-store write never
  blocks message consumption or Kafka offset commits.

What hasn't been tuned, honestly, because it hasn't needed to be yet at this traffic volume:
no caching layer in front of the REST endpoints, no connection pooling tuning beyond
SQLAlchemy's defaults, no load testing has been run (`infra/k6/` exists as an empty
directory — Stage 9 "Hardening" work, not started).

## 15. Complete data flows

Six traced end to end, function by function: live position → map, the REST bbox fallback,
a flight's open/close lifecycle, anomaly detection, ETA model training → scorecard, and the
Parquet archive. All in [Data flows](DATA_FLOWS.md) — including the two real bugs found and
fixed by actually running the system end to end during this session (the live-staleness
window mismatch, and the integration-test isolation bug).

## 16. Deployment

**Today: local-only, via Docker Compose.** `docker-compose.yml` defines nine services
(`postgres`, `redis`, `redpanda`, `minio`, `api`, `ingest`, `assembler`, `prometheus`,
`grafana`) with healthchecks gating startup order. The frontend is *not* containerized —
it runs via `npm run dev` / `npm run build && npm start` directly, consistent with a
local-dev-first setup. Full command-by-command instructions, for both Windows and macOS/
Linux: [Running guide](RUNNING_GUIDE.md).

**What a real production deployment would still need** (none of this exists yet): a CI
pipeline stage that builds and pushes Docker images (today's CI only lints and runs unit
tests — see `.github/workflows/ci.yml`), a real deployment target (the master spec's own
plan explicitly scopes this to "a single Docker Compose VPS," not Kubernetes), TLS
termination, and secrets management beyond `.env` files.

## 17. Limitations, honestly

This section is the most important one to read before claiming anything about this project
in an interview — overclaiming what's built is the fastest way to lose credibility once
someone asks a follow-up question.

- **Only one ML model is actually trained and working: M2, arrival-delay regression.**
  Trajectory forecasting (M1) is an explicit, labeled stub. Delay-propagation (M3, a graph
  neural network) and anomaly scoring beyond the rules layer (M4's learned component) don't
  exist at all yet.
- **No live model *serving* exists for any model** — `/api/v1/models/scorecard` reports
  training-time metrics for the currently-promoted model. There is no endpoint that takes a
  flight and returns a live predicted ETA. The `predictions`/`prediction_scores` tables
  exist in the schema; nothing writes to them.
- **The counterfactual simulator — the project's stated flagship feature — doesn't exist
  yet.** Only two low-level primitives are built (a deterministic event clock, a single-
  runway queueing model) plus scenario-spec validation. There's no simulation engine, no
  API surface, no worker process, and no frontend beyond an honest "coming soon" nav item.
  The time-machine/snapshot feature is in the same state.
- **No authentication on any endpoint**, though this is currently low-risk since the API is
  entirely read-only — see §12.
- **Two backend services (`ingest`, `assembler`) define Prometheus metrics but don't
  actually expose them over HTTP** — found and documented (not fixed, as it needs new
  ports and Dockerfile/compose wiring) during this session's infra work. The Grafana
  dashboard panels for those metrics exist and are correctly configured; they'll show real
  data once those two services start a metrics HTTP server.
- **CI doesn't run integration or golden tests**, only unit tests and a lint pass — a
  regression in the Alembic migration chain or the full `make train` pipeline wouldn't be
  caught automatically today.
- **No load testing has been performed** (`infra/k6/` is an empty placeholder), and no
  frontend end-to-end browser test suite exists (`tests/e2e/` is empty).
- **Single-replica assumptions throughout the streaming path** — `AssemblerState`'s
  in-memory per-aircraft state (open legs, known types, last-sampled timestamps) lives in
  one process and would need externalizing (e.g. to Redis) before running more than one
  `assembler` replica, which the code's own comments are explicit about.
- **adsb.lol's real-world rate limits are a genuine, ongoing operational constraint, not
  a solved problem** — confirmed live during this session: several of the 8 configured
  tiles returned `429 Too Many Requests` on a cold start, even at the already-reduced
  request rate from ADR 0003's third tuning pass. The circuit breaker handles this
  gracefully (it doesn't crash anything), but it means real coverage gaps are a normal,
  expected part of running this system against a free community feed, not an edge case.

## 18. Future improvements, realistically

**High priority** (blocks the project's stated flagship capability, or closes a real gap
found this session):
- Build the M1 trajectory model and a live-serving path for it — needed before M3 (delay
  propagation) can be meaningfully built on top, since M3's graph features depend on
  trajectory predictions.
- Build the actual simulation engine (`services/simulator/`) on top of the existing DES
  primitives — this is the single largest piece of work standing between the current state
  and the project's stated flagship feature.
- Expose `/metrics` over HTTP from `ingest` and `assembler` so the Grafana dashboards this
  session built actually show live data for consumer lag, ingest error rate, and Parquet
  flush health — real operational blind spots right now.
- Add `tests/integration` and `tests/golden` to CI — they currently only run locally, which
  is exactly how this session's integration-test isolation bug went undetected.

**Medium priority:**
- A real `/api/v1/anomalies` endpoint and Kafka publish for the now-wired anomaly rules
  layer, so detected anomalies are actually consumable outside the database.
- A registry-backfill job for `aircraft.type_code`, so OpenAP fuel enrichment (built,
  tested, but mostly returning `None` today for lack of a known type) starts producing real
  numbers for most aircraft.
- Basic API rate limiting and a circuit breaker around the database/Redis calls in `api`,
  so a dependency outage degrades gracefully instead of surfacing as a generic 500.
- A frontend end-to-end test suite (Playwright), since the live map and WebSocket
  reconnection logic currently have zero coverage beyond manual/this-session's verification.

**Low priority** (real, but lower-impact or lower-urgency):
- Multi-replica support for `assembler` (externalize its in-process state) — only matters
  once a single replica becomes a throughput bottleneck, which it isn't at current CONUS
  coverage.
- A second ADS-B source (`airplanes.live`, deferred per ADR 0002, or OpenSky as a validation
  source) for better coverage resilience against adsb.lol's rate limits.
- Load testing (`infra/k6/`) once there's a deployment target worth load-testing against.

---

## See also

[Architecture](ARCHITECTURE.md) · [Codebase walkthrough](CODEBASE_WALKTHROUGH.md) ·
[Data flows](DATA_FLOWS.md) · [Interview prep](INTERVIEW_PREP.md) ·
[Running guide](RUNNING_GUIDE.md) · [`docs/CONTRAIL_MASTER_SPEC.md`](CONTRAIL_MASTER_SPEC.md)
(the original full design spec and 10-stage build plan) · [`docs/adr/`](adr/) (real decisions
made during development, with reasoning).
