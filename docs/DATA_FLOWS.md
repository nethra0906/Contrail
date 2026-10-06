# Contrail — End-to-end data flows

Step-by-step traces through the major paths in the system, file and function by file and
function. Every flow here was exercised against the real stack (Docker Compose, real
adsb.lol data, a real browser) during this session, not just read from source.

---

## Flow 1 — A live aircraft position reaches the map

This is the system's core loop, running continuously.

```
adsb.lol (external ADS-B feed)
  ↓  HTTP GET per tile, rate-limited + circuit-broken
services/ingest/sources/adsb_lol.py :: AdsbLolSource.poll_once()
  ↓  raw JSON → StateVectorIn
services/ingest/normalizer.py :: dedupe_latest()
  ↓
services/ingest/main.py :: run()
  ├─→ write_batch()       → UPSERT into Postgres `state_vectors` (icao24, ts natural key)
  └─→ publish_batch()     → Kafka topic `adsb.raw`, keyed by icao24
                               ↓
                        services/assembler/main.py :: handle_message()
                               │
                               ├─ services/assembler/pipeline.py :: advance_track()
                               │    (phase classification, takeoff/landing detection)
                               │
                               ├─ services/assembler/sampling.py :: should_sample()
                               │    if sampled:
                               │      ├─→ sinks/live_fanout.py :: publish_delta()
                               │      │     → Redis PUBLISH on channel `live:cell:{h3_r5}`
                               │      └─→ sinks/parquet.py :: ParquetBuffer.add()
                               │            (buffered, flushed to MinIO periodically)
                               │
                               ├─ services/inference/anomaly_rules.py :: check_all_rules()
                               │    if any anomaly:
                               │      └─→ sinks/anomalies.py :: write_anomaly()
                               │            → INSERT into Postgres `anomalies`
                               │
                               ├─ services/assembler/enrich.py :: estimate_fuel_flow_kg_s()
                               │      └─→ sinks/enriched.py :: publish_enriched()
                               │            → Kafka topic `adsb.enriched`
                               │
                               └─ if takeoff/landing:
                                     └─→ sinks/flights.py :: write_opened_leg() / write_closed_leg()
                                           → INSERT/UPDATE Postgres `flights`

                                                              ↓ (meanwhile, independently)

Browser opens frontend/src/lib/ws-client.ts :: useLiveAircraftFeed(bbox)
  ↓  bboxToH3Cells(bbox) → list of H3 res-5 cell IDs
  ↓  WebSocket → ws://.../ws/live
services/api/ws/live.py :: ws_live()
  ├─→ receives {"op": "subscribe", "h3_cells": [...]}
  ├─→ build_full_frame(cells)  → query Postgres for latest state per aircraft in those
  │                               cells within `live_staleness_seconds`, encode as a binary
  │                               FULL frame, send it immediately
  └─→ pubsub.subscribe(*[live_channel(c) for c in cells])
        → every subsequent Redis PUBLISH on a subscribed channel is forwarded to the
          browser verbatim, as a binary DELTA frame
  ↓
frontend/src/lib/ws-client.ts :: onmessage
  ↓  ws-protocol.ts :: decodeFrame() — FULL replaces the tracked map, DELTA upserts into it
  ↓
frontend/src/components/map/LiveMap.tsx — deck.gl ScatterplotLayer re-renders
```

