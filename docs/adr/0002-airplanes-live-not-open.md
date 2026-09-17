# ADR 0002: airplanes.live is not a usable no-key secondary source

**Status:** Accepted - deviation from the original master spec

## Context

`docs/CONTRAIL_MASTER_SPEC.md` (section 2) listed `airplanes.live` as a
secondary/failover ADS-B source alongside `adsb.lol`, both described as
"free, no API key." Verified live during Stage 2 implementation:

```
$ curl https://api.airplanes.live/v2/lat/40.7/lon/-74.0/dist/50
{"error": "Please contact us at contact@airplanes.live. Your email MUST
include any links, a description of the project, and any information you
deem appropriate."}
```

airplanes.live requires emailing the maintainers for API approval - it is
not an open, unauthenticated endpoint like adsb.lol.

## Decision

- `adsb.lol` is the sole primary ADS-B source for v1.
- OpenSky Network (OAuth2 client-credentials, 4,000 req/day once registered)
  remains the documented secondary/validation source, since that access path
  was independently verified via OpenSky's own docs and requires only free
  self-service registration, not a manual approval email.
- `airplanes.live` support is deferred: the adapter interface
  (`services/ingest/sources/base.py`) makes adding it a one-file change if/when
  API access is granted, but it is not implemented or wired into the ingest
  service in v1.

## Consequences

Single-source ingestion means adsb.lol's own availability is a single point
of failure for live data (mitigated by the circuit breaker + graceful
degradation to "last known position" in the UI, not by failover to a second
live source). This is an honest trade-off, not a hidden one.
