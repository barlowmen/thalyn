import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for visual regression of the renderer.
 *
 * The desktop runtime (Tauri) is not in scope here — we exercise the
 * web renderer through Vite's preview server, which is enough to lock
 * the design tokens, panel layout, theme switching, and command
 * palette against unintended drift.
 *
 * Snapshots are stored under `tests/visual/__screenshots__/` keyed on
 * the project name plus OS, so a CI baseline (Linux) and a developer
 * baseline (macOS) coexist without clobbering each other.
 */
export default defineConfig({
  testDir: "tests/visual",
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    command: "pnpm preview --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
      animations: "disabled",
    },
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  snapshotPathTemplate:
    "{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}-{platform}{ext}",
});
