"use client";

import { AnimatePresence, motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import { useState } from "react";
import { ParticleField } from "./ParticleField";

interface NavItem {
  label: string;
  active: boolean;
  stage?: string;
  description?: string;
}

const NAV_ITEMS: NavItem[] = [
  { label: "Live", active: true },
  {
    label: "Timeline",
    active: false,
    stage: "Stage 6",
    description: "Scrub back through any point in the retention window and replay it.",
  },
  {
    label: "Sandbox",
    active: false,
    stage: "Stage 7",
    description:
      "Fork reality at a timestamp, inject a disruption, and diff the counterfactual against what actually happened.",
  },
  {
    label: "Scorecard",
    active: false,
    stage: "Stage 4",
    description: "Live model accuracy - predicted vs. actual, updated as ground truth arrives.",
  },
];

function ComingSoonItem({ item }: { item: NavItem }) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-cursor-hover
        className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm transition-colors"
        style={{ color: "var(--text-tertiary)" }}
        aria-haspopup="true"
        aria-expanded={open}
      >
        {item.label}
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: "var(--border-strong)" }}
        />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.96 }}
            transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
            className="absolute left-1/2 top-full z-50 mt-2 w-64 -translate-x-1/2 rounded-lg border p-3 text-left shadow-lg"
            style={{
              background: "var(--bg-overlay-strong)",
              borderColor: "var(--border-default)",
              backdropFilter: "blur(12px)",
              boxShadow: "var(--shadow-lg)",
            }}
          >
            <div className="mb-1 flex items-center gap-2">
              <span
                className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                style={{ background: "var(--accent-gradient-soft)", color: "var(--accent-cyan)" }}
              >
                {item.stage}
              </span>
              <span className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                {item.label}
              </span>
            </div>
            <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
              {item.description}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ParallaxLogo() {
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const springX = useSpring(x, { stiffness: 150, damping: 18 });
  const springY = useSpring(y, { stiffness: 150, damping: 18 });
  const rotateX = useTransform(springY, [-20, 20], [4, -4]);
  const rotateY = useTransform(springX, [-20, 20], [-4, 4]);

  return (
    <motion.div
      className="flex items-baseline gap-2"
      style={{ perspective: 400 }}
      onMouseMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        x.set(e.clientX - rect.left - rect.width / 2);
        y.set(e.clientY - rect.top - rect.height / 2);
      }}
      onMouseLeave={() => {
        x.set(0);
        y.set(0);
      }}
    >
      <motion.h1
        style={{ rotateX, rotateY }}
        className="text-lg font-bold tracking-tight"
      >
        Contrail
      </motion.h1>
      <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
        rewind the sky
      </span>
    </motion.div>
  );
}

export function Nav() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header
      className="relative flex items-center justify-between border-b px-4 py-2.5"
      style={{ borderColor: "var(--border-subtle)", background: "var(--bg-base)" }}
    >
      {/* overflow-hidden is scoped to this particle layer alone, not the
          header itself - clipping the whole header would also clip the
          "coming soon" popover and the mobile drawer, both of which render
          below the header's bottom edge via absolute positioning. */}
      <div className="absolute inset-0 overflow-hidden opacity-40">
        <ParticleField density={28} />
      </div>

      <div className="relative z-10">
        <ParallaxLogo />
      </div>

      <nav className="relative z-10 hidden items-center gap-1 sm:flex">
        <span
          className="rounded-md px-2 py-1 text-sm font-medium"
          style={{ color: "var(--text-primary)" }}
        >
          Live
        </span>
        {NAV_ITEMS.slice(1).map((item) => (
          <ComingSoonItem key={item.label} item={item} />
        ))}
      </nav>

      <button
        type="button"
        data-cursor-hover
        className="relative z-10 flex h-8 w-8 items-center justify-center rounded-md sm:hidden"
        onClick={() => setMobileOpen((v) => !v)}
        aria-label="Toggle menu"
        aria-expanded={mobileOpen}
      >
        <div className="flex flex-col gap-1">
          <motion.span
            animate={{ rotate: mobileOpen ? 45 : 0, y: mobileOpen ? 5 : 0 }}
            className="h-[1.5px] w-4 rounded-full"
            style={{ background: "var(--text-primary)" }}
          />
          <motion.span
            animate={{ opacity: mobileOpen ? 0 : 1 }}
            className="h-[1.5px] w-4 rounded-full"
            style={{ background: "var(--text-primary)" }}
          />
          <motion.span
            animate={{ rotate: mobileOpen ? -45 : 0, y: mobileOpen ? -5 : 0 }}
            className="h-[1.5px] w-4 rounded-full"
            style={{ background: "var(--text-primary)" }}
          />
        </div>
      </button>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="absolute left-0 right-0 top-full z-20 flex flex-col gap-1 border-b p-3 sm:hidden"
            style={{ background: "var(--bg-overlay-strong)", borderColor: "var(--border-default)", backdropFilter: "blur(12px)" }}
          >
            <span className="px-2 py-2 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
              Live
            </span>
            {NAV_ITEMS.slice(1).map((item) => (
              <div key={item.label} className="rounded-md px-2 py-2" style={{ color: "var(--text-tertiary)" }}>
                <div className="flex items-center gap-2 text-sm">
                  {item.label}
                  <span
                    className="rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide"
                    style={{ background: "var(--accent-gradient-soft)", color: "var(--accent-cyan)" }}
                  >
                    {item.stage}
                  </span>
                </div>
                <p className="mt-0.5 text-xs leading-relaxed">{item.description}</p>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
