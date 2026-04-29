import {
  Code2,
  FolderTree,
  Inbox,
  type LucideIcon,
  Monitor,
  Moon,
  Plug,
  RotateCw,
  ScrollText,
  Settings as SettingsIcon,
  Sun,
  Terminal as TerminalIcon,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { useTheme } from "@/components/theme-provider";
import {
  type DrawerKind,
  useDrawerHost,
} from "@/components/shell/drawer-host";
import { COMMAND_PALETTE_OPEN_EVENT } from "@/components/shell/top-bar";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";

type Action = {
  id: string;
  label: string;
  icon: LucideIcon;
  shortcut?: string;
  group: "Theme" | "App" | "Drawer";
  run: () => void;
};

const DRAWER_OPEN_ITEMS: ReadonlyArray<{
  kind: DrawerKind;
  label: string;
  icon: LucideIcon;
}> = [
  { kind: "editor", label: "Open editor", icon: Code2 },
  { kind: "terminal", label: "Open terminal", icon: TerminalIcon },
  { kind: "email", label: "Open email", icon: Inbox },
  { kind: "file-tree", label: "Open files", icon: FolderTree },
  { kind: "connectors", label: "Open connectors", icon: Plug },
  { kind: "logs", label: "Open logs", icon: ScrollText },
];

/**
 * The command palette opens with Cmd-K (or Ctrl-K). Every action
 * exposed in menus is also addressable here per F11.2; the chat-first
 * shell relies on the palette as its primary nav surface (F8.6).
 */
export function CommandPalette({
  onOpenSettings,
}: {
  onOpenSettings?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { setTheme } = useTheme();
  const drawerHost = useDrawerHost();
  const hasOpenDrawers = drawerHost.visible.length > 0;

  // Cmd-K (macOS) / Ctrl-K (everywhere else) toggles the palette.
  // The chat-first top bar's keyboard-shortcut chip dispatches the
  // open event so the user can reach the palette by mouse too without
  // forcing every caller to know how to synthesise a keypress.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const isToggle =
        event.key === "k" && (event.metaKey || event.ctrlKey);
      if (!isToggle) return;
      event.preventDefault();
      setOpen((current) => !current);
    };
    const onOpenEvent = () => setOpen(true);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpenEvent);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpenEvent);
    };
  }, []);

  const close = () => setOpen(false);

  const drawerActions: Action[] = DRAWER_OPEN_ITEMS.map((item) => ({
    id: `drawer.open.${item.kind}`,
    label: item.label,
    icon: item.icon,
    group: "Drawer",
    run: () => drawerHost.open({ kind: item.kind }),
  }));

  if (hasOpenDrawers) {
    drawerActions.push({
      id: "drawer.closeAll",
      label: "Close all drawers",
      icon: X,
      group: "Drawer",
      shortcut: "⌘\\",
      run: () => drawerHost.closeAll(),
    });
  }

  const actions: Action[] = [
    ...drawerActions,
    {
      id: "theme.dark",
      label: "Theme: Dark",
      icon: Moon,
      group: "Theme",
      run: () => setTheme("dark"),
    },
    {
      id: "theme.light",
      label: "Theme: Light",
      icon: Sun,
      group: "Theme",
      run: () => setTheme("light"),
    },
    {
      id: "theme.system",
      label: "Theme: Follow system",
      icon: Monitor,
      group: "Theme",
      run: () => setTheme("system"),
    },
    {
      id: "app.openSettings",
      label: "Open settings…",
      icon: SettingsIcon,
      group: "App",
      run: () => onOpenSettings?.(),
    },
    {
      id: "app.reload",
      label: "Reload window",
      icon: RotateCw,
      group: "App",
      run: () => window.location.reload(),
    },
  ];

  const grouped = actions.reduce<Record<string, Action[]>>((acc, action) => {
    const bucket = acc[action.group] ?? [];
    bucket.push(action);
    acc[action.group] = bucket;
    return acc;
  }, {});

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Type a command…" />
      <CommandList>
        <CommandEmpty>No matching action.</CommandEmpty>
        {Object.entries(grouped).map(([group, items]) => (
          <CommandGroup key={group} heading={group}>
            {items.map((action) => {
              const Icon = action.icon;
              return (
                <CommandItem
                  key={action.id}
                  value={action.label}
                  onSelect={() => {
                    action.run();
                    close();
                  }}
                >
                  <Icon aria-hidden />
                  <span>{action.label}</span>
                  {action.shortcut && (
                    <CommandShortcut>{action.shortcut}</CommandShortcut>
                  )}
                </CommandItem>
              );
            })}
          </CommandGroup>
        ))}
      </CommandList>
    </CommandDialog>
  );
}
