"""Per-run storage — one SQLite file per run.

LangGraph's `AsyncSqliteSaver` snapshots the graph state on every
node transition. Per `02-architecture.md` §5 each run gets its own
file under `runs/{run_id}.db` so individual runs are cheap to
archive or delete and large runs don't bloat a shared database.

The data directory is configurable; production uses the per-OS data
directory (`~/Library/Application Support/Thalyn/data` on macOS,
`~/.local/share/thalyn/data` on Linux, `%APPDATA%\\Thalyn\\data` on
Windows). Tests pass a temp path.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def default_data_dir() -> Path:
    """Resolve the default data directory for this OS.

    The override knob is `THALYN_DATA_DIR`; production code paths
    pass an explicit path so we don't have to scatter env-var reads
    across modules.
    """
    override = os.environ.get("THALYN_DATA_DIR")
    if override:
        return Path(override)
    # Capture sys.platform into a local so mypy's per-platform
    # narrowing doesn't elide the other branches when type-checking
    # on a single OS.
    platform: str = sys.platform
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Thalyn" / "data"
    if platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Thalyn" / "data"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "thalyn" / "data"


def run_db_path(run_id: str, *, data_dir: Path | None = None) -> Path:
    """Return the file path for a run's SQLite db, creating parents."""
    base = data_dir or default_data_dir()
    runs_dir = base / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir / f"{run_id}.db"


@asynccontextmanager
async def open_run_checkpointer(
    run_id: str,
    *,
    data_dir: Path | None = None,
) -> AsyncIterator[Any]:
    """Open a per-run AsyncSqliteSaver and yield it.

    The context manager closes the underlying connection on exit, so
    repeated runs don't leak file handles. The saver runs `.setup()`
    automatically on first use; we don't need to call it explicitly.
    """
    path = run_db_path(run_id, data_dir=data_dir)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield saver
