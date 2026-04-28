"""Migration 004 — fold v1 data into the v2 shape.

Creates the v2 entities every later stage assumes exist:

- The default project, ``slug='thalyn-default'``.
- The brain ``AGENT_RECORD`` (``kind='brain'``, ``display_name='Thalyn'``).
- The default project's lead ``AGENT_RECORD``
  (``kind='lead'``, ``display_name='Lead-Default'``).
- One eternal ``THREAD`` keyed to the user (``user_scope='self'``).

Existing v1 ``agent_runs`` rows are re-keyed under the default lead by
setting ``parent_lead_id`` (per ``02-architecture.md`` §5's migration
narrative). ``agent_id`` is left NULL because v1 didn't attribute runs
to a specific agent — later stages will populate it as runs spawn from
specific leads or workers.

Idempotency: an explicit guard checks for the canonical default
project by slug before inserting. Combined with yoyo's apply log,
this migration is safe under repeated invocation. The fixed agent /
project / thread IDs (``agent_brain``, ``agent_lead_default``,
``proj_default``, ``thread_self``) are deliberately stable so later
stages can reference them without loading lookups every time.
"""

from __future__ import annotations

import time
from typing import Any

from yoyo import step

DEFAULT_PROJECT_SLUG = "thalyn-default"
BRAIN_AGENT_ID = "agent_brain"
DEFAULT_LEAD_AGENT_ID = "agent_lead_default"
DEFAULT_THREAD_ID = "thread_self"
DEFAULT_PROJECT_ID = "proj_default"


def _seed_v2_entities(conn: Any) -> None:
    cursor = conn.cursor()
    existing = cursor.execute(
        "SELECT project_id FROM projects WHERE slug = ?",
        (DEFAULT_PROJECT_SLUG,),
    ).fetchone()
    if existing is not None:
        return
    now = int(time.time() * 1000)

    cursor.execute(
        """
        INSERT INTO projects
            (project_id, name, slug, workspace_path, repo_remote,
             lead_agent_id, memory_namespace, conversation_tag,
             roadmap, provider_config_json, connector_grants_json,
             local_only, status, created_at_ms, last_active_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            DEFAULT_PROJECT_ID,
            "Default",
            DEFAULT_PROJECT_SLUG,
            None,
            None,
            None,
            "default",
            "Default",
            "",
            None,
            None,
            0,
            "active",
            now,
            now,
        ),
    )

    cursor.execute(
        """
        INSERT INTO agent_records
            (agent_id, kind, display_name, parent_agent_id,
             project_id, scope_facet, memory_namespace,
             default_provider_id, system_prompt, status,
             created_at_ms, last_active_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            BRAIN_AGENT_ID,
            "brain",
            "Thalyn",
            None,
            None,
            None,
            "brain",
            "anthropic",
            "",
            "active",
            now,
            now,
        ),
    )

    cursor.execute(
        """
        INSERT INTO agent_records
            (agent_id, kind, display_name, parent_agent_id,
             project_id, scope_facet, memory_namespace,
             default_provider_id, system_prompt, status,
             created_at_ms, last_active_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            DEFAULT_LEAD_AGENT_ID,
            "lead",
            "Lead-Default",
            None,
            DEFAULT_PROJECT_ID,
            None,
            "lead-default",
            "anthropic",
            "",
            "active",
            now,
            now,
        ),
    )

    cursor.execute(
        "UPDATE projects SET lead_agent_id = ? WHERE project_id = ?",
        (DEFAULT_LEAD_AGENT_ID, DEFAULT_PROJECT_ID),
    )

    cursor.execute(
        """
        INSERT INTO threads (thread_id, user_scope, created_at_ms, last_active_at_ms)
        VALUES (?, ?, ?, ?)
        """,
        (DEFAULT_THREAD_ID, "self", now, now),
    )

    cursor.execute(
        "UPDATE agent_runs SET parent_lead_id = ? WHERE parent_lead_id IS NULL",
        (DEFAULT_LEAD_AGENT_ID,),
    )


steps = [step(_seed_v2_entities)]
