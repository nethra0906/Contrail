"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import Link from "next/link";
import { useState } from "react";
import { listAirports } from "@/lib/api-client";
import { fadeInUp, staggerContainer } from "@/lib/motion";
import { Nav } from "@/components/ui/Nav";

export default function AirportsPage() {
  const [search, setSearch] = useState("");
  const { data, isLoading, isError } = useQuery({
    queryKey: ["airports"],
    queryFn: () => listAirports(),
  });

  const airports = (data ?? []).filter((a) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      a.icao.toLowerCase().includes(q) ||
      (a.iata ?? "").toLowerCase().includes(q) ||
      a.name.toLowerCase().includes(q) ||
      (a.city ?? "").toLowerCase().includes(q)
    );
  });

  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-base)" }}>
      <Nav />
      <main className="mx-auto w-full max-w-3xl flex-1 overflow-y-auto p-6">
        <h1 className="text-xl font-semibold" style={{ color: "var(--text-primary)" }}>
          Airports
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-tertiary)" }}>
          Every CONUS airport this project has seeded reference data for. Pick one to see its live
          delay forecast from M3, the delay-propagation GNN.
        </p>

        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by ICAO, IATA, name, or city..."
          className="mt-4 w-full rounded-lg border px-3 py-2 text-sm outline-none"
          style={{
            borderColor: "var(--border-default)",
            background: "var(--bg-overlay)",
            color: "var(--text-primary)",
          }}
        />

        {isLoading && (
          <p className="mt-6 text-sm" style={{ color: "var(--text-tertiary)" }}>
            loading airports...
          </p>
        )}
        {isError && (
          <p className="mt-6 text-sm" style={{ color: "var(--status-error)" }}>
            airports unreachable &mdash; is the API running?
          </p>
        )}

        {!isLoading && !isError && (
          <motion.ul
            initial="hidden"
            animate="visible"
            variants={staggerContainer()}
            className="mt-4 divide-y"
            style={{ borderColor: "var(--border-subtle)" }}
          >
            {airports.map((a) => (
              <motion.li key={a.icao} variants={fadeInUp}>
                <Link
                  href={`/airports/${a.icao}`}
                  data-cursor-hover
                  className="flex items-center justify-between py-2.5 text-sm transition-colors hover:opacity-80"
                >
                  <span style={{ color: "var(--text-primary)" }}>
                    {a.name}
                    {a.city && (
                      <span style={{ color: "var(--text-tertiary)" }}> &mdash; {a.city}</span>
                    )}
                  </span>
                  <span className="font-mono text-xs" style={{ color: "var(--text-tertiary)" }}>
                    {a.icao}
                    {a.iata ? ` / ${a.iata}` : ""}
                  </span>
                </Link>
              </motion.li>
            ))}
            {airports.length === 0 && (
              <p className="py-4 text-sm" style={{ color: "var(--text-tertiary)" }}>
                no airports match &ldquo;{search}&rdquo;
              </p>
            )}
          </motion.ul>
        )}
      </main>
    </div>
  );
}
