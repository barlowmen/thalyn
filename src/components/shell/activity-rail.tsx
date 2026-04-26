import {
  Compass,
  type LucideIcon,
  MessagesSquare,
  Plug,
  ScrollText,
  Settings,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type RailItem = {
  id: string;
  label: string;
  icon: LucideIcon;
};

/**
 * The activity rail is the leftmost surface of the shell — a fixed
 * ~56 px column of large icon buttons. v0.2 ships placeholder
 * destinations; real navigation lands as the surfaces come online.
 */
const ITEMS: readonly RailItem[] = [
  { id: "chat", label: "Chat", icon: MessagesSquare },
  { id: "agents", label: "Agents", icon: Compass },
  { id: "logs", label: "Logs", icon: ScrollText },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "settings", label: "Settings", icon: Settings },
] as const;

export function ActivityRail({
  active,
  onSelect,
  className,
}: {
  active?: string;
  onSelect?: (id: string) => void;
  className?: string;
}) {
  return (
    <nav
      aria-label="Primary"
      className={cn(
        "flex h-full w-(--rail-width) flex-col items-center gap-1 border-r border-border bg-surface py-3",
        className,
      )}
      style={{ width: "var(--rail-width)" }}
    >
      {ITEMS.map(({ id, label, icon: Icon }) => {
        const isActive = active === id;
        return (
          <Button
            key={id}
            variant="ghost"
            size="icon"
            aria-label={label}
            aria-current={isActive ? "page" : undefined}
            onClick={() => onSelect?.(id)}
            className={cn(
              "h-10 w-10 rounded-md text-muted-foreground hover:text-foreground",
              isActive && "bg-accent text-foreground",
            )}
          >
            <Icon aria-hidden />
          </Button>
        );
      })}
    </nav>
  );
}
