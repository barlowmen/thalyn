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
    url: "",
    onUrlChange: () => undefined,
    onStart: () => undefined,
    onStop: () => undefined,
    onSubmit: () => undefined,
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
    state: { kind: "starting", profile_dir: "/Users/me/.thalyn/cef-profile" },
    busy: true,
  },
};

export const Running: Story = {
  args: {
    state: {
      kind: "running",
      ws_url: "ws://127.0.0.1:53219/devtools/browser/1234abcd",
      profile_dir: "/Users/me/.thalyn/cef-profile",
      sdk_version: "147.1.0+147.0.10",
    },
    url: "https://example.com/dashboard",
  },
};

export const Exited: Story = {
  args: {
    state: { kind: "exited", reason: "child exited with code Some(1)" },
  },
};

export const WithError: Story = {
  args: {
    state: { kind: "idle" },
    error:
      "could not locate the thalyn-cef-host binary: no thalyn-cef-host binary at /usr/local/bin/thalyn-cef-host and THALYN_CEF_HOST_BIN is unset",
  },
};
