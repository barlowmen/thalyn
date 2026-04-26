/**
 * TypeScript mirror of the design tokens defined in `tokens.css`.
 *
 * The CSS file is the source of truth at runtime — these constants
 * exist so motion choreography, inline-style overrides, and tests can
 * reference token names without hard-coding strings. Keep the two
 * files in lockstep; the visual-regression tests catch drift.
 */

export const cssTokens = {
  bg: "var(--bg)",
  surface: "var(--surface)",
  surfaceElevated: "var(--surface-elevated)",
  border: "var(--border)",

  text: "var(--text)",
  textMuted: "var(--text-muted)",

  accent: "var(--accent)",
  accentFg: "var(--accent-fg)",
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--danger)",
  focusRing: "var(--focus-ring)",

  radiusInput: "var(--radius-input)",
  radiusCard: "var(--radius-card)",
  radiusWindow: "var(--radius-window)",

  railWidth: "var(--rail-width)",
  sidebarWidthMin: "var(--sidebar-width-min)",
  sidebarWidthDefault: "var(--sidebar-width-default)",
  sidebarWidthMax: "var(--sidebar-width-max)",
  inspectorWidthMin: "var(--inspector-width-min)",
  inspectorWidthDefault: "var(--inspector-width-default)",
  inspectorWidthMax: "var(--inspector-width-max)",

  fontSans: "var(--font-sans)",
  fontMono: "var(--font-mono)",
} as const;

export type CssToken = keyof typeof cssTokens;

/** Spacing scale, in pixels. Mirrors Tailwind's defaults. */
export const space = {
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  6: 24,
  8: 32,
  12: 48,
  16: 64,
} as const;

export type SpaceStep = keyof typeof space;

export type Theme = "dark" | "light" | "system";

export const THEMES: readonly Theme[] = ["dark", "light", "system"] as const;
