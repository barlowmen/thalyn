import { GripVertical } from "lucide-react";
import { Group, Panel, Separator } from "react-resizable-panels";

import { cn } from "@/lib/utils";

/**
 * Thin wrapper around react-resizable-panels v4. Re-exports the
 * `Group` / `Panel` / `Separator` primitives unchanged so callers can
 * drive layout, persistence, and collapse behaviour directly, while
 * defaulting the `Separator` chrome to a soft 1 px line that uses our
 * `--border` token.
 */

const ResizableGroup = ({
  className,
  orientation = "horizontal",
  ...props
}: React.ComponentProps<typeof Group>) => (
  <Group
    orientation={orientation}
    className={cn(
      "flex h-full w-full",
      orientation === "vertical" && "flex-col",
      className,
    )}
    {...props}
  />
);

const ResizablePanel = Panel;

const ResizableSeparator = ({
  withHandle,
  orientation = "horizontal",
  className,
  ...props
}: React.ComponentProps<typeof Separator> & {
  withHandle?: boolean;
  orientation?: "horizontal" | "vertical";
}) => (
  <Separator
    className={cn(
      "relative flex items-center justify-center bg-border",
      orientation === "horizontal" ? "w-px cursor-col-resize" : "h-px w-full cursor-row-resize",
      // Invisible hit zone wider than the visible 1 px line so the
      // separator is grabbable without pixel-precise aim.
      "after:absolute after:bg-transparent",
      orientation === "horizontal"
        ? "after:inset-y-0 after:left-1/2 after:w-3 after:-translate-x-1/2"
        : "after:inset-x-0 after:top-1/2 after:h-3 after:-translate-y-1/2",
      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-offset-1",
      "data-[hovered=true]:bg-ring",
      className,
    )}
    {...props}
  >
    {withHandle && (
      <div className="z-10 flex h-4 w-3 items-center justify-center rounded-sm border border-border bg-border">
        <GripVertical className="h-2.5 w-2.5" aria-hidden />
      </div>
    )}
  </Separator>
);

export { ResizableGroup, ResizablePanel, ResizableSeparator };
