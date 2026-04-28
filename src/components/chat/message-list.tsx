import { type ReactNode, useEffect, useRef } from "react";

import { ToolCallCard } from "@/components/chat/tool-call-card";
import type { Message } from "@/components/chat/types";
import { cn } from "@/lib/utils";

type Props = {
  messages: Message[];
  /**
   * Optional header rendered above the messages inside the scroll
   * region. Used by the chat surface to slot in the day-divider /
   * since-we-last-spoke digest greeting.
   */
  header?: ReactNode;
};

/**
 * Renders the conversation. The aria-live region wraps assistant
 * messages so screen-readers announce streamed text without flooding —
 * polite mode batches per-render.
 */
export function MessageList({ messages, header }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Pin to the bottom when new content arrives — common chat affordance.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col overflow-y-auto px-6 py-4">
        {header}
        <div className="flex flex-1 items-center justify-center text-center text-muted-foreground">
          <p className="max-w-md text-sm">
            Say hello to Thalyn — the conversation never resets, so
            anything you start here picks up where it left off next time.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      className="flex-1 space-y-4 overflow-y-auto px-6 py-4"
      role="log"
      aria-live="polite"
      aria-label="Conversation"
    >
      {header}
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80ch] rounded-lg border border-border bg-card px-3 py-2 text-sm">
          {message.text}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {message.segments.map((segment, idx) => {
        if (segment.kind === "text") {
          return (
            <p
              key={idx}
              className="max-w-[80ch] whitespace-pre-wrap text-sm leading-relaxed"
            >
              {segment.text}
              {!message.done && idx === message.segments.length - 1 && (
                <span
                  aria-hidden
                  className={cn(
                    "ml-0.5 inline-block h-3.5 w-1.5 -mb-0.5",
                    "animate-pulse bg-foreground/70",
                  )}
                />
              )}
            </p>
          );
        }
        if (segment.kind === "tool_call") {
          return (
            <ToolCallCard
              key={segment.callId || idx}
              callId={segment.callId}
              tool={segment.tool}
              input={segment.input}
              output={segment.output}
              isError={segment.isError}
            />
          );
        }
        return (
          <p
            key={idx}
            className="max-w-[80ch] rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {segment.message}
            {segment.code && (
              <span className="ml-2 font-mono text-xs opacity-70">
                ({segment.code})
              </span>
            )}
          </p>
        );
      })}
      {message.done && message.totalCostUsd != null && (
        <p className="text-xs text-muted-foreground">
          ${message.totalCostUsd.toFixed(4)} ·{" "}
          {message.model || "Anthropic"}
        </p>
      )}
    </div>
  );
}
