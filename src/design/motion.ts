/**
 * Motion presets — three durations, one default easing. Bounce/spring
 * easings are reserved for "satisfying completion" moments (plan
 * approved, agent finished) and live in the components that use them.
 *
 * All values are mirrored from `tokens.css` so callers can write
 * inline animations without re-introducing magic numbers.
 */

export const duration = {
  /** 150 ms — instant transitions (state toggles, taps). */
  instant: 0.15,
  /** 250 ms — the default for most UI transitions. */
  default: 0.25,
  /** 400 ms — long, reserved for plan-tree expansion and agent spawn. */
  long: 0.4,
} as const;

export type DurationKey = keyof typeof duration;

/** Default easing — easeOutQuart. Matches `--easing-default`. */
export const easeOutQuart = [0.25, 0.46, 0.45, 0.94] as const;

/**
 * Resolve the runtime preference for reduced motion. Components should
 * gate non-functional animations on this rather than reading the media
 * query directly so tests can swap the value.
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
