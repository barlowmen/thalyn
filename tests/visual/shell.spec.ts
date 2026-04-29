import { expect, test } from "@playwright/test";

/**
 * Visual baselines for the chat-first shell (ADR-0026): top bar +
 * eternal chat + transient strip + composer. The drawer host is
 * exercised separately via the editor-perf spec and the Storybook
 * a11y harness.
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
    if (command === "list_runs") {
      return { runs: [] };
    }
    if (command === "get_run") {
      return null;
    }
    if (command === "get_run_tree") {
      return null;
    }
    if (command === "kill_run") {
      return { runId: "", status: "killed" };
    }
    if (command === "approve_plan") {
      return { runId: "", sessionId: "", providerId: "anthropic", status: "completed", finalResponse: "", actionLogSize: 0 };
    }
    if (command === "list_schedules") {
      return { schedules: [] };
    }
    if (command === "create_schedule") {
      return { schedule: { scheduleId: "", projectId: null, title: "", nlInput: "", cron: "* * * * *", runTemplate: { prompt: "" }, enabled: true, nextRunAtMs: null, lastRunAtMs: null, lastRunId: null, createdAtMs: 0 } };
    }
    if (command === "delete_schedule") {
      return { deleted: true, scheduleId: "" };
    }
    if (command === "translate_cron") {
      return { cron: "* * * * *", explanation: "", nlInput: "", valid: true, error: null };
    }
    if (command === "list_memory") {
      return { entries: [] };
    }
    if (command === "add_memory") {
      return { entry: { memoryId: "", projectId: null, scope: "user", kind: "fact", body: "", author: "", createdAtMs: 0, updatedAtMs: 0 } };
    }
    if (command === "update_memory") {
      return { entry: null };
    }
    if (command === "delete_memory") {
      return { deleted: true, memoryId: "" };
    }
    if (command === "provider_delta") {
      return { fromProviderId: "", toProviderId: "", changes: [] };
    }
    if (command === "lsp_start") {
      return { sessionId: "", language: "", command: [], startedAtMs: 0 };
    }
    if (command === "lsp_send") {
      return { queued: true };
    }
    if (command === "lsp_stop") {
      return { stopped: true, sessionId: "" };
    }
    if (command === "lsp_list") {
      return { sessions: [] };
    }
    if (command === "inline_suggest") {
      return {
        suggestion: "",
        requestId: "",
        requestedAtMs: 0,
        completedAtMs: 0,
        providerId: "anthropic",
        truncated: false,
      };
    }
    if (command === "terminal_open") {
      return { sessionId: "", snapshot: "" };
    }
    if (command === "terminal_input") {
      return null;
    }
    if (command === "terminal_resize") {
      return null;
    }
    if (command === "terminal_close") {
      return { closed: true, sessionId: "" };
    }
    if (command === "terminal_list") {
      return { sessions: [] };
    }
    return null;
  },
};
`;

const seedStorage = (theme: "dark" | "light" | "system") => `
window.localStorage.setItem("thalyn:theme", ${JSON.stringify(theme)});
window.localStorage.removeItem("thalyn:layout:default");
// Skip the first-run wizard so the shell renders without the welcome
// overlay covering it; visual snapshots target the steady-state UI.
window.localStorage.setItem("thalyn:first-run-completed", "true");
`;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(installTauriStub);
});

test("chat-first shell — dark", async ({ page }) => {
  await page.addInitScript(seedStorage("dark"));
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /Brain identity:/ }),
  ).toBeVisible();
  await expect(page).toHaveScreenshot("chat-first-dark.png");
});

test("chat-first shell — light", async ({ page }) => {
  await page.addInitScript(seedStorage("light"));
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /Brain identity:/ }),
  ).toBeVisible();
  await expect(page).toHaveScreenshot("chat-first-light.png");
});

test("command palette open", async ({ page }) => {
  await page.addInitScript(seedStorage("dark"));
  await page.goto("/");
  await page.getByRole("button", { name: /Brain identity:/ }).waitFor();
  await page.keyboard.press("ControlOrMeta+k");
  await expect(page.getByPlaceholder("Type a command…")).toBeVisible();
  await expect(page).toHaveScreenshot("command-palette.png");
});
