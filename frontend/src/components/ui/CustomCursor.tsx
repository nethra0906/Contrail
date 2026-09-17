"use client";

import { motion, useMotionValue, useSpring } from "framer-motion";
import { useEffect, useState, useSyncExternalStore } from "react";

const FINE_POINTER_QUERY = "(hover: hover) and (pointer: fine)";

/**
 * useSyncExternalStore rather than useState+useEffect for reading
 * matchMedia: this is exactly the case it exists for - subscribing to a
 * piece of state that lives outside React (the OS/browser's pointer
 * capability) without the "call setState synchronously inside an effect"
 * anti-pattern that causes an extra cascading render on mount.
 */
function subscribeFinePointer(callback: () => void) {
  const mql = window.matchMedia(FINE_POINTER_QUERY);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function getFinePointerSnapshot() {
  return window.matchMedia(FINE_POINTER_QUERY).matches;
}

function getFinePointerServerSnapshot() {
  return false;
}

/**
 * A spring-physics custom cursor: a small ring that trails the real pointer
 * with a light lag, and expands + tints cyan over anything interactive.
 * Only mounts on fine-pointer (mouse/trackpad) devices - see the
 * `(hover: hover) and (pointer: fine)` media query in globals.css that
 * hides the native cursor for exactly the same devices this renders for,
 * so touch users never lose their native tap cursor/feedback.
 */
export function CustomCursor() {
  const enabled = useSyncExternalStore(
    subscribeFinePointer,
    getFinePointerSnapshot,
    getFinePointerServerSnapshot,
  );
  const [isPointerDown, setIsPointerDown] = useState(false);
  const [isHoveringInteractive, setIsHoveringInteractive] = useState(false);

  const x = useMotionValue(-100);
  const y = useMotionValue(-100);
  const springX = useSpring(x, { stiffness: 500, damping: 40, mass: 0.4 });
  const springY = useSpring(y, { stiffness: 500, damping: 40, mass: 0.4 });

  useEffect(() => {
    if (!enabled) return;

    const move = (e: PointerEvent) => {
      x.set(e.clientX);
      y.set(e.clientY);
      const target = e.target as HTMLElement | null;
      setIsHoveringInteractive(!!target?.closest('a, button, [role="button"], input, [data-cursor-hover]'));
    };
    const down = () => setIsPointerDown(true);
    const up = () => setIsPointerDown(false);

    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("pointerdown", down);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerdown", down);
      window.removeEventListener("pointerup", up);
    };
  }, [enabled, x, y]);

  if (!enabled) return null;

  const scale = isPointerDown ? 0.75 : isHoveringInteractive ? 1.8 : 1;

  return (
    <motion.div
      aria-hidden
      className="pointer-events-none fixed left-0 top-0 z-[9999] -ml-2.5 -mt-2.5 h-5 w-5 rounded-full border"
      style={{
        x: springX,
        y: springY,
        borderColor: isHoveringInteractive ? "var(--accent-cyan)" : "var(--border-strong)",
        backgroundColor: isHoveringInteractive ? "var(--accent-cyan-soft)" : "transparent",
        boxShadow: isHoveringInteractive ? "var(--glow-cyan)" : "none",
      }}
      animate={{ scale }}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
    />
  );
}
