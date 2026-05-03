/**
 * Deterministic project pill colour.
 *
 * Per the v0.31 plan: "project pill colour: derive from project slug
 * (deterministic hash → hue) so the same project is always the same
 * colour." OKLCH gives perceptually uniform spacing — picking a hue
 * with a fixed L/C lands on a clean swatch palette without needing a
 * curated theme list per project.
 *
 * The hue is the only colour dimension that varies; lightness and
 * chroma stay fixed so foreground text stays legible against every
 * computed background. The same hash also drives a darker variant
 * for the optional border / fg-on-light role.
 */

const LIGHTNESS = 0.92;
const CHROMA = 0.08;

const FG_LIGHTNESS = 0.32;
const FG_CHROMA = 0.12;

export function projectHue(seed: string): number {
  // 32-bit FNV-1a — small, fast, deterministic, and good enough for
  // pill-colour distribution. The exact algorithm doesn't matter as
  // long as it stays stable.
  let hash = 0x811c9dc5;
  for (let i = 0; i < seed.length; i++) {
    hash ^= seed.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  // Map the unsigned hash into the [0, 360) hue wheel.
  return ((hash >>> 0) % 360);
}

export function projectColor(seed: string): {
  background: string;
  foreground: string;
  border: string;
} {
  const hue = projectHue(seed);
  return {
    background: `oklch(${LIGHTNESS} ${CHROMA} ${hue})`,
    foreground: `oklch(${FG_LIGHTNESS} ${FG_CHROMA} ${hue})`,
    border: `oklch(${FG_LIGHTNESS} ${FG_CHROMA} ${hue} / 0.25)`,
  };
}
