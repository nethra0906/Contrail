"use client";

import { MapboxOverlay } from "@deck.gl/mapbox";
import { ScatterplotLayer } from "@deck.gl/layers";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { listAircraft, type AircraftState } from "@/lib/api-client";
import { fadeInUp } from "@/lib/motion";
import { useMapStore } from "@/lib/store";
import { AnimatedCounter } from "@/components/ui/AnimatedCounter";
import { AltitudeLegend } from "./AltitudeLegend";

// Free, no-key vector basemap style. Swappable for a branded style later
// without touching any rendering logic below.
const BASEMAP_STYLE = "https://demotiles.maplibre.org/style.json";

const CONUS_CENTER: [number, number] = [-96, 38];

function altitudeColor(altFt: number | null): [number, number, number, number] {
  // Low altitude (near the ground) -> warm amber; cruise altitude -> cool cyan.
  // This is a deliberate visual encoding, not a decoration: it's how a
  // viewer reads climb/descent phases across the whole map at a glance.
  if (altFt === null) return [148, 163, 184, 200]; // slate - unknown/on-ground
  const t = Math.max(0, Math.min(1, altFt / 38000));
  const r = Math.round(251 - t * (251 - 34));
  const g = Math.round(191 - t * (191 - 211));
  const b = Math.round(36 + t * (238 - 36));
  return [r, g, b, 220];
}

export function LiveMap() {
  const mapContainer = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const bbox = useMapStore((s) => s.bbox);
  const selectedIcao24 = useMapStore((s) => s.selectedIcao24);
  const selectAircraft = useMapStore((s) => s.selectAircraft);

  const {
    data: aircraft,
    dataUpdatedAt,
    isError,
    isLoading,
  } = useQuery({
    queryKey: ["aircraft", bbox],
    queryFn: () => listAircraft(bbox),
  });

  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: BASEMAP_STYLE,
      center: CONUS_CENTER,
      zoom: 3.4,
      minZoom: 2,
      maxZoom: 14,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    const overlay = new MapboxOverlay({ layers: [] });
    map.addControl(overlay);

    mapRef.current = map;
    overlayRef.current = overlay;

    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!overlayRef.current) return;

    const layer = new ScatterplotLayer<AircraftState>({
      id: "aircraft",
      data: aircraft ?? [],
      getPosition: (d) => [d.lon, d.lat],
      getRadius: (d) => (d.icao24 === selectedIcao24 ? 9000 : 5500),
      getFillColor: (d) => altitudeColor(d.baro_alt_ft),
      getLineColor: (d) => (d.icao24 === selectedIcao24 ? [255, 255, 255, 255] : [0, 0, 0, 0]),
      lineWidthMinPixels: 2,
      stroked: true,
      radiusUnits: "meters",
      radiusMinPixels: 2,
      radiusMaxPixels: 10,
      pickable: true,
      onClick: (info) => {
        if (info.object) selectAircraft((info.object as AircraftState).icao24);
      },
      updateTriggers: {
        getRadius: [selectedIcao24],
        getLineColor: [selectedIcao24],
      },
    });

    overlayRef.current.setProps({ layers: [layer] });
  }, [aircraft, selectedIcao24, selectAircraft]);

  const statusColor = isError ? "var(--status-error)" : "var(--status-live)";
  const statusLabel = isError
    ? "live feed unreachable"
    : isLoading
      ? "connecting"
      : "live";

  return (
    <div className="relative h-full w-full">
      <div ref={mapContainer} className="h-full w-full" />

      <motion.div
        initial="hidden"
        animate="visible"
        variants={fadeInUp}
        className="pointer-events-none absolute left-3 top-3 rounded-xl border px-3.5 py-2.5 text-xs sm:left-4 sm:top-4"
        style={{
          borderColor: "var(--border-default)",
          background: "var(--bg-overlay)",
          backdropFilter: "blur(12px)",
          boxShadow: "var(--shadow-md)",
        }}
      >
        <div className="mb-0.5 flex items-center gap-1.5">
          <span className="relative flex h-1.5 w-1.5">
            <span
              className="absolute inline-flex h-full w-full rounded-full"
              style={{ background: statusColor, animation: "contrail-pulse-ring 1.8s ease-out infinite" }}
            />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full" style={{ background: statusColor }} />
          </span>
          <span className="text-[10px] uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
            {statusLabel}
          </span>
        </div>
        <div className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
          <AnimatedCounter value={aircraft?.length ?? 0} /> aircraft
        </div>
        <div style={{ color: "var(--text-tertiary)" }}>
          {isError
            ? "is the API running?"
            : dataUpdatedAt
              ? `updated ${new Date(dataUpdatedAt).toLocaleTimeString()}`
              : "loading..."}
        </div>
      </motion.div>

      <AltitudeLegend />
    </div>
  );
}
