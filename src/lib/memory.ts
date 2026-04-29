/**
 * Memory store types + Tauri bindings.
 *
 * Mirrors `brain/thalyn_brain/memory.py`. Camel-case across the wire.
 *
 * The five-tier model from `01-requirements.md` §F6 splits into two
 * halves by lifetime. `working` and `session` are ephemeral and
 * never persist as `MEMORY_ENTRY` rows; the renderer references them
 * by name (e.g. for badge labels) but the SQLite store rejects them
 * on insert. The four scopes below are the persisted tiers.
 */

import { invoke } from "@tauri-apps/api/core";

export type MemoryScope = "project" | "personal" | "episodic" | "agent";
export type MemoryTier = "working" | "session" | MemoryScope;
export type MemoryKind = "fact" | "preference" | "reference" | "feedback";

export type MemoryEntry = {
  memoryId: string;
  projectId: string | null;
  scope: MemoryScope;
  kind: MemoryKind;
  body: string;
  author: string;
  createdAtMs: number;
  updatedAtMs: number;
};

export function listMemory(args?: {
  projectId?: string;
  scopes?: MemoryScope[];
  limit?: number;
}): Promise<{ entries: MemoryEntry[] }> {
  return invoke<{ entries: MemoryEntry[] }>("list_memory", {
    projectId: args?.projectId ?? null,
    scopes: args?.scopes ?? null,
    limit: args?.limit ?? null,
  });
}

export function addMemory(args: {
  body: string;
  scope: MemoryScope;
  kind: MemoryKind;
  author: string;
  projectId?: string;
}): Promise<{ entry: MemoryEntry }> {
  return invoke<{ entry: MemoryEntry }>("add_memory", {
    body: args.body,
    scope: args.scope,
    kind: args.kind,
    author: args.author,
    projectId: args.projectId ?? null,
  });
}

export function updateMemory(args: {
  memoryId: string;
  body?: string;
  scope?: MemoryScope;
  kind?: MemoryKind;
}): Promise<{ entry: MemoryEntry | null }> {
  return invoke<{ entry: MemoryEntry | null }>("update_memory", {
    memoryId: args.memoryId,
    body: args.body ?? null,
    kind: args.kind ?? null,
    scope: args.scope ?? null,
  });
}

export function deleteMemory(
  memoryId: string,
): Promise<{ deleted: boolean; memoryId: string }> {
  return invoke<{ deleted: boolean; memoryId: string }>("delete_memory", {
    memoryId,
  });
}
