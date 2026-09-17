"use client";

import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";
import { useEffect } from "react";

/**
 * A number that spring-animates toward its target value rather than
 * snapping - used for the live aircraft count so a poll-cycle update reads
 * as "the count is moving," not a jarring re-render. Passing a MotionValue
 * as a motion component's children (verified against the installed
 * framer-motion build) updates the DOM text node directly on every spring
 * tick, bypassing React re-renders entirely - the whole point for a value
 * that can tick many times a second.
 */
export function AnimatedCounter({ value }: { value: number }) {
  const motionValue = useMotionValue(value);
  const spring = useSpring(motionValue, { stiffness: 120, damping: 20 });
  const display = useTransform(spring, (v) => Math.round(v).toLocaleString());

  useEffect(() => {
    motionValue.set(value);
  }, [value, motionValue]);

  return <motion.span>{display}</motion.span>;
}
