import { useEffect, useState } from "react";

import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { useChat } from "@/components/chat/use-chat";
import { Badge } from "@/components/ui/badge";
import {
  readActiveProvider,
  subscribeActiveProvider,
} from "@/lib/active-provider";
import {
  isProviderConfigured,
  listProviders,
  type ProviderMeta,
} from "@/lib/providers";

/**
 * The main-panel chat surface. Reads the active provider from the
 * settings store and routes turns through it; the radio-group
 * selector in Settings dispatches a `thalyn:active-provider-changed`
 * event that we listen for here.
 */
export function ChatSurface({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [providerId, setProviderId] = useState<string>(() => readActiveProvider());
  const [provider, setProvider] = useState<ProviderMeta | null>(null);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const { messages, status, send } = useChat({ providerId });

  // Listen for selection changes coming out of the Settings dialog.
  useEffect(() => subscribeActiveProvider(setProviderId), []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listProviders().catch(() => [] as ProviderMeta[]),
      isProviderConfigured(providerId).catch(() => false),
    ]).then(([providers, isConfigured]) => {
      if (cancelled) return;
      const found = providers.find((p) => p.id === providerId) ?? null;
      setProvider(found);
      setConfigured(isConfigured);
    });
    return () => {
      cancelled = true;
    };
  }, [providerId]);

  const sending = status.kind === "sending";
  const errorMessage =
    status.kind === "error"
      ? status.message
      : !configured && configured !== null
      ? "No Anthropic API key on file. Open Settings to add one."
      : null;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border bg-background px-6 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Chat</h2>
          {provider && (
            <Badge tone={configured ? "success" : "warning"}>
              {provider.displayName}
            </Badge>
          )}
        </div>
        <button
          type="button"
          onClick={onOpenSettings}
          className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded"
        >
          Settings
        </button>
      </header>

      <MessageList messages={messages} />

      {errorMessage && (
        <p className="border-t border-border bg-destructive/10 px-6 py-2 text-xs text-destructive">
          {errorMessage}
        </p>
      )}

      <Composer
        disabled={sending || !configured}
        placeholder={
          !configured
            ? "Add an Anthropic API key in Settings to enable chat."
            : undefined
        }
        onSubmit={send}
      />
    </div>
  );
}
