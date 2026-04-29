"""Project store — first-class project entity for v2.

Each project owns a lead agent (``lead_agent_id``), a memory namespace,
a conversation tag for the eternal thread, and per-project provider /
connector / privacy configuration. The v0.20 module provides CRUD only;
project lifecycle (create / archive / merge) lands in subsequent stages.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)

PROJECT_STATUSES = frozenset({"active", "paused", "archived"})


def new_project_id() -> str:
    return f"proj_{uuid.uuid4().hex}"


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
