import { useCallback, useEffect, useState } from "react";

import { PlanApprovalDialog } from "@/components/approval/plan-approval-dialog";
import { useApprovalGate } from "@/components/approval/use-approval-gate";
import { CapabilityDeltaDialog } from "@/components/chat/capability-delta-dialog";
import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { ThreadDigestGreeting } from "@/components/chat/thread-digest-greeting";
import { commit as commitProviderSwap } from "@/components/chat/provider-switcher";
import { useChat } from "@/components/chat/use-chat";
import { CommandPalette } from "@/components/command-palette";
import { useDriftGate } from "@/components/inspector/use-drift-gate";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import {
  DrawerHost,
  DrawerHostProvider,
  useDrawerHost,
} from "@/components/shell/drawer-host";
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
 * The chat-first shell (ADR-0026). Five regions stacked vertically
 * inside a chat column, with the drawer band sitting to its right
 * when one or more drawers are open:
 *
 *   ┌────────────────────────────────────────────────────────┐
 *   │  Top bar (~52 px)                                      │
 *   ├──────────────────────────┬─────────────────────────────┤
 *   │                          │                             │
 *   │  Eternal chat (fluid)    │  Drawer band                │
 *   │                          │  (0, 1, or 2 drawers)       │
 *   ├──────────────────────────┤                             │
 *   │  Transient progress      │                             │
 *   ├──────────────────────────┤                             │
 *   │  Composer                │                             │
 *   └──────────────────────────┴─────────────────────────────┘
 *
 * Drawer host (ADR-0026, F8.2) brings the editor / terminal / email /
 * file-tree / connectors / logs surfaces in on demand and dismisses
 * with ⌘\\. Below the 900 px breakpoint the drawer band takes the
 * chat column's place — chat returns the moment the drawer dismisses.
 */
export function ChatFirstShell() {
  return (
    <DrawerHostProvider>
      <ShellInner />
    </DrawerHostProvider>
  );
}

function ShellInner() {
  const [providerId, setProviderId] = useState<string>(() => readActiveProvider());
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [pendingSwap, setPendingSwap] = useState<{
    from: ProviderMeta;
    to: ProviderMeta;
  } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const { messages, status, send } = useChat({ providerId });
  const approval = useApprovalGate();
  const drift = useDriftGate();
  const drawerHost = useDrawerHost();

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

  // Priority order for the transient strip (F8.3): drift > approval >
  // sending. The strip shows one signal at a time; clicks open the
  // worker drawer for that run so the user can drill into the plan
  // and action log without leaving chat.
  const activity: TransientActivity | null = drift.gate
    ? {
        kind: "drift",
        label: `Drift flagged on a run${
          drift.gate.reason ? ` — ${drift.gate.reason}` : "."
        }`,
        onClick: () => {
          drawerHost.open({
            kind: "worker",
            params: { runId: drift.gate!.runId },
          });
          drift.dismiss();
        },
      }
    : approval.gate
    ? {
        kind: "awaiting_approval",
        label: "Plan ready for review — open to approve or edit.",
        onClick: () =>
          drawerHost.open({
            kind: "worker",
            params: { runId: approval.gate!.runId },
          }),
      }
    : sending
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

      <DrawerHost
        chat={
          <>
            <main className="flex min-h-0 flex-1 flex-col">
              <div className="mx-auto flex h-full w-full max-w-3xl flex-1 flex-col">
                <MessageList
                  messages={messages}
                  header={<ThreadDigestGreeting />}
                />
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
          </>
        }
      />

      <CommandPalette
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />

      <CapabilityDeltaDialog
        pending={pendingSwap}
        onCancel={handleCancelSwap}
        onConfirm={handleConfirmSwap}
      />

      <PlanApprovalDialog
        open={approval.gate !== null}
        runId={approval.gate?.runId ?? null}
        providerId={providerId}
        plan={approval.gate?.plan ?? null}
        onSettled={approval.clear}
      />
    </div>
  );
}
