import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

// Coloured tones use the tone for the border + tinted bg, but keep
// the foreground at the high-contrast text token. The earlier shape
// (`text-warning` etc.) gave ~3.5:1 contrast on small text against
// the tinted bg, which fails WCAG 2.1 AA — axe flagged it on the
// browser surface stories. Keeping the bg/border tinted preserves
// the colour cue without leaning on text colour for legibility.
const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      tone: {
        default: "border-border bg-secondary text-secondary-foreground",
        success: "border-success/40 bg-success/15 text-foreground",
        warning: "border-warning/40 bg-warning/15 text-foreground",
        danger: "border-destructive/40 bg-destructive/15 text-foreground",
        muted: "border-border bg-muted text-muted-foreground",
      },
    },
    defaultVariants: {
      tone: "default",
    },
  },
);

type BadgeProps = React.HTMLAttributes<HTMLSpanElement> &
  VariantProps<typeof badgeVariants>;

function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

export { Badge, badgeVariants, type BadgeProps };
