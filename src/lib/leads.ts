/**
 * Lead lifecycle bindings for the renderer.
 *
 * Wraps the Tauri commands that proxy ``lead.list / spawn / pause /
 * resume / archive`` to the brain. Wire shape mirrors ``AgentRecord``
 * (camelCase keys) and the brain's lifecycle invariants — the
 * renderer treats these calls as side-effecting state-machine
 * transitions and refreshes its local view from the response.
 */

import { invoke } from "@tauri-apps/api/core";

export type LeadStatus = "active" | "paused" | "archived";
export type LeadKind = "lead" | "sub_lead";

export type LeadAgent = {
  agentId: string;
  kind: LeadKind | string;
  displayName: string;
  parentAgentId: string | null;
  projectId: string | null;
  scopeFacet: string | null;
  memoryNamespace: string;
  defaultProviderId: string;
  systemPrompt: string;
  status: LeadStatus;
  createdAtMs: number;
  lastActiveAtMs: number;
};

export type LeadListResult = {
  agents: LeadAgent[];
};

export type LeadMutationResult = {
  agent: LeadAgent;
};

export function listLeads(
  options: {
    projectId?: string;
    status?: LeadStatus;
    kind?: LeadKind;
  } = {},
): Promise<LeadListResult> {
  return invoke<LeadListResult>("lead_list", {
    projectId: options.projectId,
    status: options.status,
    kind: options.kind,
  });
}

export function spawnLead(input: {
  projectId: string;
  displayName?: string;
  defaultProviderId?: string;
  systemPrompt?: string;
}): Promise<LeadMutationResult> {
  return invoke<LeadMutationResult>("lead_spawn", input);
}

export function pauseLead(agentId: string): Promise<LeadMutationResult> {
  return invoke<LeadMutationResult>("lead_pause", { agentId });
}

export function resumeLead(agentId: string): Promise<LeadMutationResult> {
  return invoke<LeadMutationResult>("lead_resume", { agentId });
}

export function archiveLead(agentId: string): Promise<LeadMutationResult> {
  return invoke<LeadMutationResult>("lead_archive", { agentId });
}
