"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import type { ReactNode } from "react";
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

const MODEL_LABELS: Record<string, string> = {
  eta: "M2 — ETA regression",
  trajectory: "M1 — Trajectory forecasting",
  network: "M3 — Delay-propagation GNN",
  anomaly: "M4 — Learned anomaly layer",
};

function CardShell({ entry, children }: { entry: ScorecardEntry; children: ReactNode }) {
  return (
    <motion.div
      variants={fadeInUp}
      className="rounded-xl border p-5"
      style={{ borderColor: "var(--border-default)", background: "var(--bg-overlay)" }}
    >
      <div className="flex items-baseline justify-between">
        <h2 className="text-base font-semibold" style={{ color: "var(--text-primary)" }}>
          {MODEL_LABELS[entry.kind] ?? entry.kind}
        </h2>
        <span className="font-mono text-[11px]" style={{ color: "var(--text-tertiary)" }}>
          {entry.model_version}
        </span>
      </div>
      <p className="mt-0.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
        promoted {new Date(entry.trained_at).toLocaleString()}
      </p>
      <div className="mt-4" style={{ color: "var(--text-secondary)" }}>
        {children}
      </div>
    </motion.div>
  );
}

// --- M2 (eta): baselines vs. LightGBM, with the duration-bucket breakdown. ---
function EtaCard({ entry }: { entry: ScorecardEntry }) {
  const candidates: Array<[string, ModelMetrics]> = [
    ...Object.entries(entry.metrics.baselines ?? {}),
    ...(entry.metrics.lightgbm ? [["lightgbm", entry.metrics.lightgbm] as [string, ModelMetrics]] : []),
  ];
  const bestName = candidates.length
    ? candidates.reduce((best, cur) => (cur[1].overall.mae_min < best[1].overall.mae_min ? cur : best))[0]
    : null;

  return (
    <CardShell entry={entry}>
      <div className="divide-y" style={{ borderColor: "var(--border-subtle)" }}>
        {candidates.map(([name, m]) => (
          <MetricBar key={name} label={name} metrics={m.overall} isWinner={name === bestName} />
        ))}
      </div>
      {entry.metrics.lightgbm && <DurationBucketTable metrics={entry.metrics.lightgbm} />}
    </CardShell>
  );
}

// --- M1 (trajectory): per-horizon km error, baseline vs. model. ---
interface HorizonErrorRow {
  median_error_km: number | null;
  p90_error_km: number | null;
  n: number;
}

