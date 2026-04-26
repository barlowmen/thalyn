import { Monitor, Moon, Sun } from "lucide-react";

import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const THEME_LABEL = {
  dark: "Dark",
  light: "Light",
  system: "System",
} as const;

const THEME_ICON = {
  dark: Moon,
  light: Sun,
  system: Monitor,
} as const;

/**
 * Cycles through dark → light → system on click. Aria-pressed toggles
 * across the three options so screen-readers report the current state
 * even though the icon changes too.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, cycleTheme } = useTheme();
  const Icon = THEME_ICON[theme];
  const label = `Theme: ${THEME_LABEL[theme]} (click to cycle)`;

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={cycleTheme}
      aria-label={label}
      title={label}
      className={cn(
        "h-10 w-10 rounded-md text-muted-foreground hover:text-foreground",
        className,
      )}
    >
      <Icon aria-hidden />
    </Button>
  );
}
