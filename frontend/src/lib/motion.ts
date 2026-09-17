/**
 * Shared framer-motion variants and transitions. Every animated component
 * in the app pulls from here rather than inventing its own easing/duration
 * pair - that is what keeps motion feeling like one coherent system instead
 * of a pile of independently-tuned effects.
 */

import type { Transition, Variants } from "framer-motion";

export const springTransition: Transition = {
  type: "spring",
  stiffness: 320,
  damping: 28,
  mass: 0.9,
};

export const softSpring: Transition = {
  type: "spring",
  stiffness: 180,
  damping: 24,
};

export const fadeInUp: Variants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: springTransition },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] } },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.92 },
  visible: { opacity: 1, scale: 1, transition: springTransition },
};

export const slideInRight: Variants = {
  hidden: { opacity: 0, x: 32 },
  visible: { opacity: 1, x: 0, transition: springTransition },
  exit: { opacity: 0, x: 32, transition: { duration: 0.2 } },
};

export const slideInUp: Variants = {
  hidden: { opacity: 0, y: 48 },
  visible: { opacity: 1, y: 0, transition: springTransition },
  exit: { opacity: 0, y: 48, transition: { duration: 0.2 } },
};

/** Applied to a container to stagger its children's own variant animations. */
export const staggerContainer = (staggerDelay = 0.06): Variants => ({
  hidden: {},
  visible: {
    transition: { staggerChildren: staggerDelay, delayChildren: 0.05 },
  },
});

export const hoverLift = {
  whileHover: { y: -2, transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] } },
  whileTap: { scale: 0.97 },
};
