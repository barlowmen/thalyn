import type { Meta, StoryObj } from "@storybook/react-vite";

import { ThreadDigestGreeting } from "@/components/chat/thread-digest-greeting";
import type { SessionDigest } from "@/lib/threads";

const meta: Meta<typeof ThreadDigestGreeting> = {
  title: "Chat/ThreadDigestGreeting",
  component: ThreadDigestGreeting,
  parameters: { layout: "padded" },
  decorators: [
    (Story) => (
      <div className="w-[640px] bg-background p-6">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ThreadDigestGreeting>;

const TODAY_MS = new Date("2026-04-29T10:00:00Z").getTime();
const YESTERDAY_MS = new Date("2026-04-28T16:30:00Z").getTime();

const richDigest: SessionDigest = {
  digestId: "digest_demo",
  threadId: "thread_self",
  windowStartMs: YESTERDAY_MS - 3 * 60 * 60 * 1000,
  windowEndMs: YESTERDAY_MS,
  structuredSummary: {
    topics: ["auth-backend split", "Thalyn identity", "first-run wizard"],
    decisions: [
      "ADR-0020 lands as Proposed",
      "Provider hot-swap on auth.set",
    ],
    open_threads: ["Day-divider digest UI", "Architecture review"],
  },
  secondLevelSummaryOf: null,
};

const sparseDigest: SessionDigest = {
  digestId: "digest_sparse",
  threadId: "thread_self",
  windowStartMs: YESTERDAY_MS - 60 * 60 * 1000,
  windowEndMs: YESTERDAY_MS,
  structuredSummary: {
    topics: [],
    decisions: [],
    open_threads: [],
  },
  secondLevelSummaryOf: null,
};

export const FullDigestSinceYesterday: Story = {
  args: {
    nowMs: TODAY_MS,
    preview: { digest: richDigest, lastTurnAtMs: YESTERDAY_MS },
  },
};

export const EmptyDigestSinceYesterday: Story = {
  args: {
    nowMs: TODAY_MS,
    preview: { digest: sparseDigest, lastTurnAtMs: YESTERDAY_MS },
  },
};

export const LastTurnWasToday_NoGreeting: Story = {
  args: {
    nowMs: TODAY_MS,
    preview: {
      digest: richDigest,
      lastTurnAtMs: new Date("2026-04-29T08:00:00Z").getTime(),
    },
  },
};

export const NoPriorTurns_NoGreeting: Story = {
  args: {
    nowMs: TODAY_MS,
    preview: { digest: richDigest, lastTurnAtMs: null },
  },
};

export const NoDigestYet_NoGreeting: Story = {
  args: {
    nowMs: TODAY_MS,
    preview: { digest: null, lastTurnAtMs: YESTERDAY_MS },
  },
};

const multiProjectDigest: SessionDigest = {
  digestId: "digest_multi",
  threadId: "thread_self",
  windowStartMs: YESTERDAY_MS - 4 * 60 * 60 * 1000,
  windowEndMs: YESTERDAY_MS,
  structuredSummary: {
    topics: ["mixed activity"],
    decisions: [],
    open_threads: [],
    project_breakdown: [
      {
        projectId: "proj_thalyn",
        projectName: "Thalyn",
        projectSlug: "thalyn",
        topics: ["auth refactor", "first-run wizard"],
        decisions: ["land tonight"],
        open_threads: ["doc the rollback"],
      },
      {
        projectId: "proj_taxprep",
        projectName: "Tax Prep 2026",
        projectSlug: "tax-prep-2026",
        topics: ["1099 logging"],
        decisions: [],
        open_threads: ["chase missing W-2"],
      },
      {
        projectId: "proj_offsite",
        projectName: "Q3 offsite",
        projectSlug: "q3-offsite",
        topics: ["venue shortlist"],
        decisions: [],
        open_threads: [],
      },
    ],
  },
  secondLevelSummaryOf: null,
};

export const MultiProjectBreakdown: Story = {
  args: {
    nowMs: TODAY_MS,
    preview: { digest: multiProjectDigest, lastTurnAtMs: YESTERDAY_MS },
  },
};
