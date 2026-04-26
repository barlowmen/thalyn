import { expect, test } from "@playwright/test";

/**
 * Visual baselines for the v0.2 walking-skeleton shell.
 *
 * The Tauri-specific `invoke` is replaced by a window stub so the
 * renderer doesn't error out when the brain bridge is absent. We're
 * locking layout, tokens, and theme — the brain round-trip is
 * exercised elsewhere.
 */
const installTauriStub = `
type InvokeResult = { pong: boolean; version: string; epoch_ms: number } | null;
type WindowWithTauri = Window & {
  __TAURI_INTERNALS__?: { invoke: (cmd: string) => Promise<InvokeResult> };
};
const stubbed = window as WindowWithTauri;
stubbed.__TAURI_INTERNALS__ = {
  invoke: async (command: string): Promise<InvokeResult> => {
    if (command === "ping_brain") {
      return { pong: true, version: "0.0.0", epoch_ms: 0 };
    }
    return null;
  },
};
`;

const seedStorage = (theme: "dark" | "light" | "system") => `
window.localStorage.setItem("thalyn:theme", ${JSON.stringify(theme)});
window.localStorage.removeItem("thalyn:layout:default");
`;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(installTauriStub);
});

test("dark shell — default layout", async ({ page }) => {
  await page.addInitScript(seedStorage("dark"));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Thalyn" })).toBeVisible();
  await expect(page).toHaveScreenshot("dark-shell.png");
});

test("light shell — default layout", async ({ page }) => {
  await page.addInitScript(seedStorage("light"));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Thalyn" })).toBeVisible();
  await expect(page).toHaveScreenshot("light-shell.png");
});

test("command palette open", async ({ page }) => {
  await page.addInitScript(seedStorage("dark"));
  await page.goto("/");
  await page.getByRole("heading", { name: "Thalyn" }).waitFor();
  await page.keyboard.press("ControlOrMeta+k");
  await expect(page.getByPlaceholder("Type a command…")).toBeVisible();
  await expect(page).toHaveScreenshot("command-palette.png");
});
