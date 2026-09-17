"use client";

import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { getAircraftTrack, type AircraftState } from "@/lib/api-client";
import { fadeInUp, staggerContainer } from "@/lib/motion";
import { useMapStore } from "@/lib/store";
import { ParticleField } from "@/components/ui/ParticleField";

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <motion.div variants={fadeInUp} className="flex items-baseline justify-between py-1.5">
      <dt className="text-xs" style={{ color: "var(--text-tertiary)" }}>
        {label}
      </dt>
      <dd className="font-mono text-sm" style={{ color: "var(--text-primary)" }}>
        {value}
      </dd>
    </motion.div>
  );
}

function EmptyState() {
  return (
    <div className="relative flex flex-1 flex-col items-center justify-center gap-3 overflow-hidden px-6 text-center">
      <div className="absolute inset-0 opacity-30">
        <ParticleField density={24} />
      </div>
      <motion.div
        className="relative z-10 flex h-12 w-12 items-center justify-center rounded-full border"
        style={{ borderColor: "var(--border-default)" }}
        animate={{ y: [0, -6, 0] }}
        transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent-cyan)" strokeWidth="1.5">
          <path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2.5 1.5V22l4-1 4 1v-1.5L13 19v-5.5l8 2.5z" />
        </svg>
      </motion.div>
      <p className="relative z-10 text-sm" style={{ color: "var(--text-secondary)" }}>
        Click an aircraft on the map to see its recent track.
      </p>
    </div>
  );
}

function TrackSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-1">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="skeleton-shimmer h-6 rounded" style={{ opacity: 1 - i * 0.12 }} />
      ))}
    </div>
  );
}

interface PanelContentProps {
  selectedIcao24: string | null;
  latest: AircraftState | undefined;
  track: AircraftState[] | undefined;
  isLoading: boolean;
  onClose: () => void;
}

function PanelContent({ selectedIcao24, latest, track, isLoading, onClose }: PanelContentProps) {
  if (!selectedIcao24) {
    return <EmptyState />;
  }

  return (
    <motion.div
      key={selectedIcao24}
      initial="hidden"
      animate="visible"
      variants={staggerContainer(0.04)}
      className="flex flex-1 flex-col gap-4 overflow-y-auto p-4"
    >
      <div className="flex items-center justify-between">
        <motion.h2
          variants={fadeInUp}
          className="font-mono text-lg font-semibold uppercase tracking-wide"
          style={{ color: "var(--text-primary)" }}
        >
          {selectedIcao24}
        </motion.h2>
        <button
          onClick={onClose}
          data-cursor-hover
          className="rounded-md p-1 transition-colors"
          style={{ color: "var(--text-tertiary)" }}
          aria-label="Close"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      {isLoading ? (
        <TrackSkeleton />
      ) : latest ? (
        <dl className="rounded-lg border p-3" style={{ borderColor: "var(--border-subtle)" }}>
          <StatRow
            label="Altitude"
            value={latest.on_ground ? "on ground" : `${latest.baro_alt_ft?.toLocaleString() ?? "N/A"} ft`}
          />
          <StatRow label="Speed" value={`${latest.velocity_kt?.toFixed(0) ?? "N/A"} kt`} />
          <StatRow label="Heading" value={`${latest.heading_deg?.toFixed(0) ?? "N/A"} deg`} />
          <StatRow label="Vertical rate" value={`${latest.vert_rate_fpm?.toFixed(0) ?? "N/A"} fpm`} />
        </dl>
      ) : (
        <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>
          No recent state.
        </p>
      )}

      <motion.div variants={fadeInUp}>
        <h3 className="mb-1.5 text-xs uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
          Track history ({track?.length ?? 0} points)
        </h3>
        <div
          className="max-h-64 overflow-y-auto rounded-lg border text-xs"
          style={{ borderColor: "var(--border-subtle)" }}
        >
          {track
            ?.slice(-20)
            .reverse()
            .map((p) => (
              <div
                key={p.ts}
                className="flex justify-between border-b px-2 py-1 last:border-0"
                style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}
              >
                <span>{new Date(p.ts).toLocaleTimeString()}</span>
                <span>{p.baro_alt_ft ? `${Math.round(p.baro_alt_ft)} ft` : "ground"}</span>
              </div>
            ))}
        </div>
      </motion.div>
    </motion.div>
  );
}

export function AircraftPanel() {
  const selectedIcao24 = useMapStore((s) => s.selectedIcao24);
  const selectAircraft = useMapStore((s) => s.selectAircraft);

  const { data: track, isLoading } = useQuery({
    queryKey: ["track", selectedIcao24],
    queryFn: () => getAircraftTrack(selectedIcao24!),
    enabled: !!selectedIcao24,
  });

  const latest = track?.at(-1);

  return (
    <>
      {/* Desktop: fixed right sidebar. Hidden below sm, replaced by the
          mobile bottom sheet so the map keeps full width on small screens
          instead of being squeezed by a 320px-wide column. */}
      <aside
        className="hidden w-80 flex-col border-l sm:flex"
        style={{ borderColor: "var(--border-subtle)", background: "var(--bg-overlay)" }}
      >
        <PanelContent
          selectedIcao24={selectedIcao24}
          latest={latest}
          track={track}
          isLoading={isLoading}
          onClose={() => selectAircraft(null)}
        />
      </aside>

      {/* Mobile: bottom sheet that slides up only when an aircraft is
          selected, so it never eats screen space while the map is the
          only thing on screen. */}
      <AnimatePresence>
        {selectedIcao24 && (
          <motion.div
            key="mobile-sheet"
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 32 }}
            className="fixed inset-x-0 bottom-0 z-30 flex max-h-[60vh] flex-col rounded-t-2xl border-t sm:hidden"
            style={{
              borderColor: "var(--border-default)",
              background: "var(--bg-overlay-strong)",
              backdropFilter: "blur(16px)",
              boxShadow: "var(--shadow-lg)",
            }}
          >
            <div className="mx-auto mt-2 h-1 w-10 rounded-full" style={{ background: "var(--border-strong)" }} />
            <PanelContent
              selectedIcao24={selectedIcao24}
              latest={latest}
              track={track}
              isLoading={isLoading}
              onClose={() => selectAircraft(null)}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
