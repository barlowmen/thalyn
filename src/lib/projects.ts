/**
 * Project bindings: ``project.list`` / ``project.create`` /
 * ``project.update`` / ``project.archive`` / ``project.pause`` /
 * ``project.resume`` from the renderer's perspective.
 *
 * The brain owns the project store; these helpers wrap the Tauri
 * commands that proxy ``project_list`` / ``project_create`` / … to
 * the brain RPC. Wire shape mirrors the Python side
 * (``Project.to_wire``) — camelCase keys, snake_case status enum
 * values.
 */

import { invoke } from "@tauri-apps/api/core";

export type ProjectStatus = "active" | "paused" | "archived";

export type Project = {
  projectId: string;
  name: string;
  slug: string;
  workspacePath: string | null;
  repoRemote: string | null;
  leadAgentId: string | null;
  memoryNamespace: string;
  conversationTag: string;
  roadmap: string;
  providerConfig: Record<string, unknown> | null;
  connectorGrants: Record<string, unknown> | null;
  localOnly: boolean;
  status: ProjectStatus;
  createdAtMs: number;
  lastActiveAtMs: number;
};

export type ProjectListResult = {
  projects: Project[];
};

export type ProjectMutationResult = {
  project: Project;
};

/** The seeded default project id (migration 004). */
export const DEFAULT_PROJECT_ID = "proj_default";

/** Pull projects, optionally filtered by lifecycle status. */
export function listProjects(
  options: { status?: ProjectStatus } = {},
): Promise<ProjectListResult> {
  return invoke<ProjectListResult>("project_list", {
    status: options.status,
  });
}

/** Create a new active project. The slug is derived from ``name``. */
export function createProject(input: {
  name: string;
  workspacePath?: string;
  repoRemote?: string;
  localOnly?: boolean;
}): Promise<ProjectMutationResult> {
  return invoke<ProjectMutationResult>("project_create", {
    name: input.name,
    workspacePath: input.workspacePath,
    repoRemote: input.repoRemote,
    localOnly: input.localOnly,
  });
}

/** Rename a project or flip its local-only flag. */
export function updateProject(input: {
  projectId: string;
  name?: string;
  localOnly?: boolean;
}): Promise<ProjectMutationResult> {
  return invoke<ProjectMutationResult>("project_update", {
    projectId: input.projectId,
    name: input.name,
    localOnly: input.localOnly,
  });
}

export function pauseProject(projectId: string): Promise<ProjectMutationResult> {
  return invoke<ProjectMutationResult>("project_pause", { projectId });
}

export function resumeProject(projectId: string): Promise<ProjectMutationResult> {
  return invoke<ProjectMutationResult>("project_resume", { projectId });
}

export function archiveProject(projectId: string): Promise<ProjectMutationResult> {
  return invoke<ProjectMutationResult>("project_archive", { projectId });
}
