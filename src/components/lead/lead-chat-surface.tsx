import { Brain } from "lucide-react";
import { useEffect, useState } from "react";

import { Composer } from "@/components/chat/composer";
import { MessageList } from "@/components/chat/message-list";
import { useChat } from "@/components/chat/use-chat";
import type { Message } from "@/components/chat/types";
import { Badge } from "@/components/ui/badge";
import {
  readActiveProvider,
  subscribeActiveProvider,
} from "@/lib/active-provider";

/**
 * Direct lead-chat drawer (F2.4) — full feature parity with the
 * eternal thread, scoped to one lead. Same composer, same scrollable
 * history, same streaming pipeline; the only difference is the
 * ``leadId`` plumbed through ``useChat`` so every turn delegates to
 * that lead instead of routing through Thalyn.
 *
 * The drawer host keeps the surface mounted across dismiss/re-open,
 * so the message history and scroll position survive a round-trip
 * the same way the eternal thread does.
 */
export function LeadChatSurface({
  agentId,
  displayName,
  staticMessages,
}: {
  agentId: string;
  /** Optional display name — when omitted the surface falls back to
   *  the agentId. Storybook stories pass it explicitly; the live
   *  caller resolves it from the lead drawer / palette context. */
  displayName?: string;
  /** Storybook / playwright fallback so the surface renders without
   *  a live brain. The component renders these instead of seeding a
   *  ``useChat`` session — production callers omit it. */
  staticMessages?: Message[];
}) {
  const [providerId, setProviderId] = useState<string>(() =>
    readActiveProvider(),
  );
  useEffect(() => subscribeActiveProvider(setProviderId), []);

  const live = useChat({
    providerId,
    leadId: agentId,
    leadDisplayName: displayName,
  });
  const messages = staticMessages ?? live.messages;
  const sending = live.status.kind === "sending";
  const error =
    live.status.kind === "error" ? live.status.message : null;

  const label = displayName ?? agentId;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start gap-2 border-b border-border bg-surface px-4 py-2 pr-24">
        <Brain
          aria-hidden
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-sm font-semibold">{label}</h2>
            <Badge tone="muted">Direct chat</Badge>
          </div>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
            {agentId}
          </p>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col">
        <MessageList messages={messages} />
      </div>

      {error && (
        <p
          role="alert"
          className="border-t border-border bg-destructive/10 px-4 py-1.5 text-[11px] text-danger"
        >
          {error}
        </p>
      )}

      <Composer
        size="compact"
        disabled={sending}
        placeholder={`Message ${label}…`}
        onSubmit={live.send}
      />
    </div>
  );
}
