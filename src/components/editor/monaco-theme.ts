import type { editor } from "monaco-editor";

/**
 * Monaco doesn't read CSS custom properties, so we mirror the OKLCH
 * design tokens as hex equivalents here. The values track
 * `src/design/tokens.css` — when the canonical palette shifts, these
 * shift with it.
 *
 * The dark theme inherits from `vs-dark`, the light from `vs`; only
 * the colors that visibly differ from the defaults are overridden so
 * Monaco's syntax-highlight rules carry through unchanged.
 */
export const THALYN_DARK = "thalyn-dark";
export const THALYN_LIGHT = "thalyn-light";

export const thalynDarkTheme: editor.IStandaloneThemeData = {
  base: "vs-dark",
  inherit: true,
  rules: [],
  colors: {
    "editor.background": "#1d1d1d",
    "editor.foreground": "#f2f2f2",
    "editorCursor.foreground": "#7ba6ff",
    "editor.lineHighlightBackground": "#262626",
    "editorLineNumber.foreground": "#6e6e6e",
    "editorLineNumber.activeForeground": "#bdbdbd",
    "editor.selectionBackground": "#34487a",
    "editor.inactiveSelectionBackground": "#283354",
    "editorIndentGuide.background": "#2c2c2c",
    "editorIndentGuide.activeBackground": "#404040",
    "editorWhitespace.foreground": "#3a3a3a",
    "editorWidget.background": "#262626",
    "editorWidget.border": "#3a3a3a",
    "editorSuggestWidget.background": "#262626",
    "editorSuggestWidget.border": "#3a3a3a",
    "editorSuggestWidget.selectedBackground": "#33415e",
    "scrollbarSlider.background": "#3a3a3a80",
    "scrollbarSlider.hoverBackground": "#4a4a4ac0",
    "scrollbarSlider.activeBackground": "#5a5a5af0",
    focusBorder: "#9bb6ff",
  },
};

export const thalynLightTheme: editor.IStandaloneThemeData = {
  base: "vs",
  inherit: true,
  rules: [],
  colors: {
    "editor.background": "#fdfdfd",
    "editor.foreground": "#262626",
    "editorCursor.foreground": "#2c4caa",
    "editor.lineHighlightBackground": "#f3f3f3",
    "editorLineNumber.foreground": "#9a9a9a",
    "editorLineNumber.activeForeground": "#454545",
    "editor.selectionBackground": "#c8d6f5",
    "editor.inactiveSelectionBackground": "#dee5f1",
    "editorIndentGuide.background": "#e5e5e5",
    "editorIndentGuide.activeBackground": "#cfcfcf",
    "editorWhitespace.foreground": "#dcdcdc",
    "editorWidget.background": "#f3f3f3",
    "editorWidget.border": "#cfcfcf",
    "editorSuggestWidget.background": "#f3f3f3",
    "editorSuggestWidget.border": "#cfcfcf",
    "editorSuggestWidget.selectedBackground": "#c8d6f5",
    "scrollbarSlider.background": "#cfcfcf80",
    "scrollbarSlider.hoverBackground": "#a8a8a8c0",
    "scrollbarSlider.activeBackground": "#909090f0",
    focusBorder: "#2c4caa",
  },
};

/**
 * Resolve the Monaco theme key from `<html data-theme>`. `system`
 * defers to `prefers-color-scheme` so the editor matches the rest of
 * the shell at first paint and on theme cycles.
 */
export function monacoThemeForDocument(): string {
  if (typeof document === "undefined") return THALYN_DARK;
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light") return THALYN_LIGHT;
  if (attr === "dark") return THALYN_DARK;
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: light)").matches
      ? THALYN_LIGHT
      : THALYN_DARK;
  }
  return THALYN_DARK;
}
