# ADR 0001: CONUS-only geographic scope

**Status:** Accepted (carried over from the master spec, restated here per
Stage 1 execution rule 9)

## Context

Community ADS-B coverage (adsb.lol, airplanes.live, OpenSky) is dense over
North America and Europe but has real gaps oceanically and over less
receiver-dense regions. BTS TranStats - the only free, 15-year, flight-level
delay ground truth available - is US-domestic only.

## Decision

Contrail v1 covers the continental United States (bbox: 24.5–49.5°N,
125.0–66.5°W) only.

## Consequences

- The delay-propagation GNN (M3) trains on a coherent, well-covered dataset
  with no missing-data imputation needed.
- International flights are visible while inside the bbox (e.g. transatlantic
  arrivals) but their pre-CONUS trajectory is not modeled.
- Alaska/Hawaii/territories are excluded from the primary bbox; individual
  major airports (e.g. KHNL) may still be seeded for fleet realism without
  full coverage.
