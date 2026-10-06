# ADR 0005: Stage 7, scoped down to a synchronous single-runway counterfactual against BTS history

**Status:** Accepted - deliberate, documented reduction from the master spec's Stage 7

## Context

The master spec's Stage 7 ("the counterfactual simulation sandbox", called
out repeatedly as the project's flagship feature) asks for: a lease-based
job-queue worker, `sim.frames` WebSocket streaming, a full multi-runway
airspace model with fitted per-(airport, runway, configuration, wtc-pair)
service-time distributions, weather-detour and OpenAP fuel-delta modeling,
a split-screen diff map, an animated cascade graph, and a scenario-builder
UI - forking from a live Postgres snapshot of this project's own ingested
history.

Building that in full is genuinely weeks of additional work this project
doesn't have time for, and several of its real prerequisites don't exist:
most importantly, there is no live scheduled-flight timetable anywhere in
this project (`services/inference/network.py`'s docstring already
documents this for M3 - the live pipeline only ever observes actual
aircraft movement, never a published schedule). A counterfactual "what if
we close this runway" scenario has nothing real to replay against if it
only forks from the live snapshot the rest of the spec assumes, since
there's no real demand sequence (scheduled arrival/departure times) behind
that snapshot to perturb.

This project already built three of Stage 7's lower-level primitives ahead
of time, standalone and tested: a deterministic event clock
(`services/simulator/des/clock.py`), a pure single-runway queueing state
machine (`services/simulator/des/runway.py`), and two-stage `ScenarioSpec`
validation (`services/common/schemas/scenario.py` +
`services/simulator/spec.py`). None of them assumed a live-DB dependency
- the `ReferenceData` validator is a `Protocol`, specifically so a
different backing data source could be swapped in without changing it.

## Decision

Stage 7 ships as a real, running, honestly-scoped-down counterfactual
simulator:

**Forks from a real historical BTS day, not a live snapshot.** The same
cached January 2024 BTS month M2/M3 already train on
(`services/simulator/historical.py`) is the only real, schedule-bearing
dataset this project has - it has genuine scheduled departure/arrival
times per flight, which a live snapshot does not. `BtsReferenceData`
implements the exact same `ReferenceData` Protocol
`services/simulator/spec.py`'s validator already expected, so the same
two-stage `ScenarioSpec` validation gates a historical scenario exactly as
it would a live one - nothing about that validator changed for this to
work.

**Models exactly one representative runway per airport**
(`SIMULATED_RUNWAY_IDENT = "SIM"`), shared by every arrival and departure,
with a single fixed service duration (1.5 minutes - a commonly cited rough
average runway occupancy time, stated as a simplifying assumption, not
fitted from this project's own data) rather than the spec's fitted
per-(airport, runway, configuration, wtc-pair) distribution. This is a
real reduction: a large hub's true multi-runway capacity isn't modeled.
The consequence is visible, not hidden - the API returns
`baseline_mean_wait_min`/`single_runway_model_already_saturated` alongside
every result, so a hub like ATL (which this one-runway model genuinely
cannot represent - its real demand needs ~5 runways, and the single-runway
model shows a baseline mean wait of ~745 minutes even with *no*
perturbation applied) is flagged honestly rather than silently producing
an implausible-looking diff. Small/mid airports (e.g. BOI, PWM) show a
near-zero baseline, which is the regime this scoped model is actually
accurate for.

**Only `runway.close` perturbations are executed.** `ScenarioSpec`'s other
four perturbation types (`capacity.scale`, `weather.inject`,
`ground_stop`, `flight.cancel`) still validate structurally - they're part
of the same Pydantic model every scenario goes through - but
`POST /api/v1/simulations` rejects them explicitly with a clear 400 rather
than silently accepting and ignoring them. Weather/fuel modeling in
particular needs the OpenAP integration and a weather-ingestion pipeline
neither of which exist (see ADR 0004's M3 weather-feature note); ground
stops and flight cancellations are real, smaller follow-up work, not
implemented here to keep this addition bounded.

**Synchronous request/response, not a job queue + worker + WebSocket
stream.** A single day's single-runway DES run against real BTS demand
(a few hundred to ~1,700 flights) completes in well under a second once
the process's caches are warm - there's no genuine need for async job
semantics at this scale. The "re-running the permalink reproduces
identical results" part of the master spec's DoD is satisfied by the
engine's determinism (same inputs always produce the same output,
verified in `tests/unit/test_simulator_engine.py`), not by persisting a
job record.

**M3 propagation coupling, reusing the trained checkpoint with zero
retraining** (`services/simulator/network_ripple.py`). `DelayGNN.forward()`
conditions on `SEQUENCE_LENGTH` trailing 15-minute buckets of real history
to forecast delay 1-6 hours ahead; a scenario's closure starts exactly at
the end of that trailing window, so the model has never seen the
closure's effect by the time it forecasts. What this module actually
reports is a stated, bounded what-if: "if this airport's most recent
throughput had already been reduced to zero by a closure like this one,
how much higher does the model's own forecast come out at its
flow-connected neighbors, compared to real unperturbed history?" - a real
reuse of the trained model's learned propagation structure, not a claim
about what will literally happen next. Best-effort throughout: no trained
checkpoint on disk, an airport outside the training graph, or
insufficient trailing history this early in the month all return `None`
rather than a fabricated number, the same "404, not a stub" convention
`services/inference/network.py` already established.

**No new frontend map/animation work.** `/sandbox`
(`frontend/src/app/sandbox/page.tsx`) is a form (airport, date, closure
start/duration) plus a results panel (aggregate delay, top delayed
flights, the M3 ripple, and the saturation caveat) - not the spec's
split-screen diff map or animated cascade graph. The data it needs to
render those doesn't change; building the visualization is real, separate,
bounded follow-up work, cut here to ship the actual simulation engine
first.

## Consequences

- A real counterfactual now runs end to end against real historical data,
  with a real (if simplified) queueing model and a real (if reframed)
  coupling to the already-trained delay-propagation GNN - genuinely
  meaningful, not a stub, and verifiable: see
  `tests/unit/test_simulator_engine.py` (the master-spec-named "closing a
  runway must increase delay monotonically" property, re-verified at the
  full-engine level, plus a byte-for-byte determinism test) and
  `tests/unit/test_network_ripple.py`.
- The simulator identifies airports by BTS/IATA code (e.g. "ATL", "BOI"),
  not the ICAO codes the rest of this app's live surface uses - there is
  no real IATA/ICAO crosswalk wired for this feature. A future version
  that lets a user pick from the live-seeded `airports` table (which does
  have a real `iata` column, `services/common/models/airports.py`) and
  joins through to the right BTS station is a realistic, bounded
  follow-up, not required for this feature to be real today.
- Large hubs (ATL, ORD, DFW, ...) will show an already-saturated baseline
  under this one-runway model - a known, surfaced-in-the-API limitation,
  not a hidden one. The simulator is honestly most useful, and most
  realistic, for the hundreds of smaller US airports BTS covers that
  genuinely operate on one or two active runways most of the day.
- `capacity.scale`, `weather.inject`, `ground_stop`, and `flight.cancel`
  perturbations remain spec'd and validated but not executed - a real,
  bounded scope gap, not a silently-ignored one (the API's explicit 400
  makes this impossible to miss as a caller).
- The split-screen diff map and animated cascade graph the spec calls for
  don't exist; `/sandbox` is a form and a results panel. The underlying
  data (per-flight delay deltas, the M3 ripple) is already real and
  API-shaped for a future visualization layer to consume without an
  engine change.
