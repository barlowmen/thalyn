import type { Meta, StoryObj } from "@storybook/react-vite";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";

const meta = {
  title: "UI/Button",
  component: Button,
  parameters: { layout: "centered" },
  tags: ["autodocs"],
  argTypes: {
    variant: {
      control: "select",
      options: ["default", "secondary", "outline", "ghost", "link", "destructive"],
    },
    size: {
      control: "select",
      options: ["default", "sm", "lg", "icon"],
    },
    disabled: { control: "boolean" },
  },
  args: {
    children: "Approve plan",
    variant: "default",
    size: "default",
  },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Secondary: Story = {
  args: { variant: "secondary", children: "Edit plan" },
};

export const Outline: Story = {
  args: { variant: "outline", children: "Cancel" },
};

export const Destructive: Story = {
  args: { variant: "destructive", children: "Reject run" },
};

export const Ghost: Story = {
  args: { variant: "ghost", children: "Dismiss" },
};

export const WithIcon: Story = {
  args: {
    children: (
      <>
        <Plus aria-hidden /> New project
      </>
    ),
  },
};

export const IconOnly: Story = {
  args: {
    size: "icon",
    variant: "ghost",
    "aria-label": "Delete",
    children: <Trash2 aria-hidden />,
  },
};

export const Disabled: Story = {
  args: { disabled: true, children: "Pinging…" },
};
