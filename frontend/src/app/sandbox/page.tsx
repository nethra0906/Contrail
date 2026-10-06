"use client";

import { useMutation } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { useState } from "react";
import { runSimulation, type SimulationResult } from "@/lib/api-client";
import { fadeInUp, staggerContainer } from "@/lib/motion";
import { Nav } from "@/components/ui/Nav";

// This scoped-down Stage 7 simulator replays a real BTS January 2024 day
// against one modeled runway per airport - see docs/adr/0005. Example
// airports chosen for a clean demo: BOI/PWM are small/mid-size (realistic
// near-zero baseline queueing, so a closure's effect is easy to read);
// ATL is a real busy hub included deliberately to show the honest
// "single_runway_model_already_saturated" caveat in the UI below.
const EXAMPLE_AIRPORTS = ["BOI", "PWM", "RDU", "ATL"];

function minutesToClock(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = Math.round(minutes % 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export default function SandboxPage() {
  const [airport, setAirport] = useState("BOI");
  const [date, setDate] = useState("2024-01-15");
  const [closureStartMinute, setClosureStartMinute] = useState(600); // 10:00
  const [closureDurationMinutes, setClosureDurationMinutes] = useState(60);

  const mutation = useMutation({
    mutationFn: () =>
      runSimulation({ airport, date, closureStartMinute, closureDurationMinutes }),
  });

  const result: SimulationResult | undefined = mutation.data;

  return (
    <div className="flex h-screen flex-col" style={{ background: "var(--bg-base)" }}>
      <Nav />
      <main className="mx-auto w-full max-w-2xl flex-1 overflow-y-auto p-6">
        <h1 className="text-xl font-semibold" style={{ color: "var(--text-primary)" }}>
          Counterfactual sandbox
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-tertiary)" }}>
          Stage 7, scoped down (<code className="font-mono text-xs">docs/adr/0005</code>): close
          one airport&apos;s runway on a real historical day and see the delay it actually adds,
          computed by replaying real BTS January 2024 scheduled traffic through a deterministic
          discrete-event queue &mdash; not a fabricated demo. Also shows M3&apos;s own forecast of
          the ripple at connected airports, reusing the already-trained delay-propagation GNN
          with no retraining.
        </p>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
          className="mt-5 grid grid-cols-2 gap-3 rounded-xl border p-5"
          style={{ borderColor: "var(--border-default)", background: "var(--bg-overlay)" }}
        >
          <label className="col-span-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
            Airport (BTS/IATA code)
            <input
              value={airport}
              onChange={(e) => setAirport(e.target.value.toUpperCase().slice(0, 3))}
              list="sandbox-example-airports"
              className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm uppercase outline-none"
              style={{
                borderColor: "var(--border-default)",
                background: "var(--bg-base)",
                color: "var(--text-primary)",
              }}
            />
            <datalist id="sandbox-example-airports">
              {EXAMPLE_AIRPORTS.map((a) => (
                <option key={a} value={a} />
              ))}
            </datalist>
          </label>

          <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            Date (January 2024 only)
            <input
              type="date"
              value={date}
              min="2024-01-01"
              max="2024-01-31"
              onChange={(e) => setDate(e.target.value)}
              className="mt-1 w-full rounded-md border px-2 py-1.5 text-sm outline-none"
              style={{
                borderColor: "var(--border-default)",
                background: "var(--bg-base)",
                color: "var(--text-primary)",
              }}
            />
          </label>

          <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            Closure start ({minutesToClock(closureStartMinute)} UTC)
            <input
              type="range"
              min={0}
              max={1380}
              step={5}
              value={closureStartMinute}
              onChange={(e) => setClosureStartMinute(Number(e.target.value))}
              className="mt-2 w-full"
            />
          </label>

          <label className="col-span-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
            Closure duration ({closureDurationMinutes} min)
            <input
              type="range"
              min={5}
              max={360}
              step={5}
              value={closureDurationMinutes}
              onChange={(e) => setClosureDurationMinutes(Number(e.target.value))}
              className="mt-2 w-full"
            />
          </label>

          <button
            type="submit"
            data-cursor-hover
            disabled={mutation.isPending}
            className="col-span-2 mt-1 rounded-md px-3 py-2 text-sm font-medium transition-opacity disabled:opacity-50"
            style={{ background: "var(--accent-gradient-soft)", color: "var(--accent-cyan)" }}
          >
            {mutation.isPending ? "Simulating..." : "Run simulation"}
          </button>
        </form>

        {mutation.isError && (
          <p className="mt-4 text-sm" style={{ color: "var(--status-error)" }}>
            {(mutation.error as Error).message}
          </p>
        )}

        {result && (
          <motion.div
            initial="hidden"
            animate="visible"
            variants={staggerContainer()}
            className="mt-5 space-y-4"
          >
            {result.single_runway_model_already_saturated && (
              <motion.p
                variants={fadeInUp}
                className="rounded-md border px-3 py-2 text-xs"
                style={{ borderColor: "var(--status-warning)", color: "var(--status-warning)" }}
              >
                {result.airport} already has a high baseline wait (
                {result.baseline_mean_wait_min.toFixed(0)} min avg) with no closure applied &mdash;
                this airport&apos;s real demand exceeds what one modeled runway can represent (see
                the ADR), so the diff below is less meaningful here than for a smaller airport.
              </motion.p>
            )}

            <motion.div
              variants={fadeInUp}
              className="grid grid-cols-3 gap-3 rounded-xl border p-4"
              style={{ borderColor: "var(--border-default)", background: "var(--bg-overlay)" }}
            >
              <div>
                <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  total added delay
                </div>
                <div className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
                  {result.total_added_delay_min.toFixed(0)} min
                </div>
              </div>
              <div>
                <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  flights affected
                </div>
                <div className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
                  {result.affected_flight_count} / {result.flights_simulated}
                </div>
              </div>
              <div>
                <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  max single-flight delay
                </div>
                <div className="text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
                  {result.max_added_delay_min.toFixed(0)} min
                </div>
              </div>
            </motion.div>

            {result.network_ripple && (
              <motion.div
                variants={fadeInUp}
                className="rounded-xl border p-4"
                style={{ borderColor: "var(--border-default)", background: "var(--bg-overlay)" }}
              >
                <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                  M3 network ripple (t+{result.network_ripple.horizon_minutes}min)
                </h2>
                <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                  Predicted arrival-delay change at {result.airport}&apos;s flow-connected
                  neighbors, from the already-trained GNN&apos;s own forward pass &mdash; see the
                  ADR for exactly what this is and isn&apos;t claiming.
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {Object.entries(result.network_ripple.neighbor_delay_delta_min).map(
                    ([code, delta]) => (
                      <span
                        key={code}
                        className="rounded-md border px-2 py-1 text-xs font-mono"
                        style={{ borderColor: "var(--border-subtle)", color: "var(--text-secondary)" }}
                      >
                        {code} {delta >= 0 ? "+" : ""}
                        {delta.toFixed(2)}min
                      </span>
                    )
                  )}
                </div>
              </motion.div>
            )}
            {!result.network_ripple && (
              <motion.p variants={fadeInUp} className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                No M3 network-ripple estimate available (no trained delay-propagation model found,
                or this airport wasn&apos;t in its training graph).
              </motion.p>
            )}

            <motion.div
              variants={fadeInUp}
              className="rounded-xl border p-4"
              style={{ borderColor: "var(--border-default)", background: "var(--bg-overlay)" }}
            >
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Most-delayed flights
              </h2>
              <div className="mt-2 divide-y" style={{ borderColor: "var(--border-subtle)" }}>
                {result.top_delayed_flights.slice(0, 10).map((f) => (
                  <div
                    key={f.flight_id}
                    className="flex items-center justify-between py-1.5 text-xs"
                  >
                    <span className="font-mono" style={{ color: "var(--text-secondary)" }}>
                      {f.flight_id} &middot; sched {minutesToClock(f.scheduled_minute)}
                    </span>
                    <span style={{ color: "var(--text-primary)" }}>
                      +{f.added_delay_min.toFixed(0)} min
                    </span>
                  </div>
                ))}
                {result.top_delayed_flights.length === 0 && (
                  <p className="py-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
                    No flight was delayed by this closure.
                  </p>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </main>
    </div>
  );
}
