import type { Meta, StoryObj } from "@storybook/react-vite";

import { MessageList } from "@/components/chat/message-list";
import type { Message } from "@/components/chat/types";

const meta: Meta<typeof MessageList> = {
  title: "Chat/MessageList",
  component: MessageList,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[480px] w-[640px] bg-background">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof MessageList>;

const userMessage: Message = {
  id: "m_user_1",
  role: "user",
  text: "Sam, status on the auth refactor?",
};

const directReply: Message = {
  id: "m_assistant_direct",
  role: "assistant",
  segments: [
    {
      kind: "text",
      text: "Three commits shipped overnight; one open question waiting on you.",
    },
  ],
  model: "claude-sonnet-4-6",
  done: true,
};

const delegatedReply: Message = {
  id: "m_assistant_delegated",
  role: "assistant",
  segments: [
    {
      kind: "text",
      text: "Asking Sam now…\n\nSam says: three commits shipped overnight; one open question waiting on you.",
    },
  ],
  model: "thalyn-relay",
  done: true,
  leadAttribution: {
    agentId: "agent_lead_alpha",
    displayName: "Sam",
  },
};

export const DirectReply: Story = {
  args: {
    messages: [userMessage, directReply],
  },
};

export const DelegatedReplyWithLeadChip: Story = {
  args: {
    messages: [userMessage, delegatedReply],
  },
};
