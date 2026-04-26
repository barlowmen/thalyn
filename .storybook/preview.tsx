import type { Preview } from "@storybook/react-vite";
import type { ReactElement } from "react";

import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";

import "../src/styles/globals.css";

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
    backgrounds: { disable: true },
    a11y: {
      config: {
        rules: [
          // Storybook's iframe doesn't always have a landmark, which
          // is fine for component-level stories. Disable the lint that
          // would otherwise flag every story.
          { id: "region", enabled: false },
        ],
      },
    },
  },
  globalTypes: {
    theme: {
      description: "Active design-token theme",
      defaultValue: "dark",
      toolbar: {
        title: "Theme",
        icon: "circlehollow",
        items: [
          { value: "dark", title: "Dark" },
          { value: "light", title: "Light" },
          { value: "system", title: "System" },
        ],
        dynamicTitle: true,
      },
    },
  },
  decorators: [
    (Story, context): ReactElement => {
      const theme = (context.globals.theme as string) ?? "dark";
      if (typeof document !== "undefined") {
        document.documentElement.setAttribute("data-theme", theme);
      }
      return (
        <div className="bg-background text-foreground p-6">
          <Story />
        </div>
      );
    },
  ],
};

export default preview;
