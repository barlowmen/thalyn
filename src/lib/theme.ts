/**
 * Theme handling — dark / light / system, persisted to localStorage,
 * applied to the <html data-theme="..."> attribute that the design
 * tokens key off of.
 *
 * The boot script (`THEME_BOOT_SCRIPT`) runs synchronously in
 * <head> before React mounts so the page never renders in the wrong
 * theme. The runtime helpers handle subsequent updates.
 */

import type { Theme } from "@/design/tokens";

export type { Theme } from "@/design/tokens";
export { THEMES } from "@/design/tokens";

export const STORAGE_KEY = "thalyn:theme";
export const DEFAULT_THEME: Theme = "system";

const VALID_THEMES = new Set<Theme>(["dark", "light", "system"]);

export function isTheme(value: unknown): value is Theme {
  return typeof value === "string" && VALID_THEMES.has(value as Theme);
}

export function readStoredTheme(): Theme {
  if (typeof window === "undefined") return DEFAULT_THEME;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return isTheme(raw) ? raw : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function writeStoredTheme(theme: Theme): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Storage may be full or disabled; persistence is best-effort.
  }
}

export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", theme);
}

/**
 * IIFE that runs before React mounts. Inlined into index.html via a
 * <script> tag so the correct data-theme is on <html> by the time the
 * first paint happens — no flash.
 *
 * Mirrors the runtime helpers above; kept as a literal string because
 * the script must execute before any module loader runs.
 */
export const THEME_BOOT_SCRIPT = `
(function () {
  try {
    var stored = window.localStorage.getItem(${JSON.stringify(STORAGE_KEY)});
    var valid = stored === "dark" || stored === "light" || stored === "system";
    document.documentElement.setAttribute(
      "data-theme",
      valid ? stored : ${JSON.stringify(DEFAULT_THEME)}
    );
  } catch (e) {
    document.documentElement.setAttribute(
      "data-theme",
      ${JSON.stringify(DEFAULT_THEME)}
    );
  }
})();
`.trim();
