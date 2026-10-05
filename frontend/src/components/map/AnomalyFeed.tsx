"use client";

import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { getAnomalies } from "@/lib/api-client";
import { fadeInUp } from "@/lib/motion";

const KIND_LABELS: Record<string, string> = {
  squawk_emergency: "emergency squawk",
  rapid_descent: "rapid descent",
  go_around: "go-around",
};

// Anomalies are a real-time-ish feed, but not update-every-frame like the
// live map's WS-pushed positions - a short poll interval is the right tool
// here, not a second WebSocket connection for a handful of events an hour.
const REFETCH_INTERVAL_MS = 15_000;

export function AnomalyFeed() {
  const [open, setOpen] = useState(false);
  const { data, isError } = useQuery({
    queryKey: ["anomalies"],
    queryFn: () => getAnomalies(60),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const anomalies = data ?? [];

  return (
    <div className="pointer-events-auto absolute bottom-3 right-3 z-10 sm:bottom-4 sm:right-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-cursor-hover
        className="flex items-center gap-1.5 rounded-xl border px-3.5 py-2.5 text-xs backdrop-blur-md"
        style={{
          borderColor: "var(--border-default)",
          background: "var(--bg-overlay)",
          boxShadow: "var(--shadow-md)",
          color: "var(--text-secondary)",
        }}
        aria-expanded={open}
      >
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: anomalies.length > 0 ? "var(--status-error)" : "var(--border-strong)" }}
        />
        anomalies ({anomalies.length})
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial="hidden"
            animate="visible"
            exit="hidden"
            variants={fadeInUp}
            className="absolute bottom-full right-0 mb-2 max-h-64 w-72 overflow-y-auto rounded-xl border p-2 text-xs backdrop-blur-md"
            style={{
              borderColor: "var(--border-default)",
              background: "var(--bg-overlay)",
              boxShadow: "var(--shadow-md)",
            }}
          >
            {isError && <p style={{ color: "var(--status-error)" }}>anomaly feed unreachable</p>}
            {!isError && anomalies.length === 0 && (
              <p style={{ color: "var(--text-tertiary)" }}>no anomalies in the last hour</p>
            )}
            {anomalies.map((a) => (
              <div
                key={a.id}
                className="border-t py-1.5 first:border-t-0"
                style={{ borderColor: "var(--border-subtle)" }}
              >
                <div className="flex items-center justify-between">
                  <span style={{ color: "var(--text-primary)" }}>{KIND_LABELS[a.kind] ?? a.kind}</span>
                  <span className="font-mono" style={{ color: "var(--text-tertiary)" }}>
                    {a.icao24}
                  </span>
                </div>
                <span style={{ color: "var(--text-tertiary)" }}>{new Date(a.ts).toLocaleTimeString()}</span>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
