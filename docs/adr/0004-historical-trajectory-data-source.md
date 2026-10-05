# ADR 0004: ADS-B Exchange historical samples for M1/M4 training; scoped BTS window for M3; weather features deferred

**Status:** Accepted - deviation from the original master spec

## Context

Stage 5 (Network Intelligence) depends on three ML components with different,
non-interchangeable data requirements:

- **M1** (trajectory forecasting) needs continuous per-aircraft *position*
  history — sequences of real lat/lon/alt/speed over time — to train a
  sequence model (a GRU) and to compute the chronological error metrics the
  spec requires.
- **M4's learned layer** (the autoencoder) needs the same kind of continuous
  position tracks, resampled to 128 points per flight.
- **M3** (delay-propagation GNN) needs *scheduled flight* records with delay
  outcomes, aggregated per airport per time bucket — exactly what BTS
  provides, already wired in via `ml/data/loaders/bts.py` since Stage 4.

This project's own live ingest pipeline (`services/ingest`) is the obvious
first place to look for M1/M4's position data — but it has only been running
for this project's own development lifetime, which is nowhere near enough
accumulated history to train a sequence model: M1 needs many thousands of
independent 60-second-or-longer continuous tracks, spread across enough
aircraft and conditions to generalize, which would require running `ingest`
continuously for days to weeks to accumulate for real. `ml/train/
train_trajectory.py`'s pre-existing stub already anticipated exactly this —
it checks for sufficient history and bails out if there isn't enough, rather
than training on too little data or fabricating synthetic tracks (which
master spec rule 10, "never substitute a fabricated default for genuinely
missing input," rules out regardless).

## Decision

**M1 and M4's learned layer train on ADS-B Exchange's free historical
`readsb-hist` sample data** (`ml/data/loaders/adsbx_hist.py`) instead of this
project's own live-ingested history. ADS-B Exchange publishes global
airborne-traffic snapshots every 5 seconds and makes the complete data for
the 1st of each month free, with no registration or API key
(https://www.adsbexchange.com/data-products/sample-data/) — confirmed
directly against the live endpoint during this work, not assumed from
documentation alone.

**Training window:** `2024-01-01 14:00–15:00 UTC` (one hour, CONUS-filtered,
at the data's native 5-second cadence) — not a full day. A full day is
17,280 snapshots; even gzipped, that's tens of GB and would take unreasonably
long to download and process for a project at this scale. One hour at
~3,500+ CONUS aircraft per snapshot (measured directly) yields tens of
thousands of independent trajectory windows — enough for a meaningful
train/val/test split for a small 2-layer GRU, consistent in spirit with M2's
own precedent of training on a reduced window (one BTS month, not the spec's
multi-year ask) and reporting real numbers on that reduced window honestly,
rather than claiming full-spec data volume. The exact window (09:00-10:00 US
Eastern) was chosen for dense US daytime traffic across the whole CONUS
width, not cherry-picked for a particular outcome.

**M3's node features omit the live-weather covariates** (ceiling, visibility,
wind) the spec lists, because this project has no weather-data ingestion
pipeline at all — `weather_obs` exists as a schema with zero rows (see
`services/common/models/weather.py`, added in an earlier hardening pass).
Building a NOAA Aviation Weather ingestion pipeline, for both live use and
historical backfill, is a real, separate, non-trivial scope (the spec's own
`.env.example` already reserves `NOAA_AWC_BASE_URL` for exactly this,
unimplemented). M3 v1 trains on the delay/ops/cancellation/calendar features
that **are** available from BTS alone — which already exercises the model's
actual differentiating mechanism (the rotation-edge, cascade-propagation
graph structure the spec calls "what powers the flagship"), just without the
weather covariate.

**M3's training window is one BTS month** (January 2024 - the same month
already cached for M2), not the spec's "minimum 3 years." Unlike M1/M4, M3's
data source (BTS) has no rate-limit or free-tier constraint - more months
could be pulled. The scope reduction here is specifically about keeping
dataset-construction and training time reasonable for a project at this
scale, following the exact precedent M2 already set (and reported honestly
against) rather than introducing a new kind of deviation. One month (31
days) still gives a real chronological train/val/test split with genuine
day-of-week and intra-month structure - just not multi-year seasonal
coverage.

**M4's evaluation deviates from the spec's methodology, not just its data
source.** The spec evaluates the learned anomaly layer against "positives
from BTS Diverted/Cancelled + squawk events" - a tail-number-to-flight join
this project doesn't build, and in any case the one-hour ADS-B Exchange
training window realistically contains few or zero true diversions/
cancellations to learn from. Instead, `ml/train/train_autoencoder.py`
evaluates how well the unsupervised embedding's anomaly score agrees with
the rules layer (`services/inference/anomaly_rules.py`, wired into the live
pipeline in an earlier hardening pass) firing on the same segment. This is a
real, honest check that the learned layer captures something correlated
with anomalous kinematics - but it is NOT the spec's "lift over rules on
labeled incidents" metric, and the training script's report explicitly
labels it "agreement with the rules layer," not precision against ground
truth, so this distinction isn't lost by the time the number reaches
`docs/ml-report.md`.

## Consequences

- M1 and M4's learned layer are trained on a real, honestly-reported, but
  genuinely reduced dataset relative to the full spec's ask (one hour of one
  day, not a continuously-accumulated multi-week live history). This is
  reported plainly in `docs/ml-report.md`'s generated sections, the same way
  M2's reduced BTS window is — never presented as more data than it is.
