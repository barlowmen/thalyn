/**
 * Day-divider + "since-we-last-spoke" digest greeting.
 *
 * Per F1.3 / §12 the eternal thread renders a digest as the first
 * message of each day's slice. On chat-surface mount, this component
 * fetches the latest rolling digest plus the most recent turn; if the
 * most recent turn was on a prior day, it shows a divider stamped
 * with today's date plus a system-message bubble carrying the
 * structured summary (topics / decisions / open threads).
 *
 * The greeting is purely additive — it sits above whatever the
 * existing message list renders. When there's no prior turn, no
 * digest, or the latest turn was today, the component returns null
 * and yields no layout.
 */

import { useEffect, useState } from "react";

import { ProjectTag } from "@/components/chat/project-tag";
import {
  ETERNAL_THREAD_ID,
  type ProjectBreakdownEntry,
  type SessionDigest,
  digestLatest,
  threadRecent,
} from "@/lib/threads";

type Props = {
  /** Override for tests / Storybook; defaults to the eternal thread. */
  threadId?: string;
  /**
   * Override the day-boundary clock so tests can pin "today" without
   * faking the system clock. Defaults to ``Date.now()``.
   */
  nowMs?: number;
  /**
   * Direct injection — bypasses the IPC fetches. Production renders
   * call without this; tests / stories supply both fields to render
   * deterministically.
   */
  preview?: {
    digest: SessionDigest | null;
    lastTurnAtMs: number | null;
  };
};

type LoadedState =
  | { kind: "loading" }
  | { kind: "ready"; digest: SessionDigest | null; lastTurnAtMs: number | null }
  | { kind: "error" };

export function ThreadDigestGreeting({
  threadId = ETERNAL_THREAD_ID,
  nowMs,
  preview,
}: Props) {
  const [state, setState] = useState<LoadedState>(() =>
    preview
      ? {
          kind: "ready",
          digest: preview.digest,
          lastTurnAtMs: preview.lastTurnAtMs,
        }
      : { kind: "loading" },
  );

  useEffect(() => {
    if (preview) return;
    let cancelled = false;
    (async () => {
      try {
        const [digestResult, recentResult] = await Promise.all([
          digestLatest(threadId),
          threadRecent(threadId, { limit: 1 }),
        ]);
        if (cancelled) return;
        const lastTurn = recentResult.turns[0] ?? null;
        setState({
          kind: "ready",
          digest: digestResult.digest,
          lastTurnAtMs: lastTurn?.atMs ?? null,
        });
      } catch {
        if (cancelled) return;
        setState({ kind: "error" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [threadId, preview]);

  if (state.kind !== "ready") return null;
  if (!shouldGreet(state.lastTurnAtMs, nowMs)) return null;
  if (!state.digest) return null;

  return (
    <DayDividerGreeting
      digest={state.digest}
      todayMs={nowMs ?? Date.now()}
    />
  );
}

function shouldGreet(lastTurnAtMs: number | null, nowMs?: number): boolean {
  if (lastTurnAtMs === null) return false;
  const today = startOfDay(nowMs ?? Date.now());
  const last = startOfDay(lastTurnAtMs);
  return today > last;
}

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

type GreetingProps = {
  digest: SessionDigest;
  todayMs: number;
};

function DayDividerGreeting({ digest, todayMs }: GreetingProps) {
  const summary = digest.structuredSummary ?? {};
  const topics = stringList(summary.topics);
  const decisions = stringList(summary.decisions);
  const openThreads = stringList(summary.open_threads);
  const breakdown = projectBreakdown(summary.project_breakdown);

  const todayLabel = new Date(todayMs).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  return (
    <section
      aria-label="Since we last spoke"
      className="mb-3 space-y-3"
    >
      <div className="flex items-center gap-3" role="separator">
        <div className="h-px flex-1 bg-border" aria-hidden />
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {todayLabel}
        </span>
        <div className="h-px flex-1 bg-border" aria-hidden />
      </div>
      <div className="rounded-md border border-border bg-card px-4 py-3 text-sm">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Since we last spoke
        </p>
        {breakdown.length >= 2 ? (
          <ProjectBreakdownList entries={breakdown} />
        ) : (
          <ul className="space-y-1.5">
            {topics.length > 0 && (
              <li>
                <span className="font-medium">Topics:</span>{" "}
                <span className="text-muted-foreground">
                  {topics.join(", ")}
                </span>
              </li>
            )}
            {decisions.length > 0 && (
              <li>
                <span className="font-medium">Decisions:</span>{" "}
                <span className="text-muted-foreground">
                  {decisions.join(", ")}
                </span>
              </li>
            )}
            {openThreads.length > 0 && (
              <li>
                <span className="font-medium">Open threads:</span>{" "}
                <span className="text-muted-foreground">
                  {openThreads.join(", ")}
                </span>
              </li>
            )}
            {topics.length === 0 &&
              decisions.length === 0 &&
              openThreads.length === 0 && (
                <li className="text-muted-foreground">
                  Nothing landed in the last digest worth flagging — pick
                  up wherever feels right.
                </li>
              )}
          </ul>
        )}
      </div>
    </section>
  );
}

function ProjectBreakdownList({ entries }: { entries: ProjectBreakdownEntry[] }) {
  return (
    <ul className="space-y-3">
      {entries.map((entry) => {
        const topics = stringList(entry.topics);
        const decisions = stringList(entry.decisions);
        const open = stringList(entry.open_threads);
        const seed = entry.projectSlug || entry.projectId;
        return (
          <li key={entry.projectId} className="space-y-1.5">
            <ProjectTag seed={seed} name={entry.projectName} />
            <ul className="space-y-1 pl-1">
              {topics.length > 0 && (
                <li>
                  <span className="font-medium">Topics:</span>{" "}
                  <span className="text-muted-foreground">
                    {topics.join(", ")}
                  </span>
                </li>
              )}
              {decisions.length > 0 && (
                <li>
                  <span className="font-medium">Decisions:</span>{" "}
                  <span className="text-muted-foreground">
                    {decisions.join(", ")}
                  </span>
                </li>
              )}
              {open.length > 0 && (
                <li>
                  <span className="font-medium">Open threads:</span>{" "}
                  <span className="text-muted-foreground">
                    {open.join(", ")}
                  </span>
                </li>
              )}
              {topics.length === 0 && decisions.length === 0 && open.length === 0 && (
                <li className="text-muted-foreground">No notable activity.</li>
              )}
            </ul>
          </li>
        );
      })}
    </ul>
  );
}

function projectBreakdown(value: unknown): ProjectBreakdownEntry[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry): ProjectBreakdownEntry[] => {
    if (
      entry &&
      typeof entry === "object" &&
      typeof (entry as ProjectBreakdownEntry).projectId === "string" &&
      typeof (entry as ProjectBreakdownEntry).projectName === "string"
    ) {
      return [entry as ProjectBreakdownEntry];
    }
    return [];
  });
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string" && v.length > 0);
}
