import { expect, test } from "@playwright/test";

/**
 * Visual baselines for the v0.3 walking-skeleton shell with the chat
 * surface in the main panel.
 *
 * The Tauri-specific `invoke` is replaced with a window stub so the
 * renderer doesn't error out when the brain bridge is absent. We're
 * locking layout, tokens, theme, and the chat surface's empty state —
 * the brain round-trip is exercised by the Rust + Python suites.
 */
const installTauriStub = `
type Pong = { pong: boolean; version: string; epoch_ms: number };
type Provider = {
  id: string;
  displayName: string;
  kind: string;
  defaultModel: string;
  capabilityProfile: Record<string, unknown>;
  configured: boolean;
  enabled: boolean;
};
type WindowWithTauri = Window & {
  __TAURI_INTERNALS__?: { invoke: (cmd: string, args?: unknown) => unknown };
};
const stubbed = window as WindowWithTauri;
const PROVIDERS: Provider[] = [
  {
    id: "anthropic",
    displayName: "Anthropic — Claude Sonnet 4.6",
    kind: "anthropic",
    defaultModel: "claude-sonnet-4-6",
    capabilityProfile: {
      maxContextTokens: 200000,
      supportsToolUse: true,
      toolUseReliability: "high",
      supportsVision: true,
      supportsStreaming: true,
      local: false,
    },
    configured: false,
    enabled: true,
  },
];
stubbed.__TAURI_INTERNALS__ = {
  invoke: async (command: string): Promise<unknown> => {
    if (command === "ping_brain") {
      return { pong: true, version: "0.0.0", epoch_ms: 0 } as Pong;
    }
    if (command === "list_providers") {
      return PROVIDERS;
    }
    if (command === "provider_configured") {
      return false;
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
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();
  await expect(page).toHaveScreenshot("dark-shell.png");
});

test("light shell — default layout", async ({ page }) => {
  await page.addInitScript(seedStorage("light"));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();
  await expect(page).toHaveScreenshot("light-shell.png");
});

test("command palette open", async ({ page }) => {
  await page.addInitScript(seedStorage("dark"));
  await page.goto("/");
  await page.getByRole("heading", { name: "Chat" }).waitFor();
  await page.keyboard.press("ControlOrMeta+k");
  await expect(page.getByPlaceholder("Type a command…")).toBeVisible();
  await expect(page).toHaveScreenshot("command-palette.png");
});
