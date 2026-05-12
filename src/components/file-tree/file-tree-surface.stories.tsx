import type { Meta, StoryObj } from "@storybook/react-vite";

import { ThemeProvider } from "@/components/theme-provider";
import { FileTreeSurface } from "@/components/file-tree/file-tree-surface";

const meta = {
  title: "File-Tree/FileTreeSurface",
  component: FileTreeSurface,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <ThemeProvider>
        <div className="h-[480px] w-[480px] bg-background">
          <Story />
        </div>
      </ThemeProvider>
    ),
  ],
} satisfies Meta<typeof FileTreeSurface>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {};

export const WithRoot: Story = {
  args: { root: "/Users/me/projects/example" },
};
