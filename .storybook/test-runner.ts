import type { TestRunnerConfig } from "@storybook/test-runner";
import { checkA11y, configureAxe, injectAxe } from "axe-playwright";

/**
 * Storybook test runner — runs every story in a Playwright browser and
 * verifies WCAG 2.1 AA compliance via axe-core. Stories that need to
 * opt out can set `parameters.a11y.disable = true`.
 */
const config: TestRunnerConfig = {
  async preVisit(page) {
    await injectAxe(page);
  },
  async postVisit(page, context) {
    const storyContext = await page.evaluate<
      Record<string, unknown>,
      string
    >((id) => {
      // @ts-expect-error: __STORYBOOK_PREVIEW__ is exposed at runtime
      return window.__STORYBOOK_PREVIEW__?.storyStoreValue?.loadStory({
        storyId: id,
      });
    }, context.id);
    const a11y = (storyContext as { parameters?: { a11y?: unknown } } | null)
      ?.parameters?.a11y as { disable?: boolean } | undefined;
    if (a11y?.disable) return;
    await configureAxe(page, {
      rules: [{ id: "region", enabled: false }],
    });
    await checkA11y(page, "#storybook-root", {
      detailedReport: true,
      detailedReportOptions: { html: true },
      axeOptions: {
        runOnly: {
          type: "tag",
          values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"],
        },
      },
    });
  },
};

export default config;
