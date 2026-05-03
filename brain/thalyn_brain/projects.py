"""Project store — first-class project entity for v2.

Each project owns a lead agent (``lead_agent_id``), a memory namespace,
a conversation tag for the eternal thread, and per-project provider /
connector / privacy configuration. The v0.20 module landed CRUD;
v0.31's multi-project surface adds the lifecycle helpers
(``create``, ``update_name``, ``set_status``, ``touch_active_at``)
the renderer drives through ``project.*``.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)

PROJECT_STATUSES = frozenset({"active", "paused", "archived"})

_SLUG_NORMALISE_RE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_BASE_LEN = 48


def new_project_id() -> str:
    return f"proj_{uuid.uuid4().hex}"


def slugify(name: str) -> str:
    """Lower-kebab the name; fall back to a uuid suffix when empty.

    The slug is the stable handle the lead's ``memory_namespace`` is
    keyed off and the conversation-tag default; it has to round-trip
    through the filesystem and SQLite harmlessly. The fallback keeps
    every project nameable even when the user types only emoji or
    punctuation — the unique-ness check in ``ProjectsStore.create``
    then adds a numeric suffix on top if the base collides with an
    existing row.
    """
    lowered = (name or "").strip().lower()
    cleaned = _SLUG_NORMALISE_RE.sub("-", lowered).strip("-")
    if not cleaned:
        return f"project-{uuid.uuid4().hex[:8]}"
    return cleaned[:_MAX_SLUG_BASE_LEN]


@dataclass
class Project:
    project_id: str
    name: str
    slug: str
    workspace_path: str | None
    repo_remote: str | None
    lead_agent_id: str | None
    memory_namespace: str
    conversation_tag: str
    roadmap: str
    provider_config: dict[str, Any] | None
    connector_grants: dict[str, Any] | None
    local_only: bool
    status: str
    created_at_ms: int
    last_active_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "projectId": self.project_id,
            "name": self.name,
            "slug": self.slug,
            "workspacePath": self.workspace_path,
            "repoRemote": self.repo_remote,
            "leadAgentId": self.lead_agent_id,
            "memoryNamespace": self.memory_namespace,
            "conversationTag": self.conversation_tag,
            "roadmap": self.roadmap,
            "providerConfig": self.provider_config,
            "connectorGrants": self.connector_grants,
            "localOnly": self.local_only,
            "status": self.status,
            "createdAtMs": self.created_at_ms,
            "lastActiveAtMs": self.last_active_at_ms,
        }


class ProjectsStore:
    def __init__(self, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        apply_pending_migrations(data_dir=base)
        self._db_path = base / "app.db"
        self._lock = asyncio.Lock()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def insert(self, project: Project) -> None:
        if project.status not in PROJECT_STATUSES:
            raise ValueError(f"invalid project status: {project.status}")
        async with self._lock:
            await asyncio.to_thread(self._insert_sync, project)

    def _insert_sync(self, project: Project) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO projects
                    (project_id, name, slug, workspace_path, repo_remote,
                     lead_agent_id, memory_namespace, conversation_tag,
                     roadmap, provider_config_json, connector_grants_json,
                     local_only, status, created_at_ms, last_active_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.project_id,
                    project.name,
                    project.slug,
                    project.workspace_path,
                    project.repo_remote,
                    project.lead_agent_id,
                    project.memory_namespace,
                    project.conversation_tag,
                    project.roadmap,
                    json.dumps(project.provider_config)
                    if project.provider_config is not None
                    else None,
                    json.dumps(project.connector_grants)
                    if project.connector_grants is not None
                    else None,
                    1 if project.local_only else 0,
                    project.status,
                    project.created_at_ms,
                    project.last_active_at_ms,
                ),
            )

    async def get(self, project_id: str) -> Project | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, project_id)

    def _get_sync(self, project_id: str) -> Project | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return self._from_row(row) if row else None

    async def get_by_slug(self, slug: str) -> Project | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_by_slug_sync, slug)

    def _get_by_slug_sync(self, slug: str) -> Project | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE slug = ?",
                (slug,),
            ).fetchone()
            return self._from_row(row) if row else None

    async def list_all(self, *, status: str | None = None) -> list[Project]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync, status=status)

    def _list_sync(self, *, status: str | None) -> list[Project]:
        with self._open() as conn:
            if status is not None:
                rows = conn.execute(
                    "SELECT * FROM projects WHERE status = ? ORDER BY created_at_ms ASC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM projects ORDER BY created_at_ms ASC").fetchall()
            return [self._from_row(row) for row in rows]

    async def create(
        self,
        *,
        name: str,
        workspace_path: str | None = None,
        repo_remote: str | None = None,
        local_only: bool = False,
        provider_config: dict[str, Any] | None = None,
        connector_grants: dict[str, Any] | None = None,
        roadmap: str = "",
    ) -> Project:
        """Build a ``Project`` with derived defaults and persist it.

        Slug derivation uses ``slugify(name)``; collisions append a
        numeric suffix (``alpha``, ``alpha-2``, ``alpha-3``, …) so the
        user's name is preserved verbatim even when two projects share
        a name. The ``memory_namespace`` mirrors the slug so the lead
        memory tier matches the project handle.
        """
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValueError("project name is required")
        async with self._lock:
            return await asyncio.to_thread(
                self._create_sync,
                cleaned_name,
                workspace_path,
                repo_remote,
                local_only,
                provider_config,
                connector_grants,
                roadmap,
            )

    def _create_sync(
        self,
        name: str,
        workspace_path: str | None,
        repo_remote: str | None,
        local_only: bool,
        provider_config: dict[str, Any] | None,
        connector_grants: dict[str, Any] | None,
        roadmap: str,
    ) -> Project:
        base_slug = slugify(name)
        with self._open() as conn:
            slug = self._unique_slug(conn, base_slug)
            now = int(time.time() * 1000)
            project = Project(
                project_id=new_project_id(),
                name=name,
                slug=slug,
                workspace_path=workspace_path,
                repo_remote=repo_remote,
                lead_agent_id=None,
                memory_namespace=slug,
                conversation_tag=name,
                roadmap=roadmap,
                provider_config=provider_config,
                connector_grants=connector_grants,
                local_only=local_only,
                status="active",
                created_at_ms=now,
                last_active_at_ms=now,
            )
            conn.execute(
                """
                INSERT INTO projects
                    (project_id, name, slug, workspace_path, repo_remote,
                     lead_agent_id, memory_namespace, conversation_tag,
                     roadmap, provider_config_json, connector_grants_json,
                     local_only, status, created_at_ms, last_active_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.project_id,
                    project.name,
                    project.slug,
                    project.workspace_path,
                    project.repo_remote,
                    project.lead_agent_id,
                    project.memory_namespace,
                    project.conversation_tag,
                    project.roadmap,
                    json.dumps(project.provider_config)
                    if project.provider_config is not None
                    else None,
                    json.dumps(project.connector_grants)
                    if project.connector_grants is not None
                    else None,
                    1 if project.local_only else 0,
                    project.status,
                    project.created_at_ms,
                    project.last_active_at_ms,
                ),
            )
            return project

    @staticmethod
    def _unique_slug(conn: sqlite3.Connection, base_slug: str) -> str:
        """Pick a slug that doesn't collide with an existing row.

        Returns the base slug unchanged when free; otherwise appends
        ``-2``, ``-3``, … until a free slot is found. The check runs
        inside the same connection (and therefore the same SQLite
        write lock under WAL) the insert will use, so a parallel
        ``create`` cannot race in between.
        """
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT slug FROM projects WHERE slug = ? OR slug LIKE ?",
                (base_slug, f"{base_slug}-%"),
            ).fetchall()
        }
        if base_slug not in existing:
            return base_slug
        suffix = 2
        while f"{base_slug}-{suffix}" in existing:
            suffix += 1
        return f"{base_slug}-{suffix}"

    async def update_name(self, project_id: str, name: str) -> bool:
        """Rename a project. Slug stays — slug renames cascade through
        memory namespaces and conversation tags so they're not part of
        the simple-rename surface.
        """
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("project name is required")
        async with self._lock:
            return await asyncio.to_thread(self._update_name_sync, project_id, cleaned)

    def _update_name_sync(self, project_id: str, name: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE projects SET name = ? WHERE project_id = ?",
                (name, project_id),
            )
            return cur.rowcount > 0

    async def set_status(self, project_id: str, status: str) -> bool:
        """Flip the project's lifecycle state.

        ``LeadLifecycle`` keeps the lead row's status aligned —
        callers that flip a project to ``paused`` or ``archived``
        through the project surface still need to drive the lead's
        own transition. The store is intentionally narrow: it owns
        the column, not the cross-table choreography.
        """
        if status not in PROJECT_STATUSES:
            raise ValueError(f"invalid project status: {status}")
        async with self._lock:
            return await asyncio.to_thread(self._set_status_sync, project_id, status)

    def _set_status_sync(self, project_id: str, status: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE projects SET status = ? WHERE project_id = ?",
                (status, project_id),
            )
            return cur.rowcount > 0

    async def touch_active_at(self, project_id: str, at_ms: int | None = None) -> bool:
        """Stamp ``last_active_at_ms`` so the switcher's recency sort
        sees the just-touched project at the top.
        """
        when = at_ms if at_ms is not None else int(time.time() * 1000)
        async with self._lock:
            return await asyncio.to_thread(self._touch_active_at_sync, project_id, when)

    def _touch_active_at_sync(self, project_id: str, at_ms: int) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE projects SET last_active_at_ms = ? WHERE project_id = ?",
                (at_ms, project_id),
            )
            return cur.rowcount > 0

    async def set_lead(self, project_id: str, lead_agent_id: str | None) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._set_lead_sync, project_id, lead_agent_id)

    def _set_lead_sync(self, project_id: str, lead_agent_id: str | None) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE projects SET lead_agent_id = ? WHERE project_id = ?",
                (lead_agent_id, project_id),
            )
            return cur.rowcount > 0

    async def set_local_only(self, project_id: str, local_only: bool) -> bool:
        """Flip the project's privacy flag (F3.8 / ADR-0023).

        Returns ``True`` when a row was updated. The conversational
        edit path uses this to honour ``"make this project local-only"``;
        the routing layer then short-circuits worker spawns to local
        providers regardless of stored overrides.
        """
        async with self._lock:
            return await asyncio.to_thread(self._set_local_only_sync, project_id, local_only)

    def _set_local_only_sync(self, project_id: str, local_only: bool) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE projects SET local_only = ? WHERE project_id = ?",
                (1 if local_only else 0, project_id),
            )
            return cur.rowcount > 0

    async def delete(self, project_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, project_id)

    def _delete_sync(self, project_id: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM projects WHERE project_id = ?",
                (project_id,),
            )
            return cur.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Project:
        provider_config = (
            json.loads(row["provider_config_json"])
            if row["provider_config_json"] is not None
            else None
        )
        connector_grants = (
            json.loads(row["connector_grants_json"])
            if row["connector_grants_json"] is not None
            else None
        )
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            slug=row["slug"],
            workspace_path=row["workspace_path"],
            repo_remote=row["repo_remote"],
            lead_agent_id=row["lead_agent_id"],
            memory_namespace=row["memory_namespace"],
            conversation_tag=row["conversation_tag"],
            roadmap=row["roadmap"],
            provider_config=provider_config,
            connector_grants=connector_grants,
            local_only=bool(row["local_only"]),
            status=row["status"],
            created_at_ms=row["created_at_ms"],
            last_active_at_ms=row["last_active_at_ms"],
        )
