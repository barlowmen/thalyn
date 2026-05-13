import { UserCog } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import { ConfidencePill } from "@/components/chat/confidence-pill";
import { ProjectTag } from "@/components/chat/project-tag";
import { ToolCallCard } from "@/components/chat/tool-call-card";
import type { ConfidencePayload, Message } from "@/components/chat/types";
import { Badge } from "@/components/ui/badge";
import type { Project } from "@/lib/projects";
import { cn } from "@/lib/utils";

export type ProjectsById = Map<string, Pick<Project, "projectId" | "name" | "slug">>;

type Props = {
  messages: Message[];
  /**
   * Optional header rendered above the messages inside the scroll
   * region. Used by the chat surface to slot in the day-divider /
   * since-we-last-spoke digest greeting.
   */
  header?: ReactNode;
  /**
   * Optional footer rendered below the messages inside the scroll
   * region. Used by the chat surface to slot in inline approval gates
   * (F8.9 — everyday gates render inline with the conversation rather
   * than as modal dialogs).
   */
  footer?: ReactNode;
  /**
   * Project lookup map for rendering per-message project pills (F8.5).
   * When absent or a turn's ``projectId`` isn't in the map, the pill
   * is suppressed — the bubble still renders unchanged. Resolution
   * lives at the shell level so the message list stays decoupled from
   * the projects RPC.
   */
  projectsById?: ProjectsById;
  /**
   * Drill-into-source handler for the F1.10 / ADR-0027 confidence
   * pill. Receives the audit whose ``sourceRef`` should be opened in
   * the relevant drawer. Optional — when omitted, the pill renders
   * as a non-interactive badge and the audit summary surfaces in
   * the tooltip only.
   */
  onDrillIntoSource?: (audit: ConfidencePayload["audit"]) => void;
};

/**
 * Renders the conversation. The aria-live region wraps assistant
 * messages so screen-readers announce streamed text without flooding —
 * polite mode batches per-render.
 */
export function MessageList({
  messages,
  header,
  footer,
  projectsById,
  onDrillIntoSource,
}: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Pin to the bottom when new content arrives — common chat affordance.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, footer]);

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
        {footer}
      </div>
    );
  }

  let lastDayMs: number | null = null;

  return (
    <div
      ref={scrollRef}
      className="flex-1 space-y-4 overflow-y-auto px-6 py-4"
      role="log"
      aria-live="polite"
      aria-label="Conversation"
    >
      {header}
      {messages.map((message) => {
        const messageDayMs = startOfDay(message.atMs);
        const showDivider =
          messageDayMs !== null &&
          (lastDayMs === null || messageDayMs > lastDayMs);
        if (messageDayMs !== null) {
          lastDayMs = messageDayMs;
        }
        return (
          <div key={message.id} className="space-y-4">
            {showDivider && messageDayMs !== null && (
              <DayDivider dayMs={messageDayMs} />
            )}
            <MessageBubble
              message={message}
              projectsById={projectsById}
              onDrillIntoSource={onDrillIntoSource}
            />
          </div>
        );
      })}
      {footer}
    </div>
  );
}

function renderProjectTag(
  projectId: string | undefined,
  projectsById: ProjectsById | undefined,
): ReactNode {
  if (!projectId || !projectsById) return null;
  const project = projectsById.get(projectId);
  if (!project) return null;
  return <ProjectTag seed={project.slug || project.projectId} name={project.name} />;
}

function startOfDay(ms: number | undefined): number | null {
  if (ms === undefined) return null;
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function DayDivider({ dayMs }: { dayMs: number }) {
  const label = new Date(dayMs).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  return (
    <div role="separator" className="flex items-center gap-3" aria-label={label}>
      <div className="h-px flex-1 bg-border" aria-hidden />
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="h-px flex-1 bg-border" aria-hidden />
    </div>
  );
}

function MessageBubble({
  message,
  projectsById,
  onDrillIntoSource,
}: {
  message: Message;
  projectsById?: ProjectsById;
  onDrillIntoSource?: (audit: ConfidencePayload["audit"]) => void;
}) {
  const projectTag = renderProjectTag(message.projectId, projectsById);
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[80ch] flex-col items-end gap-1">
          {projectTag}
          <div className="rounded-lg border border-border bg-card px-3 py-2 text-sm">
            {message.text}
          </div>
        </div>
      </div>
    );
  }

  const attribution = message.leadAttribution;
  const confidence = message.confidence;

  return (
    <div className="space-y-2">
      {projectTag && <div>{projectTag}</div>}
      {attribution || confidence ? (
        <div
          className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground"
          aria-label={
            attribution
              ? `Delegated to ${attribution.displayName ?? attribution.agentId}`
              : "Relayed reply"
          }
        >
          {attribution && (
            <>
              <UserCog aria-hidden className="size-3" />
              <span>via</span>
              <Badge tone="default">
                {attribution.displayName ?? attribution.agentId}
              </Badge>
            </>
          )}
          {confidence && (
            <ConfidencePill
              confidence={confidence}
              onDrill={onDrillIntoSource}
            />
          )}
        </div>
      ) : null}
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
