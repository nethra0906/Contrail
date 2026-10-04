"""OpenAP-based fuel-flow enrichment for a single state vector.

Pure function, no I/O (same philosophy as track_state.py / leg_detect.py) -
the only "lookup" here is OpenAP's own bundled aircraft performance tables,
cached per type code in-process since the same handful of commercial types
recur across every message.

Fuel enrichment is reported only when the aircraft's type is known - there is
no registry-backfill job yet to populate `aircraft.type_code` (that's a
later-stage piece of work), so in practice this returns None for most
aircraft today. That's deliberate: rule 10 of the master spec is never
substitute a fabricated default in place of genuinely missing input. Once a
type-code backfill exists, this starts producing real numbers with no change
here.
"""

from __future__ import annotations

from openap import FuelFlow, prop

from services.common.telemetry import get_logger

logger = get_logger(__name__)

# OpenAP's FuelFlow/prop lookups parse bundled performance tables from disk -
# worth caching per type code rather than re-parsing on every state vector.
_fuelflow_cache: dict[str, FuelFlow | None] = {}
_aircraft_limits_cache: dict[str, dict] = {}


def _fuelflow_for(type_code: str) -> FuelFlow | None:
    key = type_code.strip().lower()
    if key in _fuelflow_cache:
        return _fuelflow_cache[key]

    try:
        fuelflow = FuelFlow(key)
        limits = prop.aircraft(key)["limits"]
    except (ValueError, FileNotFoundError, KeyError):
        logger.info("openap_unknown_type", type_code=type_code)
        _fuelflow_cache[key] = None
        return None

    _fuelflow_cache[key] = fuelflow
    _aircraft_limits_cache[key] = limits
    return fuelflow


def estimate_fuel_flow_kg_s(
    type_code: str | None,
    on_ground: bool,
    alt_ft: float | None,
    velocity_kt: float | None,
    vert_rate_fpm: float | None,
) -> float | None:
    """Instantaneous fuel flow (kg/s) for the aircraft's current state.

    Assumes a representative half-loaded mass (OEW + half the margin to
    MTOW) - there is no real payload/fuel-state signal in an ADS-B-only
    track, so this is an order-of-magnitude estimate, not a measurement.
    Returns None when the aircraft is on the ground (OpenAP's drag/thrust
    model is for airborne flight), the type is unknown, or altitude/speed
    aren't available.
    """
    if on_ground or not type_code or alt_ft is None or velocity_kt is None:
        return None

    fuelflow = _fuelflow_for(type_code)
    if fuelflow is None:
        return None

    limits = _aircraft_limits_cache[type_code.strip().lower()]
    mass_kg = limits["OEW"] + 0.5 * (limits["MTOW"] - limits["OEW"])

    try:
        flow = fuelflow.enroute(mass=mass_kg, tas=velocity_kt, alt=alt_ft, vs=vert_rate_fpm or 0)
    except Exception:
        logger.exception("openap_fuel_flow_failed", type_code=type_code)
        return None

    return float(flow)
