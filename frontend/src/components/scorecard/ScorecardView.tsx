"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { getModelScorecard, type BucketMetrics, type ModelMetrics, type ScorecardEntry } from "@/lib/api-client";
import { fadeInUp, staggerContainer } from "@/lib/motion";

const DURATION_BUCKET_ORDER = ["<15min", "15-45min", "45-120min", ">120min"];

function MetricBar({ label, metrics, isWinner }: { label: string; metrics: BucketMetrics; isWinner: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span
        className="w-44 shrink-0 text-xs"
        style={{ color: isWinner ? "var(--text-primary)" : "var(--text-tertiary)" }}
      >
        {label}
        {isWinner && (
          <span
            className="ml-1.5 rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide"
            style={{ background: "var(--accent-gradient-soft)", color: "var(--accent-cyan)" }}
          >
            best
          </span>
        )}
      </span>
      <div className="flex flex-1 items-center gap-4 text-xs" style={{ color: "var(--text-secondary)" }}>
        <span>MAE {metrics.mae_min.toFixed(2)} min</span>
        <span>P90 {metrics.p90_min.toFixed(2)} min</span>
        <span style={{ color: "var(--text-tertiary)" }}>n={metrics.n.toLocaleString()}</span>
      </div>
    </div>
  );
}

function DurationBucketTable({ metrics }: { metrics: ModelMetrics }) {
  const buckets = DURATION_BUCKET_ORDER.filter((b) => metrics.by_duration_bucket[b]);
  if (buckets.length === 0) return null;

  return (
    <div className="mt-3 overflow-hidden rounded-lg border" style={{ borderColor: "var(--border-subtle)" }}>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ background: "var(--bg-overlay)" }}>
            <th className="px-3 py-2 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>
              scheduled duration
            </th>
            <th className="px-3 py-2 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
              MAE (min)
            </th>
            <th className="px-3 py-2 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
              P90 (min)
            </th>
            <th className="px-3 py-2 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
              n
            </th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((label) => {
            const m = metrics.by_duration_bucket[label];
            return (
              <tr key={label} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
                <td className="px-3 py-1.5" style={{ color: "var(--text-secondary)" }}>
                  {label}
                </td>
                <td className="px-3 py-1.5 text-right" style={{ color: "var(--text-primary)" }}>
                  {m.mae_min.toFixed(2)}
                </td>
                <td className="px-3 py-1.5 text-right" style={{ color: "var(--text-primary)" }}>
                  {m.p90_min.toFixed(2)}
                </td>
                <td className="px-3 py-1.5 text-right" style={{ color: "var(--text-tertiary)" }}>
                  {m.n.toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ModelCard({ entry }: { entry: ScorecardEntry }) {
  const candidates: Array<[string, ModelMetrics]> = [
    ...Object.entries(entry.metrics.baselines ?? {}),
    ...(entry.metrics.lightgbm ? [["lightgbm", entry.metrics.lightgbm] as [string, ModelMetrics]] : []),
  ];
  const bestName = candidates.length
    ? candidates.reduce((best, cur) => (cur[1].overall.mae_min < best[1].overall.mae_min ? cur : best))[0]
    : null;

  return (
    <motion.div
      variants={fadeInUp}
      className="rounded-xl border p-5"
      style={{ borderColor: "var(--border-default)", background: "var(--bg-overlay)" }}
    >
      <div className="flex items-baseline justify-between">
        <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
          M2 &mdash; {entry.kind}
        </h2>
        <span className="font-mono text-[11px]" style={{ color: "var(--text-tertiary)" }}>
          {entry.model_version}
        </span>
      </div>
      <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
        promoted {new Date(entry.trained_at).toLocaleString()}
      </p>

      <div className="mt-4 divide-y" style={{ borderColor: "var(--border-subtle)" }}>
        {candidates.map(([name, m]) => (
          <MetricBar key={name} label={name} metrics={m.overall} isWinner={name === bestName} />
        ))}
      </div>

      {entry.metrics.lightgbm && <DurationBucketTable metrics={entry.metrics.lightgbm} />}
    </motion.div>
  );
}

export function ScorecardView() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["models", "scorecard"],
    queryFn: () => getModelScorecard(),
  });

  if (isLoading) {
    return (
      <p className="p-6 text-sm" style={{ color: "var(--text-tertiary)" }}>
        loading scorecard...
      </p>
    );
  }

  if (isError) {
    return (
      <p className="p-6 text-sm" style={{ color: "var(--status-error)" }}>
        scorecard unreachable &mdash; is the API running?
      </p>
    );
  }

  const models = data?.models ?? [];

  if (models.length === 0) {
    return (
      <div className="p-6 text-sm" style={{ color: "var(--text-tertiary)" }}>
        No model has been promoted yet. Run <code className="font-mono">make train</code> against a
        live database to populate this page.
      </div>
    );
  }

  return (
    <motion.div
      initial="hidden"
      animate="visible"
      variants={staggerContainer()}
      className="mx-auto flex max-w-3xl flex-col gap-4 p-6"
    >
      <div>
        <h1 className="text-xl font-semibold" style={{ color: "var(--text-primary)" }}>
          Model scorecard
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-tertiary)" }}>
          Every number below comes from the evaluation harness (ml/eval/) at training time - never
          hand-entered. See docs/ml-report.md for the full report.
        </p>
      </div>
      {models.map((entry) => (
        <ModelCard key={entry.kind} entry={entry} />
      ))}
    </motion.div>
  );
}
