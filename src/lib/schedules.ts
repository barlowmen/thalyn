/**
 * Schedule index types + Tauri bindings.
 *
 * Mirrors `brain/thalyn_brain/schedules.py`. Camel-case across the wire.
 */

import { invoke } from "@tauri-apps/api/core";

export type RunTemplate = {
  prompt: string;
  providerId?: string | null;
  budget?: Record<string, number | null> | null;
  systemPrompt?: string | null;
};

export type Schedule = {
  scheduleId: string;
  projectId: string | null;
  title: string;
  nlInput: string;
  cron: string;
  runTemplate: RunTemplate;
  enabled: boolean;
  nextRunAtMs: number | null;
  lastRunAtMs: number | null;
  lastRunId: string | null;
  createdAtMs: number;
};

export type CronTranslation = {
  cron: string;
  explanation: string;
  nlInput: string;
  valid: boolean;
  error: string | null;
};

export function listSchedules(): Promise<{ schedules: Schedule[] }> {
  return invoke<{ schedules: Schedule[] }>("list_schedules");
}

export function createSchedule(args: {
  title: string;
  nlInput?: string;
  cron?: string;
  runTemplate: RunTemplate;
}): Promise<{ schedule: Schedule }> {
  return invoke<{ schedule: Schedule }>("create_schedule", {
    title: args.title,
    nlInput: args.nlInput ?? null,
    cron: args.cron ?? null,
    runTemplate: args.runTemplate,
  });
}

export function deleteSchedule(
  scheduleId: string,
): Promise<{ deleted: boolean; scheduleId: string }> {
  return invoke<{ deleted: boolean; scheduleId: string }>("delete_schedule", {
    scheduleId,
  });
}

export function translateCron(args: {
  nlInput: string;
  providerId?: string;
}): Promise<CronTranslation> {
  return invoke<CronTranslation>("translate_cron", {
    nlInput: args.nlInput,
    providerId: args.providerId ?? null,
  });
}