**Why the browser gets two different data sources (an initial REST-like snapshot query,
then a push stream), not just one:** a brand-new connection needs the *current* state of
every aircraft in view immediately (a single query), but after that, pushing only what
*changed* (deltas) is far cheaper than re-sending every aircraft's full state on every tick.
`build_full_frame`'s query and the delta-publish path use the same binary encoding
(`services/common/ws_protocol.py`'s `encode_frame`), so the frontend's decoder doesn't need
to know which kind of frame produced a given aircraft's data — only `frame_type` in the
header tells it whether to replace or upsert.

**A real bug found and fixed in this exact flow, this session:** `build_full_frame`'s query
filters to rows newer than `live_staleness_seconds`. That constant used to be hardcoded to
30 seconds in two places (`services/api/ws/live.py` and `services/api/routers/aircraft.py`),
while the real configured ingest poll interval is 90 seconds (`INGEST_POLL_INTERVAL_SECONDS`
in `.env`, raised from an original 5s assumption after ADR 0003's rate-limit tuning).
Running the full stack end to end showed this directly: a fresh poll cycle would write data,
then within ~30-60 seconds that same data would fall outside the staleness window and
*both* `/api/v1/aircraft` and `/ws/live`'s full-frame query would return nothing — for
roughly two-thirds of every poll cycle, even with ingest completely healthy. Fixed by
deriving the staleness window from the actual poll interval
(`Settings.live_staleness_seconds = ingest_poll_interval_seconds + 30`) instead of a
constant duplicated in two files that could silently drift from reality — which is exactly
what had happened.

---

## Flow 2 — The REST aircraft-by-bbox query (fallback / initial-load path)

```
GET /api/v1/aircraft?min_lat=...&max_lat=...&min_lon=...&max_lon=...
  ↓
services/api/routers/aircraft.py :: list_aircraft()
  ↓  SELECT DISTINCT ON (icao24) ... WHERE ts > now() - staleness AND lat/lon BETWEEN ...
  ↓  ORDER BY icao24, ts DESC
Postgres `state_vectors`
  ↓  JSON list of aircraft
Frontend (not currently the live map's primary path — see below)
```

This endpoint still exists and is still correct (and now shares the same
`live_staleness_seconds` fix as Flow 1), but the live map's frontend code switched away
from polling it in favor of `/ws/live` (`frontend/src/lib/ws-client.ts`'s own module
docstring: "the Stage 3 replacement for polling `GET /api/v1/aircraft`"). It remains useful
as a simple one-shot query (e.g. for scripting, debugging, or a future non-WebSocket
client) and is exercised directly by `tests/integration/test_ingest_write_path.py`.

---

## Flow 3 — A flight leg's lifecycle

```
Aircraft's on_ground flag flips false → true in consecutive reports
  ↓
services/assembler/track_state.py :: advance()  → TrackState.just_took_off = True
  ↓
services/assembler/main.py :: handle_message()
  ↓  nearest_airport_icao() — bbox-prefiltered haversine lookup, nearest seeded airport
  ↓
services/assembler/pipeline.py :: open_leg_if_takeoff()
  → leg_detect.py :: on_takeoff()  → OpenLeg(flight_id=uuid4(), icao24, actual_dep, origin_icao)
  ↓  held in AssemblerState.open_legs (in-process, per-aircraft)
  ↓
sinks/flights.py :: write_opened_leg()
  → INSERT INTO flights (status="airborne", ...)
  ↓  (also publishes a "takeoff" event to Kafka topic `flights.events`)

  ... aircraft cruises, eventually lands ...

Aircraft's on_ground flag flips true
  ↓
track_state.py :: advance()  → TrackState.just_landed = True
  ↓
pipeline.py :: close_leg_if_landing()
  ├─ no matching OpenLeg, or duration fails is_plausible_leg_duration (2min-8hr bound)?
  │    → (None, rejected=True) — logged, NOT synthesized into a fabricated leg
  └─ otherwise:
       → leg_detect.py :: on_landing()  → ClosedLeg(..., actual_arr, dest_icao)
       ↓
       sinks/flights.py :: write_closed_leg()
         → UPDATE flights SET actual_arr=..., dest_icao=..., status="landed"
         (also publishes a "landing" event to `flights.events`)
```

`flight_id` is a UUID generated client-side at takeoff detection, not a database-assigned
ID — this is what lets the open leg be tracked purely in-process (`AssemblerState.open_legs`)
between the takeoff and landing events without needing a round-trip to the database to learn
its own ID.

---

## Flow 4 — Anomaly detection (wired into the live pipeline this session)

```
Every state vector, as it's processed in handle_message()
  ↓
services/inference/anomaly_rules.py :: check_all_rules(sv, previous_track, current_track)
  ├─ check_emergency_squawk()   — squawk in {7500, 7600, 7700}
  ├─ check_rapid_descent()      — vert_rate ≤ -4000fpm below 10,000ft, airborne
  └─ check_go_around()          — APPROACH→CLIMB phase transition below 3,000ft, climbing
  ↓  zero or more AnomalyEvent, each independently reported (not collapsed into one)
  ↓
services/assembler/sinks/anomalies.py :: write_anomaly()
  → INSERT INTO anomalies (icao24, ts, kind, score, evidence, flight_id?)
  ↓
services/common/telemetry.py :: ANOMALIES_DETECTED_TOTAL.labels(kind=...).inc()
```

Before this session, this entire rules engine was fully built and unit-tested but never
called from anywhere in the running system — genuinely dead code, despite its own docstring
claiming otherwise. It's now live, writing real rows to the `anomalies` table on every
matching state vector. What's still missing (intentionally, out of this session's scope):
no `/api/v1/anomalies` endpoint yet to read these back out, and no publish to the
`anomalies.detected` Kafka topic that's already reserved in `services/common/bus.py`'s
topic registry.

---

## Flow 5 — Training and serving the ETA model

```
make train  (or: python -m ml.train.train_eta)
  ↓
ml/data/loaders/bts.py :: load_months([(2024, 1), ...])
  → downloads BTS On-Time Performance ZIP(s), parses to a DataFrame
  ↓
ml/data/split.py :: chronological_split()
  → train / val / test, split by TIME, never randomly (a project-wide, guard-tested rule)
  ↓
ml/datasets/eta.py :: build_feature_table()
  → services/common/features/eta.py :: compute_eta_features()
    (the SAME function a future live-serving path would call — train/serve parity by
     construction, not by convention)
  ↓
LightGBM training (ml/train/train_eta.py, objective="mae")
  ↓
ml/eval/baselines.py + ml/eval/metrics.py
  → MAE/P90 for the model AND for two honest baselines (predict-zero, departure-carryover)
  ↓
Model artifact written to data/models/eta-lgbm-{timestamp}.txt
  ↓
ml/eval/report.py :: update_report()
  → regenerates docs/ml-report.md's machine-generated section — no hand-typed numbers
  ↓
ml/export/register.py :: register_and_maybe_promote()
  → INSERT a new (unpromoted) model_registry row
  → compare its MAE against the current promoted incumbent for the same `kind`
  → promote (and demote the old incumbent) ONLY if strictly better — a tie or worse
     result is registered for the record but never promoted
       ↓ (if DB unreachable: logged, swallowed, make train still "succeeds" per its DoD)

                                                      ↓ (independently, later)

GET /api/v1/models/scorecard
  ↓
services/api/routers/models.py :: get_scorecard()
  → SELECT * FROM model_registry WHERE promoted = true [AND kind = ?]
  ↓  { "models": [{ kind, model_version, trained_at, train_window, metrics }, ...] }
  ↓
frontend/src/components/scorecard/ScorecardView.tsx
  → React Query fetch, renders per-kind cards (loading / error / empty states all handled)
```

This is "live" in the sense that it always reflects whichever model is currently promoted
— not a cached report, and not continuous real-time scoring (that would need the
`prediction_scores` table's scoring-join job, which depends on a live-serving model, which
doesn't exist yet — see the router's own docstring).

---

## Flow 6 — The Parquet cold-storage archive

```
Every sampled state vector (same sampling.py gate as the live-fanout path)
  ↓
services/assembler/sinks/parquet.py :: ParquetBuffer.add()
  → buffered in memory, per-assembler-process
  ↓  (once should_flush() — a size/time threshold — is true)
services/assembler/main.py :: run()'s main loop
  → asyncio.to_thread(flush_rows, parquet_buffer.take())
  → writes a Parquet file to MinIO (off the event loop — a slow object-store write must
     never stall Kafka offset commits)
```

This is deliberately a *secondary* sink — Postgres/TimescaleDB is the operational store
(with retention policies that eventually drop old raw rows), while Parquet-on-MinIO is the
cheap, long-term, full-resolution archive future ML training jobs will read from, via
`pyarrow`/`pandas`/`polars`, none of which require any conversion step since Parquet is
their native format. M1 and M4 don't read from it yet — this project's own ingest/assembler
pipeline hasn't been running long enough to accumulate the volume a sequence model needs,
so they train on a historical ADS-B Exchange sample instead (see Flow 7 and
[ADR 0004](adr/0004-historical-trajectory-data-source.md)). Once enough real history has
accumulated here, retraining against it instead is the natural next step.

---

## Flow 7 — Training and serving the trajectory model (M1)

```
make train  (ml/train/train_trajectory.py)
  ↓
ml/data/loaders/adsbx_hist.py :: load_default_training_window()
  → downloads (once; cached to data/raw/adsbx_hist/) a CONUS-filtered hour of ADS-B
     Exchange's free historical readsb-hist samples — real position data, not this
     project's own live history (see ADR 0004 for why)
  ↓
ml/datasets/trajectory.py :: build_arrays()
  → splits each aircraft's track into continuous (gap-free) segments
  → for each, slides a 12-point window and computes features via the SAME
     services/common/features/trajectory.py :: compute_trajectory_features() a live
     caller uses — one feature implementation, not two
  → finds each window's real future position at every horizon (60s-900s), masking out
     horizons with no real observation in range rather than fabricating one
  ↓  627,495 real windows
ml/data/split.py :: chronological_split()  (by window anchor time, never random)
  ↓
ml/models/trajectory_gru.py :: TrajectoryGRU  (2-layer GRU + quantile heads)
  → trained with masked pinball loss against {0.1, 0.5, 0.9} quantiles
  ↓
ml/eval/trajectory_baselines.py :: constant_velocity_predict()  (the required baseline)
ml/eval/trajectory_metrics.py  (median/P90 error per horizon, 80% interval coverage,
                                  a 3-quantile CRPS approximation)
  ↓
ONNX export (torch.onnx.export) → data/models/traj-gru-{timestamp}.onnx
  ↓
ml/eval/report.py + ml/export/register.py  (same pattern as every other model)

                                              ↓ (independently, later)

GET /api/v1/aircraft/{icao24}/prediction
  ↓
services/inference/trajectory.py :: predict_trajectory()
  → loads the promoted model via onnxruntime (not torch - see Architecture)
  → queries the LIVE state_vectors table for this aircraft's most recent WINDOW_SIZE
     reports, computes features via the SAME compute_trajectory_features(), runs
     inference
  ↓  per-horizon {q10, q50, q90} position offsets — real uncertainty cones
```

## Flow 8 — Training and serving the delay-propagation model (M3)

```
make train  (ml/train/train_delay_gnn.py)
  ↓
ml/data/loaders/bts.py :: load_month(2024, 1)  (same BTS month M2 trains on)
  ↓
ml/datasets/network.py :: build_graph()
  → ~334 airport nodes; flow edges (scheduled route volume) and rotation edges (the
     same tail number's consecutive legs - this project's historical stand-in for the
     live flights.prev_leg_flight_id field, which isn't populated by the live pipeline)
  ↓
ml/datasets/network.py :: build_arrays()
  → bins every flight into 15-min departure/arrival buckets per airport
  → applies services/common/features/network.py :: compute_network_features() - the
     SAME function a live caller uses
  ↓  (2976 time buckets) x (334 airports) dense feature grid
Chronological split by time-bucket index
  ↓
ml/eval/network_baselines.py
  → historical-mean-by-(airport,hour,dow), and LightGBM-plus-neighbor-delay
     (required baselines - see master spec §7)
  ↓
ml/models/delay_gnn.py :: DelayGNN  (diffusion graph conv + temporal GRU)
  → trained with masked MAE against real future arrival delay
  ↓
ml/eval/report.py + ml/export/register.py

                                              ↓ (independently, later)

GET /api/v1/airports/{icao}/delay-forecast
  ↓
services/inference/network.py :: forecast_delay()
  → loads the promoted model + its trained graph (airports, flow_adj, rotation_adj)
  → queries the LIVE flights table for the most recent SEQUENCE_LENGTH buckets'
     ops-count/cancellation signal per airport (delay signal is real zero here - the
     live pipeline doesn't populate scheduled-time delay fields yet, see the module's
     own docstring)
  ↓  per-horizon-hour predicted arrival delay for the requested airport
```

## Flow 9 — Training the learned anomaly layer (M4)

```
make train  (ml/train/train_autoencoder.py)
  ↓
ml/data/loaders/adsbx_hist.py :: load_default_training_window()  (same historical
                                                                     sample as M1)
  ↓
ml/datasets/anomaly.py :: build_windows()
  → each continuous segment of >=128 native points resampled (linear interpolation)
     to exactly 128 points - a real downsampling, not a padded-up short fragment
  ↓  5134 real segments
ml/models/traj_autoencoder.py :: TrajectoryAutoencoder  (1D conv encoder/decoder)
  → trained to reconstruct normalized segments; MSE loss
  ↓
sklearn.ensemble.IsolationForest  fit on the trained embeddings
  ↓
Evaluation: replay each test segment through services/inference/anomaly_rules.py ::
            check_all_rules() (the SAME rules engine wired into the live pipeline) to
            get a rule-flagged/not label, then check whether the IsolationForest's
            anomaly score agrees (PR-AUC, precision@k) - an honest proxy for "does the
            learned layer agree with rules", not the master spec's BTS-incident-labeled
            precision (see ADR 0004 for why)
  ↓
Embeddings saved as a .npz artifact (not written to trajectory_embeddings - that
  table's flight_id has a real FK to flights, which this historical data has no row in)
  ↓
ml/eval/report.py + ml/export/register.py
```

## Flow 10 — Conflict probability (M5)

```
GET /api/v1/conflicts?bbox=&min_prob=
  ↓
services/api/routers/conflicts.py :: list_conflicts()
  → queries state_vectors for every aircraft currently in the bbox
  ↓
services/inference/conflict.py :: prune_candidate_pairs()
  → H3 k-ring + altitude-band filter - cheap, before any model inference, rules out
     aircraft that are nowhere near each other
  ↓  a short list of candidate (i, j) pairs
For each aircraft appearing in >=1 candidate pair:
  services/inference/trajectory.py :: predict_trajectory()  (Flow 7's live M1 serving)
  ↓  per-aircraft quantile predictions, or skipped if unavailable (not fabricated)
services/inference/conflict.py :: monte_carlo_conflict_probability()
  → samples 200 trajectory pairs per candidate from each aircraft's {q10,q50,q90}
     (treated as a triangular distribution per axis), via a SEEDED Rng derived
     deterministically per pair (Rng.spawn(f"{i}:{j}")) - never Python's global random
  → checks closest-point-of-approach at every shared predicted horizon
  ↓
  { icao24_a, icao24_b, probability } for every pair at or above min_prob
```

---

## Flow 11 — Counterfactual runway-closure simulation (Stage 7, scoped down)

```
POST /api/v1/simulations  { scenario: ScenarioSpec }
  ↓
services/api/routers/simulations.py :: run_simulation()
  → rejects any perturbation type other than exactly one `runway.close` (400, not silent)
  ↓
services/simulator/historical.py :: load_simulation_reference_data()  (cached per process)
  → wraps the cached BTS month (the same one M2/M3 train on) in the `ReferenceData`
     Protocol services/simulator/spec.py already expected - airports/runway idents are
     BTS stations + the single SIMULATED_RUNWAY_IDENT ("SIM"), not live ICAO data
  ↓
services/simulator/spec.py :: validate_against_reference_data()
  → the SAME two-stage ScenarioSpec validator a live scenario would go through -
     unknown airport, horizon spanning outside the BTS month, etc. all rejected here
  ↓
services/simulator/demand.py :: build_runway_demand()
  → every REAL scheduled arrival/departure at this airport on this BTS day, sorted by
     scheduled time - never fabricated demand
  ↓
services/simulator/engine.py :: simulate_runway_closure()
  → drives SimClock + runway.py's request_service() through the SAME demand list twice:
     once unperturbed (baseline), once with the closure applied (scenario) - diffs the two
  ↓                                              ↓ (independently, best-effort)
  per-flight + aggregate delay diff    services/simulator/network_ripple.py :: estimate_ripple()
                                          → loads M3's trained checkpoint (NO retraining),
                                             zeroes the closed airport's ops/delay features
                                             for the affected trailing buckets, and diffs
                                             the GNN's own forward pass against real
                                             unperturbed history - None if no checkpoint
                                             exists or the airport isn't in the graph
  ↓                                              ↓
  { total_added_delay_min, affected_flight_count, top_delayed_flights,
    single_runway_model_already_saturated, network_ripple }
```

Both `load_simulation_reference_data()` and `network_ripple`'s graph/checkpoint caches are
warmed once at API startup (`services/api/main.py`'s lifespan, via a background
`asyncio.to_thread` task) rather than on whichever request happens to arrive first - see
[Architecture](ARCHITECTURE.md)'s Stage 7 section for the real ~114s→~16s cold-path fix
this required.

---

## See also

- [Architecture](ARCHITECTURE.md) — why these paths are shaped this way.
- [Codebase walkthrough](CODEBASE_WALKTHROUGH.md) — the files referenced above, explained
  individually.
- [Learning guide](LEARNING_GUIDE.md) — start here for the full picture.
