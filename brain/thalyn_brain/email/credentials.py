"""In-memory cache for OAuth credentials forwarded by the Rust core.

The Rust core owns the keychain. On startup, and again whenever
the user changes a refresh token or OAuth client value, the core
pushes the per-account triple (refresh token, client id, client
secret) into the brain via the ``email.set_credentials`` JSON-RPC
method. This module is the in-memory store the email manager
reads from when minting access tokens; nothing here is persisted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class _Triple:
    refresh_token: str
    client_id: str
    client_secret: str


class EmailCredentialsCache:
    """Process-local map of account id → OAuth triple."""

    def __init__(self) -> None:
        self._values: dict[str, _Triple] = {}
        self._lock = asyncio.Lock()

    async def set(
        self,
        account_id: str,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        async with self._lock:
            self._values[account_id] = _Triple(
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
            )

    async def clear(self, account_id: str) -> bool:
        async with self._lock:
            return self._values.pop(account_id, None) is not None

    async def status(self, account_id: str) -> dict[str, bool]:
        async with self._lock:
            triple = self._values.get(account_id)
        return {
            "refreshTokenConfigured": bool(triple and triple.refresh_token),
            "clientIdConfigured": bool(triple and triple.client_id),
            "clientSecretConfigured": bool(triple and triple.client_secret),
        }

    async def token_source(self, account_id: str) -> tuple[str, str, str]:
        async with self._lock:
            triple = self._values.get(account_id)
        if triple is None:
            return ("", "", "")
        return (triple.refresh_token, triple.client_id, triple.client_secret)


__all__ = ["EmailCredentialsCache"]
