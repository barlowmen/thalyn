import { useCallback, useEffect, useMemo, useState } from "react";

import { CapabilityDeltaDialog } from "@/components/chat/capability-delta-dialog";
import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { ThreadDigestGreeting } from "@/components/chat/thread-digest-greeting";
import {
  commit as commitProviderSwap,
  ProviderSwitcher,
} from "@/components/chat/provider-switcher";
import { useChat } from "@/components/chat/use-chat";
import { SubAgentTiles } from "@/components/inspector/subagent-tiles";
import { useRootRunId } from "@/components/inspector/use-root-run-id";
import { useRunDetail } from "@/components/inspector/use-run-detail";
import { useSubAgentTree } from "@/components/inspector/use-subagent-tree";
import { Button } from "@/components/ui/button";
import {
  readActiveProvider,
  subscribeActiveProvider,
} from "@/lib/active-provider";
import { isProviderConfigured, type ProviderMeta } from "@/lib/providers";
import { killRun, type Plan } from "@/lib/runs";

/**
 * The main-panel chat surface. Reads the active provider from the
 * settings store and routes turns through it; the radio-group
 * selector in Settings dispatches a `thalyn:active-provider-changed`
 * event that we listen for here.
 */
export function ChatSurface({
  onOpenSettings,
  onOpenSubAgent,
  takeOverRunId,
  onHandBack,
}: {
  onOpenSettings: () => void;
  onOpenSubAgent?: (runId: string) => void;
  takeOverRunId?: string | null;
  onHandBack?: () => void;
}) {
  const [providerId, setProviderId] = useState<string>(() => readActiveProvider());
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [pendingSwap, setPendingSwap] = useState<{
    from: ProviderMeta;
    to: ProviderMeta;
  } | null>(null);
  const handleSwapRequest = useCallback(
    (swap: { from: ProviderMeta; to: ProviderMeta } | null) => {
      if (!swap) return;
      setPendingSwap(swap);
    },
    [],
  );
  const handleConfirmSwap = useCallback((toId: string) => {
    commitProviderSwap(toId);
    setPendingSwap(null);
  }, []);
  const handleCancelSwap = useCallback(() => setPendingSwap(null), []);
  const takeOverDetail = useRunDetail(takeOverRunId ?? null);
  const takeOverPrompt = useMemo(
    () =>
      takeOverDetail
        ? buildTakeOverPrompt({
            title: takeOverDetail.title,
            plan: takeOverDetail.plan,
            finalResponse: takeOverDetail.finalResponse,
          })
        : undefined,
    [takeOverDetail],
  );
  const { messages, status, send } = useChat({
    providerId,
    systemPrompt: takeOverPrompt,
  });
  const rootRunId = useRootRunId();
  const subAgentTiles = useSubAgentTree(rootRunId);

  // Listen for selection changes coming out of the Settings dialog.
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
          <ProviderSwitcher
            activeProviderId={providerId}
            configured={configured}
            onSwap={handleSwapRequest}
          />
        </div>
        <button
          type="button"
          onClick={onOpenSettings}
          className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded"
        >
          Settings
        </button>
      </header>

      {takeOverDetail && (
        <div className="flex items-center justify-between gap-3 border-b border-border bg-warning/10 px-6 py-2 text-xs">
          <div>
            <span className="font-semibold">Took over from</span>{" "}
            <span className="text-muted-foreground">
              {takeOverDetail.title}
            </span>
          </div>
          {onHandBack && (
            <Button size="sm" variant="ghost" onClick={onHandBack}>
              Hand back
            </Button>
          )}
        </div>
      )}

      <MessageList
        messages={messages}
        header={<ThreadDigestGreeting />}
      />

      {subAgentTiles.length > 0 && (
        <div className="border-t border-border bg-surface px-6 py-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Sub-agents
          </p>
          <SubAgentTiles
            tiles={subAgentTiles}
            onOpen={onOpenSubAgent}
            onKill={(runId) => {
              void killRun(runId).catch(() => undefined);
            }}
          />
        </div>
      )}

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

      <CapabilityDeltaDialog
        pending={pendingSwap}
        onCancel={handleCancelSwap}
        onConfirm={handleConfirmSwap}
      />
    </div>
  );
}

function buildTakeOverPrompt({
  title,
  plan,
  finalResponse,
}: {
  title: string;
  plan: Plan | null;
  finalResponse: string;
}): string {
  const lines: string[] = [
    "You are continuing the work of a delegated sub-agent. The user has taken",
    "over the thread; treat its history as read-only context.",
    "",
    `Sub-agent task: ${title}`,
  ];
  if (plan?.goal) {
    lines.push(`Goal: ${plan.goal}`);
  }
  if (plan && plan.nodes.length > 0) {
    lines.push("Planned steps:");
    plan.nodes.forEach((node, index) => {
      lines.push(`  ${index + 1}. ${node.description}`);
    });
  }
  if (finalResponse.trim()) {
    lines.push("Result so far:");
    lines.push(finalResponse.trim());
  } else {
    lines.push("The sub-agent had not produced a final response when the user took over.");
  }
  return lines.join("\n");
}