- Because the training data is a single historical hour rather than this
  project's own live feed, M1's *live-serving* path
  (`services/inference/trajectory.py`) is a separate code path that consumes
  real-time `state_vectors` history at inference time, even though training
  happened on ADS-B Exchange data — this is exactly what the shared
  `services/common/features/trajectory.py` feature function exists to
  guarantee stays consistent (the same train/serve-parity test pattern
  Stage 4 established for M2).
- A future, better version of this would either accumulate enough of this
  project's own live history over time (and retrain against it), or pull a
  larger/more varied ADS-B Exchange historical window (more than one free
  day is available for non-free historical access, per their published
  pricing) — both are realistic follow-ups, not required to make the current
  models real and honestly evaluated.
- M3 will under-perform relative to a weather-aware version specifically
  during weather-driven delay events (the scenario where weather matters
  most) — this is a known, stated limitation, not a hidden gap: the model's
  reported metrics reflect exactly what it was trained on, and the ADR trail
  makes the omission traceable for whoever builds the weather pipeline later.

## Addendum (2026-10-05): M1's dead-reckoning baseline beats the GRU

After fixing a real bug in the baseline's velocity calculation (it was
dividing a window's *cumulative* displacement by one native 5-second step
instead of the true last-step delta, overstating implied velocity by ~11x
and making the baseline look artificially bad — caught by sanity-checking
predicted speeds against physically plausible aircraft speeds, not from a
test failure), the corrected numbers show the constant-velocity baseline
**beating** the trained GRU at every horizon with test coverage:

| horizon | baseline median error | GRU median error | n |
|---|---|---|---|
| 60s | 0.38 km | 9.67 km | 102,900 |
| 180s | 1.57 km | 29.27 km | 76,428 |
| 300s | 3.24 km | 49.22 km | 52,016 |
| 600s | n/a (0 valid windows) | n/a | 0 |
| 900s | n/a (0 valid windows) | n/a | 0 |

This is reported as-is, not improved-looking (the same project-wide rule
M4's unflattering PR-AUC result follows). The GRU's 80% quantile intervals
also only achieve 59–64% actual coverage against a nominal 80% target,
i.e. the model is overconfident, not just less accurate at the median.

**Why the baseline wins:** over 60–300s horizons, real aircraft in level
cruise (the dominant phase in one hour of CONUS daytime traffic) are close
to constant-velocity by nature — dead reckoning is a genuinely strong,
not a strawman, baseline at these horizons. The GRU had only one hour of
training data (627K windows, but drawn from a single hour's worth of
distinct aircraft/situations) and 10 epochs to learn to beat physics at a
regime where physics already does well; it did not get there.

**Why 600s/900s report nothing:** the 1-hour training window is shorter
than those horizons plus the window length needed to produce a target, so
zero test windows exist that far out — not a bug, a direct consequence of
the hour-long window this ADR already documents as a reduced-scope
decision.

**Not fixed by re-tuning in this pass**, consistent with reporting the
real result rather than chasing a flattering one: a longer/more varied
training window (see "Consequences" above) is the most likely lever to
let the GRU add value beyond dead reckoning, particularly by learning
the turning/climbing/descending cases where constant-velocity genuinely
breaks down and a sequence model has real information to add that this
one-hour sample may under-represent.
