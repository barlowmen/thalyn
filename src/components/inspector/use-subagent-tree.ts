import { useEffect, useState } from "react";

import {
  getRun,
  getRunTree,
  type Budget,
  type BudgetConsumed,
  type RunHeader,
  type RunStatus,
  type RunTreeNode,
  type SandboxTier,
  subscribeRunStatus,
} from "@/lib/runs";

export type SubAgentTile = {
  runId: string;
  parentRunId: string | null;
  title: string;
  status: RunStatus;
  startedAtMs: number;
  sandboxTier?: SandboxTier | null;
  driftScore: number;
  budget?: Budget | null;
  budgetConsumed?: BudgetConsumed | null;
};

/**
 * Track the sub-agent subtree rooted at ``rootRunId``.
 *
 * Strategy: seed from `runs.tree` once on mount, then listen for
 * `run:status` events. The brain stamps each event with the run's
 * ``parentRunId`` so we can route descendant events without a second
 * lookup. Existing tiles get a status patch in place; new tiles
 * trigger a one-shot ``runs.get`` to backfill the title.
 *
 * Returns the tiles in start-time order so the renderer's list is
 * stable across re-renders.
 */
export function useSubAgentTree(rootRunId: string | null): SubAgentTile[] {
  const [tiles, setTiles] = useState<Record<string, SubAgentTile>>({});

  useEffect(() => {
    if (!rootRunId) {
      setTiles({});
      return;
    }
    let active = true;
    setTiles({});

    void getRunTree(rootRunId).then((tree) => {
      if (!active || !tree) return;
      setTiles(flatten(tree, rootRunId));
    });

    const sub = subscribeRunStatus((event) => {
      if (!active) return;
      if (event.parentRunId === null) return;
      setTiles((current) => {
        if (!isInSubtree(event.parentRunId, rootRunId, current)) {
          return current;
        }
        const existing = current[event.runId];
        if (existing) {
          return {
            ...current,
            [event.runId]: { ...existing, status: event.status },
          };
        }
        // Backfill the header for a freshly-spawned descendant. The
        // tile lands as soon as the lookup resolves.
        void getRun(event.runId).then((header) => {
          if (!active || !header) return;
          setTiles((later) => ({
            ...later,
            [event.runId]: tileFromHeader(header),
          }));
        });
        return current;
      });
    });

    return () => {
      active = false;
      void sub.then((fn) => fn());
    };
  }, [rootRunId]);

  return Object.values(tiles).sort((a, b) => a.startedAtMs - b.startedAtMs);
}

function flatten(
  tree: RunTreeNode,
  rootRunId: string,
): Record<string, SubAgentTile> {
  const out: Record<string, SubAgentTile> = {};
  const walk = (node: RunTreeNode) => {
    if (node.runId !== rootRunId) {
      out[node.runId] = tileFromHeader(node);
    }
    node.children.forEach(walk);
  };
  walk(tree);
  return out;
}

function tileFromHeader(header: RunHeader): SubAgentTile {
  return {
    runId: header.runId,
    parentRunId: header.parentRunId,
    title: header.title,
    status: header.status,
    startedAtMs: header.startedAtMs,
    sandboxTier: header.sandboxTier ?? null,
    driftScore: header.driftScore ?? 0,
    budget: header.budget ?? null,
    budgetConsumed: header.budgetConsumed ?? null,
  };
}

function isInSubtree(
  parentRunId: string | null,
  rootRunId: string,
  tiles: Record<string, SubAgentTile>,
): boolean {
  if (!parentRunId) return false;
  if (parentRunId === rootRunId) return true;
  return parentRunId in tiles;
}
