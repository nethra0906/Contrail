/**
 * Live aircraft feed over `/ws/live` - the Stage 3 replacement for polling
 * `GET /api/v1/aircraft`. Subscribes to the H3 cells covering the current
 * viewport (resolution 5, matching `H3_LIVE_RESOLUTION` in
 * `services/common/geo.py`), decodes the binary frames from
 * `ws-protocol.ts`, and keeps a running `icao24 -> AircraftState` map: a
 * full frame replaces it, a delta frame upserts one record.
 *
 * Per `services/api/ws/live.py`'s own docstring, changing viewport means
 * reconnecting with a new subscribe message rather than resubscribing
 * mid-stream - so this hook tears down and reopens the socket whenever the
 * requested cell set changes, which is cheap at this scale.
 */

import { useEffect, useRef, useState } from "react";
import * as h3 from "h3-js";
import type { AircraftState, Bbox } from "./api-client";
import { decodeFrame, FRAME_TYPE_FULL, type AircraftRecord } from "./ws-protocol";

const H3_LIVE_RESOLUTION = 5;

// Mirrors MAX_SUBSCRIBED_CELLS in services/api/ws/live.py - trimming here
// too avoids sending a subscribe payload the server will truncate anyway.
const MAX_SUBSCRIBED_CELLS = 300;

const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 15000;

export type LiveFeedStatus = "connecting" | "live" | "error";

function wsUrl(path: string): string {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  return apiBase.replace(/^http/, "ws") + path;
}

export function bboxToH3Cells(bbox: Bbox, resolution = H3_LIVE_RESOLUTION): string[] {
  const polygon: [number, number][] = [
    [bbox.minLat, bbox.minLon],
    [bbox.minLat, bbox.maxLon],
    [bbox.maxLat, bbox.maxLon],
    [bbox.maxLat, bbox.minLon],
  ];
  return h3.polygonToCells(polygon, resolution).slice(0, MAX_SUBSCRIBED_CELLS);
}

function toAircraftState(r: AircraftRecord): AircraftState {
  return {
    icao24: r.icao24,
    ts: new Date().toISOString(),
    lat: r.lat,
    lon: r.lon,
    baro_alt_ft: r.altFt,
    velocity_kt: r.velocityKt,
    heading_deg: r.headingDeg,
    vert_rate_fpm: r.vertRateFpm,
    on_ground: r.onGround,
  };
}

export interface LiveFeed {
  aircraft: AircraftState[];
  status: LiveFeedStatus;
  lastUpdatedAt: number | null;
}

export function useLiveAircraftFeed(bbox: Bbox): LiveFeed {
  const [aircraft, setAircraft] = useState<AircraftState[]>([]);
  const [status, setStatus] = useState<LiveFeedStatus>("connecting");
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const trackedRef = useRef<Map<string, AircraftState>>(new Map());

  const cells = bboxToH3Cells(bbox);
  const cellsKey = cells.join(",");

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      setStatus("connecting");
      socket = new WebSocket(wsUrl("/ws/live"));
      socket.binaryType = "arraybuffer";

      socket.onopen = () => {
        attempt = 0;
        socket?.send(JSON.stringify({ op: "subscribe", h3_cells: cells }));
      };

      socket.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        let frame;
        try {
          frame = decodeFrame(event.data);
        } catch {
          return;
        }

        if (frame.frameType === FRAME_TYPE_FULL) {
          trackedRef.current = new Map(frame.records.map((r) => [r.icao24, toAircraftState(r)]));
        } else {
          for (const r of frame.records) {
            trackedRef.current.set(r.icao24, toAircraftState(r));
          }
        }

        setAircraft(Array.from(trackedRef.current.values()));
        setLastUpdatedAt(Date.now());
        setStatus("live");
      };

      socket.onerror = () => {
        setStatus("error");
      };

      socket.onclose = () => {
        if (stopped) return;
        setStatus("error");
        const delay = Math.min(RECONNECT_MAX_DELAY_MS, RECONNECT_BASE_DELAY_MS * 2 ** attempt);
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
    // cellsKey captures everything cells-derived that should trigger a
    // reconnect; `cells` itself is a new array every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cellsKey]);

  return { aircraft, status, lastUpdatedAt };
}
