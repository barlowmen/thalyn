import { useCallback, useEffect, useState } from "react";

import { CapabilityDeltaDialog } from "@/components/chat/capability-delta-dialog";
import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { ThreadDigestGreeting } from "@/components/chat/thread-digest-greeting";
import { commit as commitProviderSwap } from "@/components/chat/provider-switcher";
import { useChat } from "@/components/chat/use-chat";
import { CommandPalette } from "@/components/command-palette";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import { TopBar } from "@/components/shell/top-bar";
import {
  type TransientActivity,
  TransientProgressStrip,
} from "@/components/shell/transient-progress-strip";
import {
  readActiveProvider,
  subscribeActiveProvider,
} from "@/lib/active-provider";
import {
  isProviderConfigured,
  type ProviderMeta,
} from "@/lib/providers";

/**
 * The chat-first shell (ADR-0026). Five regions stacked vertically:
 *
 *   ┌──────────────────────────────────────────────────────┐
 *   │  Top bar (~52 px)                                    │
 *   ├──────────────────────────────────────────────────────┤
 *   │  Eternal chat (fluid)                                │
 *   ├──────────────────────────────────────────────────────┤
 *   │  Transient progress strip (~36 px, when in flight)   │
 *   ├──────────────────────────────────────────────────────┤
 *   │  Composer (~72 px)                                   │
 *   └──────────────────────────────────────────────────────┘
 *
 * Drawer-host primitive lands later; until then this shell wraps
 * the same chat surface logic the v1 mosaic uses, just in the new
 * topology. The legacy mosaic shell stays reachable under
 * ``/legacy`` so the editor / terminal / browser / email /
 * connectors / agents / logs surfaces remain available during the
 * transition.
 */
export function ChatFirstShell() {
  const [providerId, setProviderId] = useState<string>(() => readActiveProvider());
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [pendingSwap, setPendingSwap] = useState<{
    from: ProviderMeta;
    to: ProviderMeta;
  } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const { messages, status, send } = useChat({ providerId });

  useEffect(() => subscribeActiveProvider(setProviderId), []);

  useEffect(() => {
    let cancelled = false;
    isProviderConfigured(providerId)
      .then((isConfigured) => {
        if (cancelled) return;
        setConfigured(isConfigured);
      })
      .catch(() => {
        if (cancelled) return;
        setConfigured(false);
      });
    return () => {
      cancelled = true;
    };
  }, [providerId]);

  const sending = status.kind === "sending";
  const handleConfirmSwap = useCallback((toId: string) => {
    commitProviderSwap(toId);
    setPendingSwap(null);
  }, []);
  const handleCancelSwap = useCallback(() => setPendingSwap(null), []);

  const errorMessage =
    status.kind === "error"
      ? status.message
      : !configured && configured !== null
      ? "No Anthropic API key on file. Open Settings to add one."
      : null;

  const activity: TransientActivity | null = sending
    ? {
        kind: "sending",
        label: "Routing your turn through Thalyn…",
      }
    : null;

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <TopBar
        brainName="Thalyn"
        activeProviderId={providerId}
        configured={configured}
        projectName="Thalyn"
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="flex min-h-0 flex-1 flex-col">
        <div className="mx-auto flex h-full w-full max-w-3xl flex-1 flex-col">
          <MessageList messages={messages} header={<ThreadDigestGreeting />} />
        </div>
      </main>

      {errorMessage && (
        <p
          role="alert"
          className="border-t border-border bg-destructive/10 px-6 py-2 text-xs text-destructive"
        >
          {errorMessage}
        </p>
      )}

      <TransientProgressStrip activity={activity} />

      <div className="mx-auto w-full max-w-3xl">
        <Composer
          size="roomy"
          disabled={sending || !configured}
          placeholder={
            !configured
              ? "Add an Anthropic API key in Settings to enable chat."
              : undefined
          }
          onSubmit={send}
        />
      </div>

      <CommandPalette
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />

      <CapabilityDeltaDialog
        pending={pendingSwap}
        onCancel={handleCancelSwap}
        onConfirm={handleConfirmSwap}
      />
    </div>
  );
}
