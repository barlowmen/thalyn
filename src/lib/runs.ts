/**
 * Run lifecycle types + Tauri bindings.
 *
 * Mirrors `brain/thalyn_brain/orchestration/state.py` and
 * `brain/thalyn_brain/runs.py`. Camel-case across the wire.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type RunStatus =
  | "pending"
  | "planning"
  | "awaiting_approval"
  | "running"
  | "paused"
  | "completed"
  | "errored"
  | "killed";

export type PlanNodeStatus =
  | "pending"
  | "in_progress"
  | "done"
  | "errored"
  | "skipped";

export type SandboxTier = "tier_0" | "tier_1" | "tier_2" | "tier_3";

export type PlanNode = {
  id: string;
  order: number;
  description: string;
  rationale: string;
  estimatedCost: Record<string, unknown>;
  status: PlanNodeStatus;
  parentId: string | null;
  subagentKind?: string | null;
  sandboxTier?: SandboxTier | null;
};

export type Plan = {
  goal: string;
  nodes: PlanNode[];
};

export type ActionLogEntry = {
  atMs: number;
  kind:
    | "tool_call"
    | "llm_call"
    | "decision"
    | "file_change"
    | "approval"
    | "drift_check"
    | "node_transition";
  payload: Record<string, unknown>;
};

export type Budget = {
  maxTokens?: number | null;
  maxSeconds?: number | null;
  maxIterations?: number | null;
};

export type BudgetConsumed = {
  tokensUsed: number;
  elapsedSeconds: number;
  iterations: number;
  startedAtMs: number;
};

export type RunHeader = {
  runId: string;
  projectId: string | null;
  parentRunId: string | null;
  status: RunStatus;
  title: string;
  providerId: string;
  startedAtMs: number;
  completedAtMs: number | null;
  driftScore: number;
  finalResponse: string;
  plan: Plan | null;
  sandboxTier?: SandboxTier | null;
  budget?: Budget | null;
  budgetConsumed?: BudgetConsumed | null;
  agentId?: string | null;
  parentLeadId?: string | null;
};

/**
 * Nested run-tree node — same shape as RunHeader plus the children
 * recursively beneath it. Returned by `runs.tree` so the renderer can
 * draw sub-agent tiles without a second pass over the index.
 */
export type RunTreeNode = RunHeader & {
  children: RunTreeNode[];
};

// --- Live event payloads -----------------------------------------------------

export type RunStatusEvent = {
  runId: string;
  status: RunStatus;
  parentRunId: string | null;
};
export type RunPlanUpdateEvent = { runId: string; plan: Plan };
export type RunActionLogEvent = { runId: string; entry: ActionLogEntry };
export type RunApprovalRequiredEvent = {
  runId: string;
  gateKind: "plan" | "depth" | "drift" | "budget";
  plan: Plan;
  threshold?: string;
  driftScore?: number;
  reason?: string;
  dimension?: string;
};

export type ApprovalDecision = "approve" | "edit" | "reject";

export type ApprovalResult = {
  runId: string;
  sessionId: string;
  providerId: string;
  status: RunStatus;
  finalResponse: string;
  actionLogSize: number;
  plan?: Plan;
};

export function listRuns(options?: {
  statuses?: RunStatus[];
  limit?: number;
}): Promise<{ runs: RunHeader[] }> {
  return invoke<{ runs: RunHeader[] }>("list_runs", options ?? {});
}

export function getRun(runId: string): Promise<RunHeader | null> {
  return invoke<RunHeader | null>("get_run", { runId });
}

export function getRunTree(runId: string): Promise<RunTreeNode | null> {
  return invoke<RunTreeNode | null>("get_run_tree", { runId });
}

export function killRun(
  runId: string,
): Promise<{ runId: string; status: RunStatus }> {
  return invoke<{ runId: string; status: RunStatus }>("kill_run", { runId });
}

export function approvePlan(args: {
  runId: string;
  providerId: string;
  decision: ApprovalDecision;
  editedPlan?: Plan;
  sessionId?: string;
}): Promise<ApprovalResult> {
  return invoke<ApprovalResult>("approve_plan", args);
}

export function subscribeRunStatus(
  handler: (event: RunStatusEvent) => void,
): Promise<UnlistenFn> {
  return listen<RunStatusEvent>("run:status", (e) => handler(e.payload));
}

export function subscribeRunPlanUpdate(
  handler: (event: RunPlanUpdateEvent) => void,
): Promise<UnlistenFn> {
  return listen<RunPlanUpdateEvent>("run:plan_update", (e) =>
    handler(e.payload),
  );
}

export function subscribeRunActionLog(
  handler: (event: RunActionLogEvent) => void,
): Promise<UnlistenFn> {
  return listen<RunActionLogEvent>("run:action_log", (e) =>
    handler(e.payload),
  );
}

export function subscribeRunApprovalRequired(
  handler: (event: RunApprovalRequiredEvent) => void,
): Promise<UnlistenFn> {
  return listen<RunApprovalRequiredEvent>("run:approval_required", (e) =>
    handler(e.payload),
  );
}
