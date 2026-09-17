from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, field_validator


class StateVectorIn(BaseModel):
    """Normalized shape every ingest source adapter must produce, regardless
    of the wire format the upstream API uses. This is the seam that makes
    adding a new source (a third ADS-B provider, say) a one-file change.
    """

    icao24: str
    ts: dt.datetime
    lat: float
    lon: float
    baro_alt_ft: float | None = None
    geo_alt_ft: float | None = None
    velocity_kt: float | None = None
    heading_deg: float | None = None
    vert_rate_fpm: float | None = None
    on_ground: bool = False
    squawk: str | None = None
    source: str

    @field_validator("icao24")
    @classmethod
    def lower_hex(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or len(v) > 6:
            raise ValueError("icao24 must be a non-empty hex string of at most 6 chars")
        return v

    @field_validator("lat")
    @classmethod
    def lat_range(cls, v: float) -> float:
        if not (-90 <= v <= 90):
            raise ValueError("lat out of range")
        return v

    @field_validator("lon")
    @classmethod
    def lon_range(cls, v: float) -> float:
        if not (-180 <= v <= 180):
            raise ValueError("lon out of range")
        return v


class StateVectorOut(BaseModel):
    icao24: str
    ts: dt.datetime
    lat: float
    lon: float
    baro_alt_ft: float | None
    velocity_kt: float | None
    heading_deg: float | None
    vert_rate_fpm: float | None
    on_ground: bool
    callsign: str | None = None

    model_config = {"from_attributes": True}
