import {
  ChevronDown,
  Command as CommandIcon,
  Settings as SettingsIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  emitActiveProviderChange,
  writeActiveProvider,
} from "@/lib/active-provider";
import {
  isProviderConfigured,
  listProviders,
  type ProviderMeta,
} from "@/lib/providers";
import { cn } from "@/lib/utils";

export const COMMAND_PALETTE_OPEN_EVENT = "thalyn:command-palette-open";

/**
 * Fire the global custom event the command palette listens for. The
 * top bar's keyboard-shortcut chip dispatches this; the palette also
 * still binds the bare ⌘K / Ctrl-K shortcut, so this event is purely
 * additive.
 */
export function dispatchOpenCommandPalette(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(COMMAND_PALETTE_OPEN_EVENT));
}

type Props = {
  /** The brain's display name. F8.5 anchors the badge with the brain
   *  name on the left ("◉ Thalyn · …"); the project switcher pill in
   *  the centre carries the active project name. */
  brainName: string;
  /** Active provider id (kept in parent state so settings can drive
   *  it). The badge calls ``listProviders`` to resolve display name +
   *  the provider list when the popover opens. */
  activeProviderId: string;
  /** Whether the active provider has a key on file. ``null`` means
   *  the check is still pending; the badge tones itself accordingly. */
  configured: boolean | null;
  /** Active project's display name. Single-project for now; v0.30
   *  introduces the multi-project popover. */
  projectName: string;
  /** Open the settings dialog (the cog on the right). */
  onOpenSettings: () => void;
  /** Optional callback when the project switcher pill is activated.
   *  Until the multi-project popover lands, the pill is a clickable
   *  surface that flashes a "More projects coming soon" hint via
   *  ``onClick``. The shell wires it; tests can supply a stub. */
  onOpenProjectSwitcher?: () => void;
};

/**
 * The chat-first shell's top bar. Thin (~52 px), three-region layout:
 * brain identity badge on the left, project switcher pill in the
 * centre, command-palette hint + settings cog on the right.
 *
 * The brain identity badge owns the provider-switcher popover for
 * the chat-first surface — clicking it lists configured providers
 * and writes the chosen one through ``writeActiveProvider`` +
 * ``emitActiveProviderChange``, the same channel the legacy chat
 * header uses. The configuration check tones the dot
 * (``border-success`` when keyed, ``border-warning`` otherwise) so
 * an unconfigured provider is visible at a glance without forcing
 * a popover open to discover it.
 */
export function TopBar({
  brainName,
  activeProviderId,
  configured,
  projectName,
  onOpenSettings,
  onOpenProjectSwitcher,
}: Props) {
  const [providerOpen, setProviderOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderMeta[]>([]);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!providerOpen) return;
    let cancelled = false;
    void listProviders()
      .then(async (list) => {
        const enriched = await Promise.all(
          list.map(async (provider) => {
            const isConfigured = await isProviderConfigured(provider.id).catch(
              () => false,
            );
            return { ...provider, configured: isConfigured };
          }),
        );
        if (cancelled) return;
        setProviders(enriched);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [providerOpen]);

  useEffect(() => {
    if (!providerOpen) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (
        target instanceof Node &&
        (popoverRef.current?.contains(target) ||
          triggerRef.current?.contains(target))
      ) {
        return;
      }
      setProviderOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setProviderOpen(false);
    };
    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [providerOpen]);

  const active =
    providers.find((p) => p.id === activeProviderId) ??
    providers.find((p) => p.id === activeProviderId.split(":")[0]) ??
    null;

  const providerLabel =
    active?.displayName ??
    fallbackProviderLabel(activeProviderId);

  const handleSelect = (provider: ProviderMeta) => {
    setProviderOpen(false);
    if (provider.id === activeProviderId) return;
    if (!provider.enabled) return;
    writeActiveProvider(provider.id);
    emitActiveProviderChange(provider.id);
  };

  return (
    <header
      role="banner"
      aria-label="App"
      className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-border bg-background px-4"
    >
      <div className="flex min-w-0 items-center gap-2">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setProviderOpen((current) => !current)}
          aria-haspopup="listbox"
          aria-expanded={providerOpen}
          aria-label={`Brain identity: ${brainName} via ${providerLabel}. Open provider switcher.`}
          className="group flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <span
            aria-hidden
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              configured === false
                ? "bg-warning"
                : "bg-success",
            )}
          />
          <span className="font-semibold">{brainName}</span>
          <span aria-hidden className="text-muted-foreground">
            ·
          </span>
          <span className="truncate text-muted-foreground">
            {providerLabel}
          </span>
          <ChevronDown
            aria-hidden
            className="h-3.5 w-3.5 text-muted-foreground transition-transform group-aria-expanded:rotate-180"
          />
        </button>
        {providerOpen && (
          <div className="relative">
            <div
              ref={popoverRef}
              role="listbox"
              aria-label="Available providers"
              className="absolute left-0 top-1 z-30 w-[280px] rounded-md border border-border bg-popover p-1 shadow-lg"
            >
              <ul>
                {providers.map((provider) => {
                  const isActive = provider.id === activeProviderId;
                  const disabled = !provider.enabled;
                  return (
                    <li key={provider.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={isActive}
                        aria-disabled={disabled}
                        disabled={disabled}
                        onClick={() => handleSelect(provider)}
                        className={cn(
                          "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors",
                          disabled
                            ? "cursor-not-allowed text-muted-foreground"
                            : "hover:bg-accent",
                        )}
                      >
                        <span className="flex flex-col overflow-hidden">
                          <span className="truncate font-medium">
                            {provider.displayName}
                          </span>
                          <span className="truncate text-[10px] text-muted-foreground">
                            {profileSummary(provider)}
                          </span>
                        </span>
                        {isActive && (
                          <span
                            aria-hidden
                            className="text-success"
                          >
                            ●
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
                {providers.length === 0 && (
                  <li className="px-2 py-2 text-xs text-muted-foreground">
                    No providers loaded.
                  </li>
                )}
              </ul>
            </div>
          </div>
        )}
      </div>

      <div className="flex min-w-0 items-center justify-center">
        <button
          type="button"
          onClick={onOpenProjectSwitcher}
          aria-label={`Project: ${projectName}. Open project switcher.`}
          className="group flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <span className="truncate">{projectName}</span>
          <ChevronDown
            aria-hidden
            className="h-3 w-3 text-muted-foreground transition-transform group-aria-expanded:rotate-180"
          />
        </button>
      </div>

      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={dispatchOpenCommandPalette}
          aria-label="Open command palette"
          className="hidden items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:inline-flex"
        >
          <CommandIcon aria-hidden className="h-3 w-3" />
          <span>K</span>
        </button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onOpenSettings}
          aria-label="Open settings"
          className="h-8 w-8"
        >
          <SettingsIcon aria-hidden className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}

function profileSummary(provider: ProviderMeta): string {
  const parts: string[] = [];
  if (provider.capabilityProfile?.local) {
    parts.push("local");
  } else if (provider.capabilityProfile) {
    parts.push("cloud");
  }
  if (provider.configured === false) parts.push("not configured");
  if (!provider.enabled) parts.push("coming soon");
  return parts.join(" · ");
}

function fallbackProviderLabel(providerId: string): string {
  if (!providerId) return "Provider";
  return providerId
    .split(/[_-]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
