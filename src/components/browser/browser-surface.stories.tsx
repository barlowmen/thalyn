import type { Meta, StoryObj } from "@storybook/react-vite";

import { BrowserView } from "@/components/browser/browser-surface";

const meta: Meta<typeof BrowserView> = {
  title: "Browser/Surface",
  component: BrowserView,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[640px] w-[820px] bg-background">
        <Story />
      </div>
    ),
  ],
  args: {
    busy: false,
    error: null,
    onStart: () => undefined,
    onStop: () => undefined,
  },
};

export default meta;
type Story = StoryObj<typeof BrowserView>;

export const Idle: Story = {
  args: {
    state: { kind: "idle" },
  },
};

export const Starting: Story = {
  args: {
    state: { kind: "starting", binary: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" },
  },
};

export const Running: Story = {
  args: {
    state: {
      kind: "running",
      binary: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      ws_url: "ws://127.0.0.1:53219/devtools/browser/d8a6-23-9c-3f-eb",
      profile_dir: "/Users/example/Library/Application Support/Thalyn/chromium-profile",
    },
  },
};

export const Exited: Story = {
  args: {
    state: { kind: "exited", reason: "stopped by user" },
  },
};

export const ErrorState: Story = {
  args: {
    state: { kind: "idle" },
    error:
      "no Chromium-family browser found; install Google Chrome, Chromium, Microsoft Edge, or Brave",
  },
};
