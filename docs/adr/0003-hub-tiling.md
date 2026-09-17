# ADR 0003: Hub-centered tiling instead of full-CONUS grid polling

**Status:** Accepted - deviation from the original master spec

## Context

`adsb.lol`'s live endpoint is `GET /v2/lat/{lat}/lon/{lon}/dist/{radius_nm}`
- a point+radius query, not an arbitrary bounding-box query. Covering the
full CONUS bbox on a regular grid at a 200 nm tile radius requires on the
order of 60–90 concurrent tiles per poll cycle.

During Stage 2 implementation this was measured directly against the live
API: even the default 30-tile **hub-centered** set (far fewer than a full
grid) triggers HTTP 429 from adsb.lol at anything above roughly 1
serialized request/second - there is no published rate limit, but the
empirical ceiling is low. A full 60–90 tile grid at that same ceiling would
take 60–90+ seconds per sweep and put sustained, meaningful load on a free,
community-run, unauthenticated API.

## Decision

Default ingest coverage is `hub_tiles()`
(`services/ingest/sources/tiling.py`): a fixed set of ~30 major CONUS hub
airports, each covered by a 200 nm circle, swept serially at a conservative
1 req/s. `grid_tiles()` (full-bbox grid) is implemented and available for
anyone running their own feeder/mirror with more headroom, but is not the
default.

## Consequences

- Hub tiling captures the large majority of CONUS scheduled traffic at any
  moment - most flights are near a hub during climb/descent, and en-route
  cruise segments between major hubs are still substantially covered by
  circles at 200 nm radius given typical hub spacing.
- Remote regional traffic far from any of the ~30 listed hubs (e.g. deep
  interior regional routes) is not covered in v1.
- Reduces load on adsb.lol by roughly 2–3x versus a full grid, which matters
  both operationally (fewer 429s, more stable ingestion) and ethically
  (proportionate use of a volunteer-run public good).

### Update - 2026-09-17, second measurement

Initial testing (first measurement above) used 1 req/s serialized and
completed the 30-tile sweep. A follow-up run of the *same* 30-tile sweep,
minutes later in the same dev session, drew 429s on 24/30 tiles even at that
same 1 req/s pace, taking ~109s wall clock with retries instead of the
expected ~30s. The most likely explanation is a Cloudflare burst/WAF rule
reacting to several back-to-back test invocations against the same source IP
within a short window - not necessarily a hard steady-state ceiling - but
this was **not** re-verified against sustained traffic (deliberately: further
rapid-fire testing against a free community API to pin down the exact number
would itself be the irresponsible-use pattern this ADR exists to avoid).

Decision: default rate dropped further to 0.5 req/s (`DEFAULT_RATE_PER_SEC`
in `services/ingest/sources/adsb_lol.py`), retry budget widened (4 attempts,
up to 20s backoff), and `INGEST_POLL_INTERVAL_SECONDS` default raised to 90s
accordingly. This is a conservative starting point, not a validated ceiling -
the consumer-lag and `INGEST_ERRORS_TOTAL{kind=RetryableError}` Prometheus
metrics this service exports are exactly what should be watched to retune it
against real, sustained (not test-burst) traffic in Stage 9 (Hardening).

### Update - 2026-09-17, third measurement: this looks like a quota, not a rate

After the fixes above shipped, a user running the ingest service continuously
for ~10 minutes (repeated 30-tile sweeps every 90s) still hit sustained 429s.
Re-investigated directly:

- Repeating the exact same already-queried coordinate 3x back-to-back:
  **200, 200, 200** - no throttling at all for a "known" point.
- Querying 10 fresh, never-before-queried hub coordinates at 0.5 req/s:
  **the first 429s immediately**, and every subsequent fresh coordinate also
  429s, all in the same ~7s window.
- Querying 5 *different* fresh coordinates spaced 3 seconds apart: **only
  the very first succeeds**; every fresh coordinate after that 429s, despite
  3s of separation.
- No `Retry-After` header is ever sent.

This pattern - a fresh coordinate succeeds once, and essentially every
subsequent *fresh* coordinate fails regardless of spacing, while an
already-seen coordinate keeps working - is not consistent with a simple
requests-per-second ceiling. It's consistent with a request **budget/quota**
(e.g. N distinct queries per hour or per day) that had already been consumed
by cumulative testing earlier in the same development session (many tens of
distinct hub coordinates queried repeatedly across several test runs).

Decision, given the size of that budget and its reset window are both
unknown:

- Default tile set cut from the full 30-hub `hub_tiles()` to a new
  `core_hub_tiles()` - 8 of the highest-traffic hubs only - cutting how much
  of any such budget one sweep consumes by roughly 4x.
- Rate further reduced to 1 req / 3s.
- Retry attempts reduced from 4 to 2 (hammering a quota-exhausted endpoint
  with more retries doesn't help and may make things worse).
- Circuit-breaker cooldown raised from 30s to 5 minutes, and its
  failure-threshold lowered from 5 consecutive failed poll cycles to 2, so a
  real quota exhaustion is detected and backed off from quickly rather than
  retried every cycle.

**If you see persistent 429s after this change**, the most likely
explanation is still a not-yet-reset quota from earlier testing, not a
misconfiguration - give it time (try again in 30-60 minutes) before assuming
something is broken. The `INGEST_ERRORS_TOTAL{kind=RetryableError}` metric
and the circuit-breaker's open/half-open transitions in the logs are the
right things to watch to characterize the actual reset window over time.
