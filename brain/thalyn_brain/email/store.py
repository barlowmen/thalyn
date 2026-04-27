"""Per-account email storage in app.db.

Holds the lightweight account metadata (id, provider, label, email
address, timestamps). Refresh tokens and OAuth client credentials
live in the OS keychain on the Rust side, keyed by
``email:<account_id>:refresh_token`` etc., so this module never
touches secret material.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import default_data_dir

EMAIL_PROVIDERS = frozenset({"gmail", "microsoft"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_accounts (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    address TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS email_accounts_provider_idx ON email_accounts(provider);
"""


@dataclass(frozen=True)
class EmailAccountRow:
    """One row in ``email_accounts``."""

    account_id: str
    provider: str
    label: str
    address: str
    created_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "accountId": self.account_id,
            "provider": self.provider,
            "label": self.label,
            "address": self.address,
            "createdAtMs": self.created_at_ms,
        }


class EmailAccountStore:
    """Async SQLite store for email accounts."""

    def __init__(self, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        base.mkdir(parents=True, exist_ok=True)
        self._db_path = base / "app.db"
        self._lock = asyncio.Lock()
        with self._open() as conn:
            conn.executescript(_SCHEMA)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    async def add(
        self,
        *,
        account_id: str,
        provider: str,
        label: str,
        address: str,
    ) -> EmailAccountRow:
        async with self._lock:
            return await asyncio.to_thread(self._add_sync, account_id, provider, label, address)

    def _add_sync(
        self, account_id: str, provider: str, label: str, address: str
    ) -> EmailAccountRow:
        if provider not in EMAIL_PROVIDERS:
            raise ValueError(f"unknown provider: {provider}")
        now = int(time.time() * 1000)
        with self._open() as conn:
            try:
                conn.execute(
                    "INSERT INTO email_accounts ("
                    "account_id, provider, label, address, created_at_ms"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (account_id, provider, label, address, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"account already exists: {account_id}") from exc
        return EmailAccountRow(
            account_id=account_id,
            provider=provider,
            label=label,
            address=address,
            created_at_ms=now,
        )

    async def get(self, account_id: str) -> EmailAccountRow | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, account_id)

    def _get_sync(self, account_id: str) -> EmailAccountRow | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM email_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return _to_row(row) if row else None

    async def list_all(self) -> list[EmailAccountRow]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> list[EmailAccountRow]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM email_accounts ORDER BY created_at_ms ASC"
            ).fetchall()
        return [_to_row(row) for row in rows]

    async def delete(self, account_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, account_id)

    def _delete_sync(self, account_id: str) -> bool:
        with self._open() as conn:
            cursor = conn.execute("DELETE FROM email_accounts WHERE account_id = ?", (account_id,))
        return cursor.rowcount > 0


def _to_row(row: sqlite3.Row) -> EmailAccountRow:
    return EmailAccountRow(
        account_id=row["account_id"],
        provider=row["provider"],
        label=row["label"],
        address=row["address"],
        created_at_ms=int(row["created_at_ms"]),
    )


__all__ = ["EMAIL_PROVIDERS", "EmailAccountRow", "EmailAccountStore"]
