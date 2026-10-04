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

export interface ScorecardEntry {
  kind: string;
  model_version: string;
  trained_at: string;
  train_window: Record<string, unknown>;
  metrics: {
    baselines?: Record<string, ModelMetrics>;
    lightgbm?: ModelMetrics;
  };
}

export function getModelScorecard(model?: string): Promise<{ models: ScorecardEntry[] }> {
  const params = model ? `?model=${encodeURIComponent(model)}` : "";
  return getJson<{ models: ScorecardEntry[] }>(`/api/v1/models/scorecard${params}`);
}
