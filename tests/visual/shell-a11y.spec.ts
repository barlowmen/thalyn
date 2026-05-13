import { expect, test } from "@playwright/test";
import { checkA11y, configureAxe, injectAxe } from "axe-playwright";

/**
 * Integrated WCAG 2.1 AA audit for the chat-first shell — the
 * Storybook harness covers every component in isolation (73 stories
 * at the time of writing), and this spec covers how they compose at
 * the application level. NFR8 lists chat, lead chat, drawers, and
 * plan tree as the primary surfaces; each is exercised here through
 * the same brain-event dispatch path the renderer uses at runtime.
 *
 * Runs in the existing visual-regression Playwright job so a11y
 * regressions trip the same CI gate as visual regressions.
 *
 * The init script is plain JavaScript on purpose: Playwright injects
 * the string into the page as-is, and TypeScript syntax silently
 * fails to parse — dropping the Tauri stub and the localStorage
 * seeds with it. Use plain ``var`` / ``function`` here even when the
 * rest of the spec is TS.
 */
const INIT_SCRIPT = `
(function () {
  window.__TAURI_INTERNALS__ = {
    invoke: function (command) {
      if (command === "ping_brain") {
        return Promise.resolve({ pong: true, version: "0.0.0", epoch_ms: 0 });
      }
      if (command === "list_providers") return Promise.resolve([]);
      if (command === "provider_configured") return Promise.resolve(false);
      if (command === "list_runs") return Promise.resolve({ runs: [] });
      if (command === "list_schedules") return Promise.resolve({ schedules: [] });
      if (command === "list_memory") return Promise.resolve({ entries: [] });
      if (command === "list_lead_runs") return Promise.resolve({ runs: [] });
      if (command === "list_lead_turns") return Promise.resolve({ turns: [] });
      return Promise.resolve(null);
    },
  };
  window.localStorage.removeItem("thalyn:layout:default");
  window.localStorage.setItem("thalyn:first-run-completed", "true");
})();
`;

async function audit(page: import("@playwright/test").Page) {
  await injectAxe(page);
  await configureAxe(page, {
    // The shell deliberately uses one <main> region; axe's "region"
    // rule wants every element inside a landmark, which is over-strict
    // for nested transient surfaces (the same exception the Storybook
    // test-runner applies).
    rules: [{ id: "region", enabled: false }],
  });
  await checkA11y(page, undefined, {
    detailedReport: true,
    detailedReportOptions: { html: true },
    axeOptions: {
      runOnly: {
        type: "tag",
        values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
      },
    },
  });
}

function seedTheme(theme: "dark" | "light") {
  return `window.localStorage.setItem("thalyn:theme", ${JSON.stringify(theme)});`;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(INIT_SCRIPT);
});

for (const theme of ["dark", "light"] as const) {
  test(`chat-first shell idle — ${theme}`, async ({ page }) => {
    await page.addInitScript(seedTheme(theme));
    await page.goto("/");
    await expect(
      page.getByRole("button", { name: /Brain identity:/ }),
    ).toBeVisible();
    await audit(page);
  });
}

test("chat-first shell with logs drawer open — dark", async ({ page }) => {
  await page.addInitScript(seedTheme("dark"));
  await page.goto("/");
  await page.getByRole("button", { name: /Brain identity:/ }).waitFor();
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("thalyn:tools-open", { detail: { kind: "logs" } }),
    ),
  );
  await page
    .getByRole("region", { name: /Logs drawer/ })
    .waitFor({ state: "visible" });
  await audit(page);
});

test("chat-first shell with lead-chat drawer open — dark", async ({
  page,
}) => {
  await page.addInitScript(seedTheme("dark"));
  await page.goto("/");
  await page.getByRole("button", { name: /Brain identity:/ }).waitFor();
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("thalyn:tools-open", {
        detail: {
          kind: "lead-chat",
          params: { agentId: "agent_lead_a11y", displayName: "Sam" },
        },
      }),
    ),
  );
  await page
    .getByRole("region", { name: /Lead chat drawer/ })
    .waitFor({ state: "visible" });
  await audit(page);
});

test("command palette open — dark", async ({ page }) => {
  await page.addInitScript(seedTheme("dark"));
  await page.goto("/");
  await page.getByRole("button", { name: /Brain identity:/ }).waitFor();
  await page.keyboard.press("ControlOrMeta+k");
  await expect(page.getByPlaceholder("Type a command…")).toBeVisible();
  // cmdk renders the group headings through a Radix Portal; the
  // portal mount + Tailwind class application can lag under parallel
  // workers, so axe occasionally samples the dialog before the dark
  // palette has been applied. Wait for the heading text to land in
  // the DOM at the expected colour before running the audit.
  await expect(
    page.locator("[cmdk-group-heading]").first(),
  ).toBeVisible();
  await page.waitForFunction(() => {
    const head = document.querySelector("[cmdk-group-heading]");
    if (!(head instanceof HTMLElement)) return false;
    const color = window.getComputedStyle(head).color;
    // The dark palette resolves --text-muted to a high-L greyscale;
    // light palette resolves it to a low-L greyscale. A simple
    // luminance gate avoids re-parsing the colour value.
    const match = color.match(/oklch\(([0-9.]+)/);
    return match ? Number(match[1]) > 0.5 : false;
  });
  await audit(page);
});
