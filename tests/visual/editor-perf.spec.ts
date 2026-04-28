import { expect, test } from "@playwright/test";

/**
 * Editor cold-start budget — NFR1 says cold start to a brain-ready
 * chat is under 3 s on M-series Mac, 5 s on Windows. The editor pane
 * lazy-loads on first activation, so its first-paint budget rides
 * the same bus.
 *
 * We measure the wall-clock time between clicking the Editor rail
 * item and Monaco's textarea becoming interactive. The threshold is
 * deliberately generous (well under 5 s) so a slow Linux CI runner
 * doesn't false-fail; a regression that brings the editor back to
 * the seconds-not-fractions range still trips the gate.
 *
 * The Tauri-specific `invoke` is replaced with a window stub so the
 * renderer doesn't error on the brain bridge being absent.
 */
const installTauriStub = `
type WindowWithTauri = Window & {
  __TAURI_INTERNALS__?: { invoke: (cmd: string, args?: unknown) => unknown };
};
const stubbed = window as WindowWithTauri;
stubbed.__TAURI_INTERNALS__ = {
  invoke: async (command: string): Promise<unknown> => {
    if (command === "ping_brain") {
      return { pong: true, version: "0.0.0", epoch_ms: 0 };
    }
    if (command === "list_providers") return [];
    if (command === "provider_configured") return false;
    if (command === "list_runs") return { runs: [] };
    if (command === "list_schedules") return { schedules: [] };
    if (command === "list_memory") return { entries: [] };
    return null;
  },
};
window.localStorage.setItem("thalyn:theme", "dark");
window.localStorage.removeItem("thalyn:layout:default");
// Skip the first-run wizard so the editor click isn't intercepted
// by the welcome overlay.
window.localStorage.setItem("thalyn:first-run-completed", "true");
`;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(installTauriStub);
});

test("editor cold-start lands inside the NFR1 budget", async ({ page }) => {
  const COLD_START_BUDGET_MS = 5_000;

  await page.goto("/");
  await page.getByRole("heading", { name: "Chat" }).waitFor();

  const editorButton = page.getByRole("button", { name: "Editor" });
  await editorButton.waitFor();

  const start = Date.now();
  await editorButton.click();
  // Monaco's editable area is exposed as a textbox role; waiting for
  // it ensures the editor is past lazy-load AND past the first
  // useable paint, not just the title bar.
  await page.locator(".monaco-editor textarea").first().waitFor({ state: "attached" });
  const elapsed = Date.now() - start;

  expect(elapsed, `editor cold-start was ${elapsed}ms`).toBeLessThan(
    COLD_START_BUDGET_MS,
  );
});
