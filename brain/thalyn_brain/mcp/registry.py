"""SQLite-backed registry of installed MCP connectors.

Stores the connector id, descriptor snapshot, granted-tool list,
and enabled flag. Secrets never land here — they're held in the OS
keychain through the Rust core and re-injected when the connector
is started, so the brain DB only ever holds non-sensitive config.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)


@dataclass
class ConnectorRecord:
    """One installed connector as held in the registry."""

    connector_id: str
    descriptor: dict[str, Any]
    granted_tools: list[str]
    enabled: bool
    installed_at_ms: int
    updated_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "connectorId": self.connector_id,
            "descriptor": self.descriptor,
            "grantedTools": list(self.granted_tools),
            "enabled": self.enabled,
            "installedAtMs": self.installed_at_ms,
            "updatedAtMs": self.updated_at_ms,
        }


class ConnectorRegistry:
    """Async wrapper around the connector table in ``app.db``."""

    def __init__(self, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        apply_pending_migrations(data_dir=base)
        self._db_path = base / "app.db"
        self._lock = asyncio.Lock()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    async def upsert(
        self,
        *,
        connector_id: str,
        descriptor: dict[str, Any],
        granted_tools: list[str],
        enabled: bool = True,
    ) -> ConnectorRecord:
        async with self._lock:
            return await asyncio.to_thread(
                self._upsert_sync, connector_id, descriptor, granted_tools, enabled
            )

    def _upsert_sync(
        self,
        connector_id: str,
        descriptor: dict[str, Any],
        granted_tools: list[str],
        enabled: bool,
    ) -> ConnectorRecord:
        now = int(time.time() * 1000)
        with self._open() as conn:
            existing = conn.execute(
                "SELECT installed_at_ms FROM mcp_connectors WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
            installed_at = existing["installed_at_ms"] if existing else now
            conn.execute(
                """
                INSERT INTO mcp_connectors (
                    connector_id, descriptor_json, granted_tools_json,
                    enabled, installed_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(connector_id) DO UPDATE SET
                    descriptor_json = excluded.descriptor_json,
                    granted_tools_json = excluded.granted_tools_json,
                    enabled = excluded.enabled,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    connector_id,
                    json.dumps(descriptor, sort_keys=True),
                    json.dumps(granted_tools),
                    1 if enabled else 0,
                    installed_at,
                    now,
                ),
            )
        return ConnectorRecord(
            connector_id=connector_id,
            descriptor=descriptor,
            granted_tools=list(granted_tools),
            enabled=enabled,
            installed_at_ms=installed_at,
            updated_at_ms=now,
        )

    async def set_grants(self, connector_id: str, granted_tools: list[str]) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._set_grants_sync, connector_id, granted_tools)

    def _set_grants_sync(self, connector_id: str, granted_tools: list[str]) -> bool:
        now = int(time.time() * 1000)
        with self._open() as conn:
            cursor = conn.execute(
                "UPDATE mcp_connectors SET granted_tools_json = ?, updated_at_ms = ? "
                "WHERE connector_id = ?",
                (json.dumps(granted_tools), now, connector_id),
            )
        return cursor.rowcount > 0

    async def set_enabled(self, connector_id: str, enabled: bool) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._set_enabled_sync, connector_id, enabled)

    def _set_enabled_sync(self, connector_id: str, enabled: bool) -> bool:
        now = int(time.time() * 1000)
        with self._open() as conn:
            cursor = conn.execute(
                "UPDATE mcp_connectors SET enabled = ?, updated_at_ms = ? WHERE connector_id = ?",
                (1 if enabled else 0, now, connector_id),
            )
        return cursor.rowcount > 0

    async def get(self, connector_id: str) -> ConnectorRecord | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, connector_id)

    def _get_sync(self, connector_id: str) -> ConnectorRecord | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_connectors WHERE connector_id = ?",
                (connector_id,),
            ).fetchone()
        return _row_to_record(row) if row else None

    async def list_all(self) -> list[ConnectorRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> list[ConnectorRecord]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM mcp_connectors ORDER BY installed_at_ms ASC"
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    async def delete(self, connector_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, connector_id)

    def _delete_sync(self, connector_id: str) -> bool:
        with self._open() as conn:
            cursor = conn.execute(
                "DELETE FROM mcp_connectors WHERE connector_id = ?",
                (connector_id,),
            )
        return cursor.rowcount > 0


def _row_to_record(row: sqlite3.Row) -> ConnectorRecord:
    return ConnectorRecord(
        connector_id=row["connector_id"],
        descriptor=json.loads(row["descriptor_json"]),
        granted_tools=json.loads(row["granted_tools_json"]),
        enabled=bool(row["enabled"]),
        installed_at_ms=int(row["installed_at_ms"]),
        updated_at_ms=int(row["updated_at_ms"]),
    )


__all__ = ["ConnectorRecord", "ConnectorRegistry"]
