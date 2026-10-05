/**
 * Thin fetch wrapper for the Contrail API. Every endpoint the frontend calls
 * is typed here so a backend contract change is a compile error in the
 * frontend, not a runtime surprise.
 */

function resolveApiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!configured && process.env.NODE_ENV === "production") {
    console.warn(
      "NEXT_PUBLIC_API_BASE_URL is not set in this production build - falling back to " +
        "http://localhost:8000, which will not reach the real API for deployed visitors."
    );
  }
  return configured ?? "http://localhost:8000";
}

const API_BASE = resolveApiBase();

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

export function getAircraftTrack(icao24: string): Promise<AircraftState[]> {
  return getJson<AircraftState[]>(`/api/v1/aircraft/${icao24}/track`);
}

export interface BucketMetrics {
  mae_min: number;
  p90_min: number;
  n: number;
}

export interface ModelMetrics {
  overall: BucketMetrics;
  by_duration_bucket: Record<string, BucketMetrics>;
}

// The scorecard's `metrics` shape differs per model kind - each training
// script (ml/train/train_*.py) reports whatever its own evaluation harness
// computes, not a one-size-fits-all schema. `Record<string, unknown>` here
// plus kind-specific narrowing in ScorecardView.tsx mirrors that: this
// client doesn't assume every model kind looks like ETA's baselines/lightgbm
// shape.
export interface ScorecardEntry {
  kind: string;
  model_version: string;
  trained_at: string;
  train_window: Record<string, unknown>;
  metrics: Record<string, unknown> & {
    baselines?: Record<string, ModelMetrics>;
    lightgbm?: ModelMetrics;
  };
}

export function getModelScorecard(model?: string): Promise<{ models: ScorecardEntry[] }> {
  const params = model ? `?model=${encodeURIComponent(model)}` : "";
  return getJson<{ models: ScorecardEntry[] }>(`/api/v1/models/scorecard${params}`);
}

export interface Anomaly {
  id: string;
  icao24: string;
  ts: string;
  kind: string;
  score: number;
  evidence: Record<string, unknown>;
  flight_id: string | null;
}

export function getAnomalies(sinceMinutes = 60): Promise<Anomaly[]> {
  return getJson<Anomaly[]>(`/api/v1/anomalies?since_minutes=${sinceMinutes}`);
}

export interface AirportSummary {
  icao: string;
  iata: string | null;
  name: string;
  city: string | null;
  lat: number;
  lon: number;
  hub_rank: number | null;
}

export function listAirports(): Promise<AirportSummary[]> {
  return getJson<AirportSummary[]>("/api/v1/airports");
}

export interface AirportDetail {
  icao: string;
  iata: string | null;
  name: string;
  city: string | null;
  lat: number;
  lon: number;
  elevation_ft: number | null;
  runways: { ident: string; heading_deg: number | null; length_ft: number | null }[];
}

export function getAirport(icao: string): Promise<AirportDetail> {
  return getJson<AirportDetail>(`/api/v1/airports/${icao}`);
}

export interface DelayForecastHorizon {
  hours: number;
  predicted_arr_delay_min: number;
}

export interface DelayForecast {
  airport: string;
  model_version: string;
  horizons: DelayForecastHorizon[];
}

// Thrown by getJson as a generic Error on any non-OK response, including a
// 404 when no model has been promoted yet - callers distinguish "no
// forecast available" (expected, pre-training) from a real failure by
// checking the status code embedded in the message, matching the pattern
// every other not-yet-populated endpoint in this app already uses.
export function getDelayForecast(icao: string): Promise<DelayForecast> {
  return getJson<DelayForecast>(`/api/v1/airports/${icao}/delay-forecast`);
}
