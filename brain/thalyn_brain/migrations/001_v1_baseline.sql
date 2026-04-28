-- Migration 001 — v1 baseline schema.
--
-- Captures the v1 schema as the starting line for v2 migrations. Per
-- ADR-0028 the schema lives exclusively under this directory; the five
-- v1 stores (runs, schedules, memory, mcp/registry, email/store) no
-- longer carry inline CREATE TABLE blocks.
--
-- The CREATE statements use IF NOT EXISTS so the migration is a no-op
-- on databases brought forward from v1; for fresh installs it creates
-- all five tables in their canonical shape. Migration 002 backfills
-- the late-v1 columns on agent_runs for older databases.

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_run_id TEXT,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    drift_score REAL NOT NULL DEFAULT 0,
    final_response TEXT NOT NULL DEFAULT '',
    plan_json TEXT,
    sandbox_tier TEXT,
    budget_json TEXT,
    budget_consumed_json TEXT
);

CREATE INDEX IF NOT EXISTS agent_runs_status_idx ON agent_runs(status);
CREATE INDEX IF NOT EXISTS agent_runs_started_idx ON agent_runs(started_at_ms);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT NOT NULL,
    nl_input TEXT NOT NULL,
    cron TEXT NOT NULL,
    run_template_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at_ms INTEGER,
    last_run_at_ms INTEGER,
    last_run_id TEXT,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS schedules_next_idx ON schedules(next_run_at_ms);
CREATE INDEX IF NOT EXISTS schedules_enabled_idx ON schedules(enabled);

CREATE TABLE IF NOT EXISTS memory_entries (
    memory_id TEXT PRIMARY KEY,
    project_id TEXT,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    embedding_json TEXT
);

CREATE INDEX IF NOT EXISTS memory_scope_idx ON memory_entries(scope);
CREATE INDEX IF NOT EXISTS memory_project_idx ON memory_entries(project_id);
CREATE INDEX IF NOT EXISTS memory_kind_idx ON memory_entries(kind);
CREATE INDEX IF NOT EXISTS memory_created_idx ON memory_entries(created_at_ms);

CREATE TABLE IF NOT EXISTS mcp_connectors (
    connector_id TEXT PRIMARY KEY,
    descriptor_json TEXT NOT NULL,
    granted_tools_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    installed_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS email_accounts (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    address TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS email_accounts_provider_idx ON email_accounts(provider);
