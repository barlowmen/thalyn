import { useEffect, useState } from "react";

import { AnthropicApiKeyForm } from "@/components/settings/anthropic-api-key";
import { Badge } from "@/components/ui/badge";
import { listProviders, type ProviderMeta } from "@/lib/providers";
import { cn } from "@/lib/utils";

const RELIABILITY_LABEL = {
  high: "High",
  medium: "Medium",
  low: "Low",
  unknown: "—",
} as const;

/**
 * Lists every provider with its capability profile and inline
 * configuration (the Anthropic provider gets the paste-API-key form;
 * the placeholders are read-only with a "coming later" note).
 */
export function ProviderSection() {
  const [providers, setProviders] = useState<ProviderMeta[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const list = await listProviders();
      setProviders(list);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Providers
        </h3>
        <p className="text-sm text-muted-foreground">
          Configure where the brain runs. Anthropic is the v0.3 default;
          local + OpenAI-compatible adapters arrive in subsequent
          iterations.
        </p>
      </header>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <ul className="space-y-3">
        {providers.map((provider) => (
          <li
            key={provider.id}
            className={cn(
              "rounded-lg border border-border bg-card p-4",
              !provider.enabled && "opacity-70",
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {provider.displayName}
                  </span>
                  {provider.configured && (
                    <Badge tone="success">Connected</Badge>
                  )}
                  {!provider.enabled && (
                    <Badge tone="muted">Coming later</Badge>
                  )}
                </div>
                <CapabilityLine provider={provider} />
              </div>
            </div>

            {provider.id === "anthropic" && provider.enabled && (
              <div className="mt-4 border-t border-border pt-4">
                <AnthropicApiKeyForm onConfiguredChange={() => refresh()} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function CapabilityLine({ provider }: { provider: ProviderMeta }) {
  if (!provider.enabled) {
    return (
      <p className="mt-1 text-xs text-muted-foreground">
        {provider.capabilityProfile.local
          ? "Local inference; capabilities populate when the adapter ships."
          : "Capabilities populate when the adapter ships."}
      </p>
    );
  }
  const { capabilityProfile: cap } = provider;
  return (
    <p className="mt-1 text-xs text-muted-foreground">
      {cap.maxContextTokens.toLocaleString()} tokens · tool use{" "}
      {RELIABILITY_LABEL[cap.toolUseReliability]} ·
      {cap.supportsVision ? " vision · " : " no vision · "}
      {cap.supportsStreaming ? "streaming" : "no streaming"}
    </p>
  );
}
