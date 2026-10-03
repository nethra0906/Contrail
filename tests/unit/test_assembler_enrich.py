"""Unit tests for OpenAP fuel-flow enrichment (services/assembler/enrich.py).

Uses a real, well-known type code ("a320") rather than mocking OpenAP - the
bundled performance tables are deterministic and fast to load, so there's
no benefit to faking the library here, only risk of the fake drifting from
its real behaviour.
"""

from __future__ import annotations

from services.assembler.enrich import estimate_fuel_flow_kg_s


def test_returns_none_when_type_unknown():
    assert estimate_fuel_flow_kg_s(None, False, 35000, 450, 0) is None
    assert estimate_fuel_flow_kg_s("not-a-real-type", False, 35000, 450, 0) is None


def test_returns_none_on_ground():
    assert estimate_fuel_flow_kg_s("a320", True, 0, 0, 0) is None


def test_returns_none_without_altitude_or_speed():
    assert estimate_fuel_flow_kg_s("a320", False, None, 450, 0) is None
    assert estimate_fuel_flow_kg_s("a320", False, 35000, None, 0) is None


def test_returns_a_plausible_cruise_fuel_flow():
    flow = estimate_fuel_flow_kg_s("a320", False, 35000, 450, 0)
    assert flow is not None
    # A real A320 burns on the order of 2000-3000 kg/hr at cruise; the
    # half-loaded-mass estimate should land in a sane ballpark around that,
    # not just "some positive number".
    per_hour = flow * 3600
    assert 1000 < per_hour < 5000


def test_type_code_is_case_insensitive():
    lower = estimate_fuel_flow_kg_s("a320", False, 35000, 450, 0)
    upper = estimate_fuel_flow_kg_s("A320", False, 35000, 450, 0)
    assert lower == upper
