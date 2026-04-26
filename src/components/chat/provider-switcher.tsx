import { useEffect, useRef, useState } from "react";

import { Check, ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  emitActiveProviderChange,
  writeActiveProvider,
} from "@/lib/active-provider";
import {
  isProviderConfigured,
  listProviders,
  type ProviderMeta,
} from "@/lib/providers";

type Props = {
  activeProviderId: string;
  configured: boolean | null;
  onSwap?: (
    args: { from: ProviderMeta; to: ProviderMeta } | null,
  ) => void;
};

/**
 * Provider switcher rendered in the chat header — the brain-mode
 * badge from the requirements. Clicking the badge opens a list of
 * the available providers; selecting one writes the choice into
 * persistent state and broadcasts the change so every chat surface
 * subscribes-and-updates.
 *
 * The optional ``onSwap`` callback fires *before* the change
 * propagates so a parent can show a capability-delta dialog and
 * confirm the swap; pass ``null`` to skip the dialog. When ``onSwap``
 * is omitted the switcher commits the change directly.
 */
export function ProviderSwitcher({
  activeProviderId,
  configured,
  onSwap,
}: Props) {
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<ProviderMeta[]>([]);
  const [hover, setHover] = useState<string | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
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
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (
        target instanceof Node &&
        (popoverRef.current?.contains(target) ||
          triggerRef.current?.contains(target))
      ) {
        return;
      }
      setOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const active =
    providers.find((p) => p.id === activeProviderId) ??
    providers.find((p) => p.id === activeProviderId.split(":")[0]) ??
    null;

  const handleSelect = (provider: ProviderMeta) => {
    setOpen(false);
    if (provider.id === activeProviderId) return;
    if (!provider.enabled) return;
    if (onSwap) {
      onSwap({
        from: active ?? provider,
        to: provider,
      });
    } else {
      commit(provider.id);
    }
  };

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-md hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <Badge tone={configured ? "success" : "warning"}>
          {active?.displayName ?? "Provider"}
        </Badge>
        <ChevronDown
          className="h-3.5 w-3.5 text-muted-foreground"
          aria-hidden
        />
      </button>
      {open && (
        <div
          ref={popoverRef}
          role="listbox"
          aria-label="Available providers"
          className="absolute left-0 top-full z-30 mt-1 w-[280px] rounded-md border border-border bg-popover p-1 shadow-lg"
        >
          <ul>
            {providers.map((provider) => {
              const isActive = provider.id === activeProviderId;
              const isHover = hover === provider.id;
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
                    onMouseEnter={() => setHover(provider.id)}
                    onMouseLeave={() => setHover(null)}
                    className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors ${
                      disabled
                        ? "cursor-not-allowed text-muted-foreground"
                        : isHover
                        ? "bg-surface-hover"
                        : ""
                    }`}
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
                      <Check
                        className="h-3.5 w-3.5 text-success"
                        aria-hidden
                      />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
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

export function commit(providerId: string): void {
  writeActiveProvider(providerId);
  emitActiveProviderChange(providerId);
}
