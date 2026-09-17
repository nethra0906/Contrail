/**
 * Thin fetch wrapper for the Contrail API. Every endpoint the frontend calls
 * is typed here so a backend contract change is a compile error in the
 * frontend, not a runtime surprise.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface AircraftState {
  icao24: string;
  ts: string;
  lat: number;
  lon: number;
  baro_alt_ft: number | null;
  velocity_kt: number | null;
  heading_deg: number | null;
  vert_rate_fpm: number | null;
  on_ground: boolean;
}

export interface Airport {
  icao: string;
  iata: string | null;
  name: string;
  city: string | null;
  lat: number;
  lon: number;
  hub_rank: number | null;
}

export interface Bbox {
  minLat: number;
  maxLat: number;
  minLon: number;
  maxLon: number;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${path} -> ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function listAircraft(bbox: Bbox, limit = 2000): Promise<AircraftState[]> {
  const params = new URLSearchParams({
    min_lat: String(bbox.minLat),
    max_lat: String(bbox.maxLat),
    min_lon: String(bbox.minLon),
    max_lon: String(bbox.maxLon),
    limit: String(limit),
  });
  return getJson<AircraftState[]>(`/api/v1/aircraft?${params}`);
}

export function getAircraftTrack(icao24: string): Promise<AircraftState[]> {
  return getJson<AircraftState[]>(`/api/v1/aircraft/${icao24}/track`);
}

export function listAirports(): Promise<Airport[]> {
  return getJson<Airport[]>("/api/v1/airports");
}
