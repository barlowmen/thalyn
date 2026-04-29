import type { Meta, StoryObj } from "@storybook/react-vite";

import { LeadChatSurface } from "@/components/lead/lead-chat-surface";
import { ThemeProvider } from "@/components/theme-provider";
import type { Message } from "@/components/chat/types";

const NOW = Date.now();

const SEED: Message[] = [
  {
    id: "m_1",
    role: "user",
    text: "Lead-Thalyn, what's the status on the auth refactor?",
    atMs: NOW - 5 * 60_000,
  },
  {
    id: "m_2",
    role: "assistant",
    segments: [
      {
        kind: "text",
        text:
          "I have 3 open questions before I can land the next slice — the worker drawer should help you scan them. Here's the current take:\n\n1. Session boundaries — current behaviour vs. proposed.\n2. Whether the legacy helper retires in this slice or the next.\n3. Whether to bundle the test fixtures or split them.",
      },
    ],
    model: "claude-sonnet-4-6",
    done: true,
    atMs: NOW - 4 * 60_000,
  },
  {
    id: "m_3",
    role: "user",
    text: "Bundle the test fixtures with this slice.",
    atMs: NOW - 90_000,
  },
];

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="h-[640px] w-[480px] border border-border bg-background">
        {children}
      </div>
    </ThemeProvider>
  );
}

const meta = {
  title: "Lead/LeadChatSurface",
  component: LeadChatSurface,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <Frame>
        <Story />
      </Frame>
    ),
  ],
  args: {
    agentId: "lead_thalyn",
    displayName: "Lead-Thalyn",
    staticMessages: SEED,
  },
} satisfies Meta<typeof LeadChatSurface>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Active: Story = {};

export const Empty: Story = {
  args: { staticMessages: [] },
};
