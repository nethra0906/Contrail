"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import Link from "next/link";
import { use } from "react";
import { getAirport, getDelayForecast } from "@/lib/api-client";
import { fadeInUp, staggerContainer } from "@/lib/motion";
import { Nav } from "@/components/ui/Nav";

export default function AirportDetailPage({ params }: { params: Promise<{ icao: string }> }) {
  const { icao } = use(params);

  const airportQuery = useQuery({
    queryKey: ["airport", icao],
    queryFn: () => getAirport(icao),
  });
  const forecastQuery = useQuery({
    queryKey: ["delay-forecast", icao],
    queryFn: () => getDelayForecast(icao),
    retry: false, // a 404 here means "no model promoted yet" - not worth retrying
  });

  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-base)" }}>
      <Nav />
      <main className="mx-auto w-full max-w-2xl flex-1 overflow-y-auto p-6">
        <Link href="/airports" data-cursor-hover className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          &larr; all airports
        </Link>

        {airportQuery.isLoading && (
          <p className="mt-4 text-sm" style={{ color: "var(--text-tertiary)" }}>
            loading...
          </p>
        )}
        {airportQuery.isError && (
          <p className="mt-4 text-sm" style={{ color: "var(--status-error)" }}>
            unknown airport {icao.toUpperCase()}
          </p>
        )}

        {airportQuery.data && (
          <>
            <h1 className="mt-2 text-xl font-semibold" style={{ color: "var(--text-primary)" }}>
              {airportQuery.data.name}
            </h1>
            <p className="mt-1 text-sm" style={{ color: "var(--text-tertiary)" }}>
              {airportQuery.data.city ?? "unknown city"} &middot; {airportQuery.data.icao}
              {airportQuery.data.iata ? ` / ${airportQuery.data.iata}` : ""}
              {airportQuery.data.elevation_ft != null && ` · ${airportQuery.data.elevation_ft}ft elevation`}
            </p>

            {airportQuery.data.runways.length > 0 && (
              <div className="mt-4">
                <h2 className="text-xs uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
                  runways
                </h2>
                <ul className="mt-1 flex flex-wrap gap-2">
                  {airportQuery.data.runways.map((r) => (
                    <li
                      key={r.ident}
                      className="rounded-md border px-2 py-1 text-xs"
                      style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}
                    >
                      {r.ident}
                      {r.length_ft ? ` · ${r.length_ft}ft` : ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="mt-6 rounded-xl border p-5" style={{ borderColor: "var(--border-default)", background: "var(--bg-overlay)" }}>
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Delay forecast
              </h2>
              <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                M3 &mdash; the delay-propagation GNN&apos;s live forecast for this airport.
              </p>

              {forecastQuery.isLoading && (
                <p className="mt-3 text-sm" style={{ color: "var(--text-tertiary)" }}>
                  loading forecast...
                </p>
              )}
              {forecastQuery.isError && (
                <p className="mt-3 text-sm" style={{ color: "var(--text-tertiary)" }}>
                  No forecast available for this airport yet &mdash; either no delay-propagation
                  model has been promoted (run <code className="font-mono">make train</code>), or
                  this airport wasn&apos;t among the nodes in the training graph.
                </p>
              )}
              {forecastQuery.data && (
                <motion.div
                  initial="hidden"
                  animate="visible"
                  variants={staggerContainer()}
                  className="mt-3 divide-y"
                  style={{ borderColor: "var(--border-subtle)" }}
                >
                  {forecastQuery.data.horizons.map((h) => (
                    <motion.div
                      key={h.hours}
                      variants={fadeInUp}
                      className="flex items-center justify-between py-1.5 text-sm"
                    >
                      <span style={{ color: "var(--text-tertiary)" }}>t+{h.hours}h</span>
                      <span style={{ color: "var(--text-primary)" }}>
                        {h.predicted_arr_delay_min.toFixed(1)} min
                      </span>
                    </motion.div>
                  ))}
                  <p className="pt-2 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                    model {forecastQuery.data.model_version}
                  </p>
                </motion.div>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
