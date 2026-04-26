import { useEffect, useState } from "react";

import { AnthropicApiKeyForm } from "@/components/settings/anthropic-api-key";
import { ProviderSelector } from "@/components/settings/provider-selector";
import { readActiveProvider } from "@/lib/active-provider";
import { listProviders, type ProviderMeta } from "@/lib/providers";

/**
 * Lists every provider with its capability profile, lets the user
 * pick which one is active (radio-group), and surfaces inline
 * configuration for the active selection.
 */
export function ProviderSection() {
  const [providers, setProviders] = useState<ProviderMeta[]>([]);
  const [activeProviderId, setActiveProviderId] = useState<string>(() =>
    readActiveProvider(),
  );
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

  const active = providers.find((p) => p.id === activeProviderId) ?? null;

  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Providers
        </h3>
        <p className="text-sm text-muted-foreground">
          Pick where the brain runs. v0.3 ships the Anthropic adapter;
          the OpenAI-compatible / Ollama / llama.cpp / MLX rows arrive
          in subsequent iterations.
        </p>
      </header>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <ProviderSelector
        providers={providers}
        activeProviderId={activeProviderId}
        onChange={setActiveProviderId}
      />

      {active?.id === "anthropic" && active.enabled && (
        <div className="space-y-3 rounded-lg border border-border bg-card p-4">
          <h4 className="text-sm font-medium">Anthropic API key</h4>
          <AnthropicApiKeyForm onConfiguredChange={() => refresh()} />
        </div>
      )}
    </section>
  );
}
