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

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(
      (detail && typeof detail === "object" && "detail" in detail
        ? String((detail as { detail: unknown }).detail)
        : null) ?? `${path} -> ${res.status} ${res.statusText}`
    );
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

// Stage 7, scoped down (docs/adr/0005): a real counterfactual - close one
// airport's single modeled runway for part of a real historical BTS day
// and see the delay this adds, plus (best-effort) M3's own forecast of the
// ripple at flow-connected neighbor airports. `airport` here is a BTS/IATA
// station code (e.g. "ATL", "BOI"), NOT the ICAO codes the rest of this
// app otherwise uses - this feature has no live-DB dependency at all.
export interface SimulationRequest {
  airport: string;
  date: string; // "YYYY-MM-DD", within the cached BTS month (January 2024)
  closureStartMinute: number; // minutes after midnight UTC
  closureDurationMinutes: number;
}

export interface FlightDelayResult {
  flight_id: string;
  scheduled_minute: number;
  baseline_wait_min: number;
  scenario_wait_min: number;
  added_delay_min: number;
}

export interface NetworkRipple {
  model_version: string;
  horizon_minutes: number;
  neighbor_delay_delta_min: Record<string, number>;
}

export interface SimulationResult {
  airport: string;
  date: string;
  total_added_delay_min: number;
  affected_flight_count: number;
  max_added_delay_min: number;
  flights_simulated: number;
  baseline_mean_wait_min: number;
  baseline_max_wait_min: number;
  single_runway_model_already_saturated: boolean;
  top_delayed_flights: FlightDelayResult[];
  network_ripple: NetworkRipple | null;
}

export function runSimulation(req: SimulationRequest): Promise<SimulationResult> {
  const forkTs = `${req.date}T00:00:00Z`;
  const closureFrom = new Date(
    new Date(forkTs).getTime() + req.closureStartMinute * 60_000
  ).toISOString();
  return postJson<SimulationResult>("/api/v1/simulations", {
    scenario: {
      fork_ts: closureFrom,
      horizon_minutes: Math.min(req.closureDurationMinutes + 60, 720),
      perturbations: [
        {
          type: "runway.close",
          airport: req.airport.toUpperCase(),
          runway: "SIM",
          from: closureFrom,
          duration_minutes: req.closureDurationMinutes,
        },
      ],
    },
  });
}
