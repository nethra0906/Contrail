import { create } from "zustand";
import type { Bbox } from "./api-client";

interface MapState {
  bbox: Bbox;
  selectedIcao24: string | null;
  setBbox: (bbox: Bbox) => void;
  selectAircraft: (icao24: string | null) => void;
}

// Default view: continental US, matching the ingest service's coverage -
// see services/common/config.py conus_bbox and docs/adr/0001-conus-only-scope.md.
const CONUS_BBOX: Bbox = { minLat: 24.5, maxLat: 49.5, minLon: -125.0, maxLon: -66.5 };

export const useMapStore = create<MapState>((set) => ({
  bbox: CONUS_BBOX,
  selectedIcao24: null,
  setBbox: (bbox) => set({ bbox }),
  selectAircraft: (icao24) => set({ selectedIcao24: icao24 }),
}));
