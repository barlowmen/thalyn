import type { Meta, StoryObj } from "@storybook/react-vite";

import { Composer } from "@/components/chat/composer";
import { ThemeProvider } from "@/components/theme-provider";

const meta = {
  title: "Chat/Composer",
  component: Composer,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <ThemeProvider>
        <div className="min-h-[160px] bg-background">
          <Story />
        </div>
      </ThemeProvider>
    ),
  ],
  args: {
    onSubmit: () => undefined,
  },
} satisfies Meta<typeof Composer>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Compact: Story = {
  args: { size: "compact" },
};

export const Roomy: Story = {
  args: { size: "roomy" },
};

export const Disabled: Story = {
  args: {
    size: "roomy",
    disabled: true,
    placeholder: "Add an Anthropic API key in Settings to enable chat.",
  },
};
