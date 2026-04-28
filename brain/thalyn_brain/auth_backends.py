"""Auth-backend store — separates auth from model in the provider abstraction.

Per ADR-0012 (refined for v2), ADR-0020, and ``02-architecture.md`` §7,
an auth backend is a credential source: ``claude_subscription``,
``anthropic_api``, ``openai_compat``, ``ollama``, ``llama_cpp``,
``mlx``. The model dimension is independent.

This module owns the *persistence record* (``AuthBackendRecord``) and
its store. The *runtime protocol* (``AuthBackend``, the trait the
provider composes when it talks to the SDK) lives in
``thalyn_brain.provider.auth``.

``config_json`` holds secrets-adapter pointers (keychain entry names),
not plaintext secrets. Secrets remain Rust-side per ADR-0028.
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

AUTH_BACKEND_KINDS = frozenset(
    {
        "claude_subscription",
        "anthropic_api",
        "openai_compat",
        "ollama",
        "llama_cpp",
        "mlx",
    }
)


def new_auth_backend_id() -> str:
    return f"auth_{uuid.uuid4().hex}"


@dataclass
class AuthBackendRecord:
    auth_backend_id: str
    kind: str
    config: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "authBackendId": self.auth_backend_id,
            "kind": self.kind,
            "config": self.config,
        }


class AuthBackendsStore:
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

    async def insert(self, backend: AuthBackendRecord) -> None:
        if backend.kind not in AUTH_BACKEND_KINDS:
            raise ValueError(f"invalid auth backend kind: {backend.kind}")
        async with self._lock:
            await asyncio.to_thread(self._insert_sync, backend)

    def _insert_sync(self, backend: AuthBackendRecord) -> None:
        with self._open() as conn:
            conn.execute(
                "INSERT INTO auth_backends (auth_backend_id, kind, config_json) VALUES (?, ?, ?)",
                (backend.auth_backend_id, backend.kind, json.dumps(backend.config)),
            )

    async def get(self, auth_backend_id: str) -> AuthBackendRecord | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, auth_backend_id)

    def _get_sync(self, auth_backend_id: str) -> AuthBackendRecord | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM auth_backends WHERE auth_backend_id = ?",
                (auth_backend_id,),
            ).fetchone()
            return self._from_row(row) if row else None

    async def list_all(self, *, kind: str | None = None) -> list[AuthBackendRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync, kind)

    def _list_sync(self, kind: str | None) -> list[AuthBackendRecord]:
        with self._open() as conn:
            if kind is not None:
                rows = conn.execute(
                    "SELECT * FROM auth_backends WHERE kind = ? ORDER BY auth_backend_id",
                    (kind,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM auth_backends ORDER BY auth_backend_id"
                ).fetchall()
            return [self._from_row(row) for row in rows]

    async def delete(self, auth_backend_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, auth_backend_id)

    def _delete_sync(self, auth_backend_id: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM auth_backends WHERE auth_backend_id = ?",
                (auth_backend_id,),
            )
            return cur.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AuthBackendRecord:
        return AuthBackendRecord(
            auth_backend_id=row["auth_backend_id"],
            kind=row["kind"],
            config=json.loads(row["config_json"]),
        )
