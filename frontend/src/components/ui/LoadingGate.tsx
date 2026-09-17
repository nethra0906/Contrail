"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { Loader } from "./Loader";

const MIN_DISPLAY_MS = 1400;

/**
 * Gates first paint of the real app behind the branded loader for a fixed
 * minimum duration (long enough for the flight-path draw-in to complete),
 * then cross-fades into the app. A minimum duration - not "as long as
 * assets take to load" - is deliberate: on a fast connection the loader
 * would otherwise flash for 80ms, which reads as a bug, not a design
 * choice.
 */
export function LoadingGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setReady(true), MIN_DISPLAY_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <>
      <AnimatePresence>
        {!ready && (
          <motion.div
            key="loader"
            initial={{ opacity: 1 }}
            exit={{ opacity: 0, transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] } }}
          >
            <Loader />
          </motion.div>
        )}
      </AnimatePresence>
      {/* `display: contents` would be the cleanest way to fade this wrapper
          without affecting layout, but opacity does not reliably apply to a
          contents-display box (no generated box to paint). A full-height
          flex column matches what RootLayout's <body> already expects. */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: ready ? 1 : 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1], delay: ready ? 0.1 : 0 }}
        className="flex min-h-full flex-1 flex-col"
      >
        {children}
      </motion.div>
    </>
  );
}
