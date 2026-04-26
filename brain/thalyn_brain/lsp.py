"""LSP integration scaffolding.

Spawns an external Language Server Protocol process (typescript-
language-server, pyright-langserver, …) and pipes Content-Length-
framed JSON-RPC messages between it and the renderer through the
existing brain → tauri → frontend transport.

The renderer drives every LSP message. The brain doesn't synthesise
diagnostics or completions — it carries bytes between Monaco and the
language server, plus a pinch of session bookkeeping. That keeps the
language-specific behaviour where Monaco already expects it to live
and avoids re-implementing LSP semantics on three sides at once.

Per-language defaults live in :data:`DEFAULT_LSP_COMMANDS`. They're
a best-effort starting point — if the binary is not on ``PATH`` the
session start raises ``LspNotAvailableError`` with the failing
command. A future surface can let projects override the command list
through their workspace config.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

LspNotify = Callable[[str, Any], Awaitable[None]]
"""Callback the manager uses to push server-initiated messages back
to the renderer. Wired by :func:`register_lsp_methods` to the
dispatcher's notify channel."""


class LspError(RuntimeError):
    """Generic failure inside the LSP transport."""


class LspNotAvailableError(LspError):
    """The configured language-server binary is not installed."""


# A starting set of language → command-line mappings. Each entry is
# the argv to spawn; the binary must already be on ``PATH``. The
# values are intentionally close to the upstream defaults so common
# installs (npm i -g typescript-language-server, pip install pyright)
# work without any further config.
DEFAULT_LSP_COMMANDS: dict[str, list[str]] = {
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "python": ["pyright-langserver", "--stdio"],
}


@dataclass
class LspSession:
    """Bookkeeping for one running LSP subprocess."""

    session_id: str
    language: str
    command: list[str]
    process: asyncio.subprocess.Process
    reader_task: asyncio.Task[None]
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class LspManager:
    """Spawn, address, and tear down LSP subprocesses on demand."""

    def __init__(
        self,
        commands: dict[str, list[str]] | None = None,
        notify: LspNotify | None = None,
    ) -> None:
        self._commands = dict(commands) if commands is not None else dict(DEFAULT_LSP_COMMANDS)
        self._notify: LspNotify | None = notify
        self._sessions: dict[str, LspSession] = {}
        self._lock = asyncio.Lock()

    def configure_notify(self, notify: LspNotify) -> None:
        """Late-bind the notification sink. Useful when the manager
        is built before the dispatcher is wired."""

        self._notify = notify

    async def start(self, language: str) -> LspSession:
        """Spawn an LSP for ``language`` and start the reader loop.

        Raises :class:`LspNotAvailableError` if the configured binary
        is not on ``PATH``."""

        command = self._commands.get(language)
        if not command:
            raise LspError(f"no LSP command configured for language: {language}")
        binary = command[0]
        if shutil.which(binary) is None:
            raise LspNotAvailableError(
                f"language server {binary!r} is not installed or not on PATH"
            )

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.stdin is None or process.stdout is None:
            await process.wait()
            raise LspError("failed to open subprocess pipes for LSP")

        session_id = f"lsp_{uuid.uuid4().hex[:12]}"
        session = LspSession(
            session_id=session_id,
            language=language,
            command=list(command),
            process=process,
            reader_task=asyncio.create_task(self._reader_loop(session_id, process.stdout)),
        )
        async with self._lock:
            self._sessions[session_id] = session
        return session

    async def send(self, session_id: str, message: dict[str, Any]) -> None:
        """Forward one LSP-shaped message (request, response, or
        notification) to the language server. The reader loop carries
        replies back via the notify channel — this method does not
        block on a response."""

        session = self._sessions.get(session_id)
        if session is None:
            raise LspError(f"unknown LSP session: {session_id}")
        stdin = session.process.stdin
        if stdin is None or stdin.is_closing():
            raise LspError(f"session {session_id} stdin is closed")
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        stdin.write(header + body)
        await stdin.drain()

    async def stop(self, session_id: str) -> bool:
        """Terminate the LSP subprocess and clean up bookkeeping."""

        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.reader_task.cancel()
        if session.process.returncode is None:
            session.process.terminate()
            try:
                await asyncio.wait_for(session.process.wait(), timeout=2.0)
            except TimeoutError:
                session.process.kill()
                await session.process.wait()
        return True

    async def shutdown(self) -> None:
        """Stop every session — used on brain teardown."""

        for session_id in list(self._sessions.keys()):
            await self.stop(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "sessionId": session.session_id,
                "language": session.language,
                "command": session.command,
                "startedAtMs": session.started_at_ms,
            }
            for session in self._sessions.values()
        ]

    async def _reader_loop(
        self,
        session_id: str,
        stream: asyncio.StreamReader,
    ) -> None:
        """Read Content-Length-framed messages off the LSP's stdout
        and forward each one to the renderer via the notify channel."""

        try:
            while True:
                message = await _read_lsp_message(stream)
                if message is None:
                    break
                if self._notify is None:
                    continue
                await self._notify(
                    "lsp.message",
                    {"sessionId": session_id, "message": message},
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover - defensive
            if self._notify is not None:
                await self._notify(
                    "lsp.error",
                    {"sessionId": session_id, "error": str(exc)},
                )


async def _read_lsp_message(
    stream: asyncio.StreamReader,
) -> dict[str, Any] | None:
    """Pull one ``Content-Length: N\\r\\n\\r\\n<body>`` frame off
    ``stream``. Returns ``None`` on EOF."""

    headers: dict[str, str] = {}
    while True:
        line = await stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        try:
            decoded = line.decode("ascii", errors="replace").strip()
        except UnicodeDecodeError:
            continue
        if ":" not in decoded:
            continue
        key, _, value = decoded.partition(":")
        headers[key.strip().lower()] = value.strip()

    length_str = headers.get("content-length")
    if length_str is None:
        return None
    try:
        length = int(length_str)
    except ValueError:
        return None
    body = await stream.readexactly(length)
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None