function HorizonTable({ perHorizon, unit }: { perHorizon: Record<string, HorizonErrorRow>; unit: string }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr style={{ background: "var(--bg-overlay)" }}>
          <th className="px-2 py-1.5 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>
            horizon
          </th>
          <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
            median ({unit})
          </th>
          <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
            P90 ({unit})
          </th>
          <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
            n
          </th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(perHorizon).map(([h, m]) => (
          <tr key={h} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
            <td className="px-2 py-1" style={{ color: "var(--text-secondary)" }}>
              {h}s
            </td>
            <td className="px-2 py-1 text-right" style={{ color: "var(--text-primary)" }}>
              {m.median_error_km != null ? m.median_error_km.toFixed(2) : "–"}
            </td>
            <td className="px-2 py-1 text-right" style={{ color: "var(--text-primary)" }}>
              {m.p90_error_km != null ? m.p90_error_km.toFixed(2) : "–"}
            </td>
            <td className="px-2 py-1 text-right" style={{ color: "var(--text-tertiary)" }}>
              {m.n.toLocaleString()}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TrajectoryCard({ entry }: { entry: ScorecardEntry }) {
  const model = entry.metrics.model as
    | { overall_median_error_km: number; per_horizon: Record<string, HorizonErrorRow> }
    | undefined;
  const baseline = entry.metrics.baseline as
    | { per_horizon: Record<string, HorizonErrorRow> }
    | undefined;
  if (!model) return <CardShell entry={entry}>no metrics reported</CardShell>;

  return (
    <CardShell entry={entry}>
      <p className="mb-3 text-xs" style={{ color: "var(--text-tertiary)" }}>
        overall median error: {model.overall_median_error_km.toFixed(2)} km
      </p>
      {baseline && (
        <>
          <p className="mb-1 text-[11px] uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
            baseline: constant-velocity dead reckoning
          </p>
          <HorizonTable perHorizon={baseline.per_horizon} unit="km" />
        </>
      )}
      <p className="mb-1 mt-3 text-[11px] uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
        GRU (median / q50)
      </p>
      <HorizonTable perHorizon={model.per_horizon} unit="km" />
    </CardShell>
  );
}

// --- M3 (network): per-horizon-hour MAE, two baselines vs. the GNN. ---
interface HorizonMaeRow {
  mae_min: number | null;
  rmse_min: number | null;
  n: number;
}

function NetworkHorizonTable({ perHorizonHours }: { perHorizonHours: Record<string, HorizonMaeRow> }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr style={{ background: "var(--bg-overlay)" }}>
          <th className="px-2 py-1.5 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>
            horizon
          </th>
          <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
            MAE (min)
          </th>
          <th className="px-2 py-1.5 text-right font-medium" style={{ color: "var(--text-tertiary)" }}>
            n
          </th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(perHorizonHours).map(([h, m]) => (
          <tr key={h} className="border-t" style={{ borderColor: "var(--border-subtle)" }}>
            <td className="px-2 py-1" style={{ color: "var(--text-secondary)" }}>
              t+{h}h
            </td>
            <td className="px-2 py-1 text-right" style={{ color: "var(--text-primary)" }}>
              {m.mae_min != null ? m.mae_min.toFixed(2) : "–"}
            </td>
            <td className="px-2 py-1 text-right" style={{ color: "var(--text-tertiary)" }}>
              {m.n.toLocaleString()}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NetworkCard({ entry }: { entry: ScorecardEntry }) {
  const model = entry.metrics.model as
    | { overall_mae_min: number; per_horizon_hours: Record<string, HorizonMaeRow> }
    | undefined;
  const histBaseline = entry.metrics.historical_mean_baseline as
    | { per_horizon_hours: Record<string, HorizonMaeRow> }
    | undefined;
  const lgbmBaseline = entry.metrics.lgbm_baseline as
    | { per_horizon_hours: Record<string, HorizonMaeRow> }
    | undefined;
  if (!model) return <CardShell entry={entry}>no metrics reported</CardShell>;

  return (
    <CardShell entry={entry}>
      <p className="mb-3 text-xs" style={{ color: "var(--text-tertiary)" }}>
        overall MAE: {model.overall_mae_min.toFixed(2)} min
      </p>
      {histBaseline && (
        <>
          <p className="mb-1 text-[11px] uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
            baseline: historical mean by (airport, hour, day-of-week)
          </p>
          <NetworkHorizonTable perHorizonHours={histBaseline.per_horizon_hours} />
        </>
      )}
      {lgbmBaseline && (
        <>
          <p className="mb-1 mt-3 text-[11px] uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
            baseline: LightGBM + neighbor delay
          </p>
          <NetworkHorizonTable perHorizonHours={lgbmBaseline.per_horizon_hours} />
        </>
      )}
      <p className="mb-1 mt-3 text-[11px] uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
        diffusion-GCN + temporal GRU
      </p>
      <NetworkHorizonTable perHorizonHours={model.per_horizon_hours} />
    </CardShell>
  );
}

// --- M4 (anomaly): reconstruction error + agreement with the rules layer. ---
function AnomalyCard({ entry }: { entry: ScorecardEntry }) {
  const model = entry.metrics.model as
    | {
        reconstruction_mae: number;
        rule_agreement: {
          n_test_segments: number;
          n_rule_flagged: number;
          pr_auc_vs_rules: number | null;
          precision_at_k_vs_rules: number | null;
          note: string;
        };
      }
    | undefined;
  if (!model) return <CardShell entry={entry}>no metrics reported</CardShell>;
  const { rule_agreement: ra } = model;

  return (
    <CardShell entry={entry}>
      <p className="text-xs">reconstruction MAE: {model.reconstruction_mae.toFixed(3)}</p>
      <p className="mt-2 text-xs">
        PR-AUC vs. rules layer: {ra.pr_auc_vs_rules != null ? ra.pr_auc_vs_rules.toFixed(3) : "n/a"}
      </p>
      <p className="text-xs">
        precision@k vs. rules layer:{" "}
        {ra.precision_at_k_vs_rules != null ? ra.precision_at_k_vs_rules.toFixed(3) : "n/a"}
      </p>
      <p className="mt-1 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
        {ra.n_rule_flagged} / {ra.n_test_segments} test segments flagged by the rules layer
      </p>
      <p className="mt-2 text-[11px] italic" style={{ color: "var(--text-tertiary)" }}>
        {ra.note}
      </p>
    </CardShell>
  );
}

function ModelCard({ entry }: { entry: ScorecardEntry }) {
  switch (entry.kind) {
    case "eta":
      return <EtaCard entry={entry} />;
    case "trajectory":
      return <TrajectoryCard entry={entry} />;
    case "network":
      return <NetworkCard entry={entry} />;
    case "anomaly":
      return <AnomalyCard entry={entry} />;
    default:
      return <CardShell entry={entry}>unrecognized model kind</CardShell>;
  }
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
