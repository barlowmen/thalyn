/**
 * Memory store types + Tauri bindings.
 *
 * Mirrors `brain/thalyn_brain/memory.py`. Camel-case across the wire.
 */

import { invoke } from "@tauri-apps/api/core";

export type MemoryScope = "user" | "project" | "agent";
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
