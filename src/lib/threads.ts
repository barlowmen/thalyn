/**
 * Eternal-thread bindings: ``thread.recent`` + ``digest.latest`` from
 * the renderer's perspective.
 *
 * The brain owns the thread store; these helpers wrap the Tauri
 * commands that proxy ``thread_recent`` / ``digest_latest`` to the
 * brain RPC. Wire shape mirrors the Python side
 * (``ThreadTurn.to_wire``, ``SessionDigest.to_wire``) — camelCase
 * keys, snake_case status enum values.
 */

import { invoke } from "@tauri-apps/api/core";

export type ThreadTurnStatus = "in_progress" | "completed";

export type ThreadTurn = {
  turnId: string;
  threadId: string;
  projectId: string | null;
  agentId: string | null;
  role: string;
  body: string;
  provenance: string | null;
  confidence: number | null;
  episodicIndexPtr: string | null;
  atMs: number;
  status: ThreadTurnStatus;
};

export type ThreadRecentResult = {
  threadId: string;
  turns: ThreadTurn[];
};

export type SessionDigest = {
  digestId: string;
  threadId: string;
  windowStartMs: number;
  windowEndMs: number;
  structuredSummary: {
    topics?: string[];
    decisions?: string[];
    open_threads?: string[];
    [key: string]: unknown;
  };
  secondLevelSummaryOf: string | null;
};

export type DigestLatestResult = {
  threadId: string;
  digest: SessionDigest | null;
};

/** The seeded eternal thread id (migration 004). */
export const ETERNAL_THREAD_ID = "thread_self";

/** Pull recent completed turns for a thread, newest-first. */
export function threadRecent(
  threadId: string,
  options: { limit?: number } = {},
): Promise<ThreadRecentResult> {
  return invoke<ThreadRecentResult>("thread_recent", {
    threadId,
    limit: options.limit,
  });
}

/** Read the most recent rolling digest for a thread, if any. */
export function digestLatest(threadId: string): Promise<DigestLatestResult> {
  return invoke<DigestLatestResult>("digest_latest", { threadId });
}
