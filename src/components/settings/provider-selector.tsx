import { Check } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  emitActiveProviderChange,
  writeActiveProvider,
} from "@/lib/active-provider";
import type { ProviderMeta } from "@/lib/providers";
import { cn } from "@/lib/utils";

type Props = {
  providers: ProviderMeta[];
  activeProviderId: string;
  onChange: (providerId: string) => void;
};

const RELIABILITY_LABEL = {
  high: "High",
  medium: "Medium",
  low: "Low",
  unknown: "—",
} as const;

/**
 * Radio-group of providers. Disabled placeholders surface here so the
 * user can see what's coming; they're not selectable until the
 * adapter ships.
 */
export function ProviderSelector({
  providers,
  activeProviderId,
  onChange,
}: Props) {
  return (
    <fieldset className="space-y-2">
      <legend className="sr-only">Active provider</legend>
      <ul role="radiogroup" className="space-y-2">
        {providers.map((provider) => {
          const selected = provider.id === activeProviderId;
          const disabled = !provider.enabled;
          return (
            <li key={provider.id}>
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                aria-disabled={disabled}
                disabled={disabled}
                onClick={() => {
                  if (disabled) return;
                  writeActiveProvider(provider.id);
                  emitActiveProviderChange(provider.id);
                  onChange(provider.id);
                }}
                className={cn(
                  "flex w-full items-start gap-3 rounded-lg border border-border bg-card px-3 py-2 text-left",
                  "transition-colors",
                  selected
                    ? "border-primary bg-primary/5"
                    : "hover:border-foreground/20",
                  disabled && "cursor-not-allowed opacity-60",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "mt-0.5 flex h-4 w-4 items-center justify-center rounded-full border",
                    selected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-background",
                  )}
                >
                  {selected && <Check className="h-3 w-3" />}
                </span>

                <span className="flex-1">
                  <span className="flex items-center gap-2">
                    <span className="text-sm font-medium">
                      {provider.displayName}
                    </span>
                    {provider.configured && provider.enabled && (
                      <Badge tone="success">Connected</Badge>
                    )}
                    {disabled && <Badge tone="muted">Coming later</Badge>}
                  </span>
                  <ProfileLine provider={provider} />
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </fieldset>
  );
}

function ProfileLine({ provider }: { provider: ProviderMeta }) {
  if (!provider.enabled) {
    return (
      <span className="mt-1 block text-xs text-muted-foreground">
        Capabilities populate when the adapter ships.
      </span>
    );
  }
  const cap = provider.capabilityProfile;
  return (
    <span className="mt-1 block text-xs text-muted-foreground">
      {cap.maxContextTokens.toLocaleString()} tokens · tool use{" "}
      {RELIABILITY_LABEL[cap.toolUseReliability]} ·
      {cap.supportsVision ? " vision · " : " no vision · "}
      {cap.supportsStreaming ? "streaming" : "no streaming"}
    </span>
  );
}
