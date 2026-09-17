"use client";

import { animate, svg } from "animejs";
import { useEffect, useRef } from "react";

/**
 * The app's signature loading moment: a flight path draws itself across the
 * screen (anime.js animating an SVG path's stroke), a small aircraft glyph
 * travels along it, and the wordmark resolves once the path completes. This
 * is the one place anime.js is used rather than framer-motion - framer
 * handles React-driven component state transitions throughout the rest of
 * the app, anime.js owns this one designed, non-interactive sequence where
 * precise timeline choreography (path draw -> dot travel -> text reveal)
 * matters more than binding to component state.
 */
export function Loader() {
  const pathRef = useRef<SVGPathElement | null>(null);
  const dotRef = useRef<SVGCircleElement | null>(null);
  const wordmarkRef = useRef<HTMLDivElement | null>(null);
  const taglineRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!pathRef.current || !dotRef.current) return;

    // NOTE: animejs v4 renamed the timing-function param from `easing` to
    // `ease`, and the string presets dropped their "ease" prefix
    // (e.g. "inOutCubic", not "easeInOutCubic") - verified against the
    // installed package's own .d.ts files rather than assumed, since v4 is
    // a ground-up rewrite of the v3 API most examples online still show.
    const drawable = svg.createDrawable(pathRef.current);

    const timeline = animate(drawable, {
      draw: ["0 0", "0 1"],
      ease: "inOutCubic",
      duration: 1100,
    });

    animate(dotRef.current, {
      ...svg.createMotionPath(pathRef.current),
      duration: 1100,
      ease: "inOutCubic",
    });

    if (wordmarkRef.current) {
      animate(wordmarkRef.current, {
        opacity: [0, 1],
        translateY: [10, 0],
        duration: 500,
        delay: 750,
        ease: "outQuad",
      });
    }
    if (taglineRef.current) {
      animate(taglineRef.current, {
        opacity: [0, 1],
        duration: 500,
        delay: 950,
        ease: "outQuad",
      });
    }

    return () => {
      timeline.pause();
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-[10000] flex flex-col items-center justify-center gap-6"
      style={{ background: "var(--bg-void)" }}
    >
      <svg viewBox="0 0 320 120" width="min(70vw, 320px)" height="120" aria-hidden>
        <path
          ref={pathRef}
          d="M10 100 C 80 100, 90 20, 160 20 S 260 100, 310 40"
          fill="none"
          stroke="var(--border-default)"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <circle ref={dotRef} r="4" fill="var(--accent-cyan)" style={{ filter: "drop-shadow(0 0 6px var(--accent-cyan))" }} />
      </svg>

      <div className="flex flex-col items-center gap-1">
        <div
          ref={wordmarkRef}
          className="text-2xl font-semibold tracking-tight text-gradient opacity-0"
          style={{ fontFamily: "var(--font-sans)" }}
        >
          CONTRAIL
        </div>
        <div ref={taglineRef} className="text-xs tracking-widest uppercase opacity-0" style={{ color: "var(--text-tertiary)" }}>
          rewind the sky
        </div>
      </div>
    </div>
  );
}
