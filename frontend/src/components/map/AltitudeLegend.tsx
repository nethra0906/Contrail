"use client";

import { motion } from "framer-motion";
import { fadeInUp } from "@/lib/motion";

export function AltitudeLegend() {
  return (
    <motion.div
      initial="hidden"
      animate="visible"
      variants={fadeInUp}
      transition={{ delay: 0.1 }}
      className="pointer-events-none absolute bottom-3 left-3 rounded-xl border px-3.5 py-2.5 text-xs sm:bottom-4 sm:left-4"
      style={{
        borderColor: "var(--border-default)",
        background: "var(--bg-overlay)",
        backdropFilter: "blur(12px)",
        boxShadow: "var(--shadow-md)",
      }}
    >
      <div className="mb-1.5 font-semibold" style={{ color: "var(--text-primary)" }}>
        Altitude
      </div>
      <div
        className="h-1.5 w-28 rounded-full sm:w-32"
        style={{ background: "var(--accent-gradient)" }}
      />
      <div className="mt-1 flex justify-between" style={{ color: "var(--text-tertiary)" }}>
        <span>ground</span>
        <span>FL380+</span>
      </div>
    </motion.div>
  );
}
