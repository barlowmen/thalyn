-- Migration 003 — v2 schema.
--
-- Lays in the data-model bones for the v2 build per 02-architecture.md
-- §5: the eternal-thread tables (threads, thread_turns, session_digests),
-- the agent registry (agent_records), the project entity (projects),
-- the auth-backend split (auth_backends), worker routing
-- (routing_overrides), approvals (approvals), and the cross-run action
-- log header (action_log). Existing tables (agent_runs, memory_entries)
-- gain the FK columns the new entities depend on.
--
-- Foreign keys are declared so SQLite (with PRAGMA foreign_keys = ON,
-- set in every store's _open) enforces referential integrity from
-- day one. The PROJECT and AGENT_RECORD circular reference (a project
-- has a lead, a lead belongs to a project) is resolved by making both
-- FK columns nullable. Migration 004 backfills v1 data into the new
-- shape under that pattern.

-- ----------------------------------------------------------------- --
-- Agent registry + projects (created first because other v2 tables
-- reference them; the circular FK between the two is handled by
-- nullable columns).
-- ----------------------------------------------------------------- --

CREATE TABLE IF NOT EXISTS agent_records (
    agent_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    display_name TEXT NOT NULL,
    parent_agent_id TEXT
        REFERENCES agent_records(agent_id) ON DELETE SET NULL,
    project_id TEXT,
    scope_facet TEXT,
    memory_namespace TEXT NOT NULL,
    default_provider_id TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at_ms INTEGER NOT NULL,
    last_active_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS agent_records_kind_idx ON agent_records(kind);
CREATE INDEX IF NOT EXISTS agent_records_parent_idx ON agent_records(parent_agent_id);
CREATE INDEX IF NOT EXISTS agent_records_project_idx ON agent_records(project_id);
CREATE INDEX IF NOT EXISTS agent_records_status_idx ON agent_records(status);

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    workspace_path TEXT,
    repo_remote TEXT,
    lead_agent_id TEXT
        REFERENCES agent_records(agent_id) ON DELETE SET NULL,
    memory_namespace TEXT NOT NULL,
    conversation_tag TEXT NOT NULL,
    roadmap TEXT NOT NULL DEFAULT '',
    provider_config_json TEXT,
    connector_grants_json TEXT,
    local_only INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at_ms INTEGER NOT NULL,
    last_active_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS projects_status_idx ON projects(status);

-- ----------------------------------------------------------------- --
-- Eternal thread + rolling summarizer
-- ----------------------------------------------------------------- --

CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    user_scope TEXT NOT NULL DEFAULT 'self',
    created_at_ms INTEGER NOT NULL,
    last_active_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_turns (
    turn_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL
        REFERENCES threads(thread_id) ON DELETE CASCADE,
    project_id TEXT
        REFERENCES projects(project_id) ON DELETE SET NULL,
    agent_id TEXT
        REFERENCES agent_records(agent_id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    body TEXT NOT NULL,
    provenance_json TEXT,
    confidence_json TEXT,
    episodic_index_ptr_json TEXT,
    at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS thread_turns_thread_at_idx
    ON thread_turns(thread_id, at_ms);
CREATE INDEX IF NOT EXISTS thread_turns_project_at_idx
    ON thread_turns(project_id, at_ms);

CREATE TABLE IF NOT EXISTS session_digests (
    digest_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL
        REFERENCES threads(thread_id) ON DELETE CASCADE,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    structured_summary_json TEXT NOT NULL,
    second_level_summary_of TEXT
        REFERENCES session_digests(digest_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS session_digests_thread_idx
    ON session_digests(thread_id, window_end_ms);

-- ----------------------------------------------------------------- --
-- Auth backends + routing overrides
-- ----------------------------------------------------------------- --

CREATE TABLE IF NOT EXISTS auth_backends (
    auth_backend_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS routing_overrides (
    routing_override_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL
        REFERENCES projects(project_id) ON DELETE CASCADE,
    task_tag TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE(project_id, task_tag)
);

CREATE INDEX IF NOT EXISTS routing_overrides_project_idx
    ON routing_overrides(project_id);

-- ----------------------------------------------------------------- --
-- Approvals + cross-run action log header
-- ----------------------------------------------------------------- --

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL
        REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    gate_kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    context_json TEXT,
    requested_at_ms INTEGER NOT NULL,
    resolved_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS approvals_run_idx ON approvals(run_id);
CREATE INDEX IF NOT EXISTS approvals_status_idx ON approvals(status);

CREATE TABLE IF NOT EXISTS action_log (
    action_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL
        REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    at_ms INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS action_log_run_at_idx
    ON action_log(run_id, at_ms);
CREATE INDEX IF NOT EXISTS action_log_kind_idx ON action_log(kind);

-- ----------------------------------------------------------------- --
-- Extensions to existing v1 tables
-- ----------------------------------------------------------------- --

ALTER TABLE agent_runs ADD COLUMN agent_id TEXT
    REFERENCES agent_records(agent_id) ON DELETE SET NULL;
ALTER TABLE agent_runs ADD COLUMN parent_lead_id TEXT
    REFERENCES agent_records(agent_id) ON DELETE SET NULL;
ALTER TABLE agent_runs ADD COLUMN task_tags_json TEXT;

CREATE INDEX IF NOT EXISTS agent_runs_agent_idx ON agent_runs(agent_id);
CREATE INDEX IF NOT EXISTS agent_runs_parent_lead_idx
    ON agent_runs(parent_lead_id);

ALTER TABLE memory_entries ADD COLUMN agent_id TEXT
    REFERENCES agent_records(agent_id) ON DELETE SET NULL;
ALTER TABLE memory_entries ADD COLUMN provenance_json TEXT;

CREATE INDEX IF NOT EXISTS memory_entries_agent_idx
    ON memory_entries(agent_id);
