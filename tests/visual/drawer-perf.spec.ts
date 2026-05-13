import { expect, test } from "@playwright/test";

/**
 * Drawer-open performance budgets — NFR1's "drawer open animation
 * ≤ 200 ms" and the F2.4 invariant that the direct lead-chat drawer
 * is a primary surface (history loads instantly, no slower than the
 * brain's own reply path).
 *
 * Linux CI is meaningfully slower than the M-series Mac the headline
 * budget is set against; the thresholds here are generous enough to
 * absorb CI noise (~5× the spec target for the tools-drawer animation,
 * ~3× for the lead-chat cold-mount) while still tripping on a
 * structural regression.
 *
 * Like the editor-perf spec, we stub Tauri's ``invoke`` so the
 * renderer doesn't error on the missing brain bridge.
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
    if (command === "list_lead_runs") return { runs: [] };
    if (command === "list_lead_turns") return { turns: [] };
    return null;
  },
};
window.localStorage.setItem("thalyn:theme", "dark");
window.localStorage.removeItem("thalyn:layout:default");
// Skip the first-run wizard so drawer dispatches aren't intercepted
// by the welcome overlay.
window.localStorage.setItem("thalyn:first-run-completed", "true");
`;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(installTauriStub);
});

test("logs drawer opens inside the NFR1 animation budget", async ({ page }) => {
  // NFR1 spec: ≤ 200 ms on M-series Mac. CI multiplier ≈ 5× to absorb
  // GitHub-runner noise; the gate's job is catching the day a drawer
  // grows a heavy mount, not measuring the absolute headline number.
  const DRAWER_OPEN_BUDGET_MS = 1_000;

  await page.goto("/");
  await page.getByRole("button", { name: /Brain identity:/ }).waitFor();

  const start = Date.now();
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("thalyn:tools-open", { detail: { kind: "logs" } }),
    ),
  );
  await page
    .getByRole("region", { name: /Logs drawer/ })
    .waitFor({ state: "visible" });
  const elapsed = Date.now() - start;

  expect(elapsed, `logs drawer open took ${elapsed}ms`).toBeLessThan(
    DRAWER_OPEN_BUDGET_MS,
  );
});

test("lead-chat drawer cold-mounts inside the F2.4 budget", async ({ page }) => {
  // F2.4: direct lead chat is a primary surface — history must load
  // instantly. The drawer mounts the lead-chat surface and pulls run
  // metadata; on the stubbed bridge the metadata is empty, so this
  // measures the React-tree cost alone. The budget catches a heavy
  // mount or a synchronous brain round-trip slipping into the path.
  const LEAD_CHAT_COLD_MOUNT_BUDGET_MS = 1_500;

  await page.goto("/");
  await page.getByRole("button", { name: /Brain identity:/ }).waitFor();

  const start = Date.now();
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("thalyn:tools-open", {
        detail: {
          kind: "lead-chat",
          params: { agentId: "agent_lead_perf", displayName: "Sam" },
        },
      }),
    ),
  );
  await page
    .getByRole("region", { name: /Lead chat drawer/ })
    .waitFor({ state: "visible" });
  const elapsed = Date.now() - start;

  expect(
    elapsed,
    `lead-chat drawer cold mount took ${elapsed}ms`,
  ).toBeLessThan(LEAD_CHAT_COLD_MOUNT_BUDGET_MS);
});
