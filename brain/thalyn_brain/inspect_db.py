"""Tabular dump of any of the three SQLite stores for debugging.

Usage::

    uv run python -m thalyn_brain.inspect_db app
    uv run python -m thalyn_brain.inspect_db app --table agent_runs
    uv run python -m thalyn_brain.inspect_db memory
    uv run python -m thalyn_brain.inspect_db thread
    uv run python -m thalyn_brain.inspect_db runs
    uv run python -m thalyn_brain.inspect_db runs --run-id run_abc

Per ADR-0028 the brain owns every SQLite store, so this CLI is the
authoritative way to peek at on-disk state without spinning up the
full sidecar. The ``memory`` and ``thread`` aliases dump the relevant
tables out of ``app.db`` today; once those stores split into their own
files (in a later stage) the aliases stay stable.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
    run_db_path,
)

# Per-store table groupings. The "alias" keys map to slices of app.db
# until memory.db and thread.db split into their own files.
_STORE_TABLE_GROUPS: dict[str, tuple[str, ...]] = {
    "app": (
        "projects",
        "agent_records",
        "auth_backends",
        "routing_overrides",
        "agent_runs",
        "schedules",
        "approvals",
        "action_log",
        "mcp_connectors",
        "email_accounts",
    ),
    "memory": ("memory_entries",),
    "thread": ("threads", "thread_turns", "session_digests"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thalyn-inspect-db")
    parser.add_argument(
        "store",
        choices=["app", "memory", "thread", "runs"],
        help="which logical store to inspect",
    )
    parser.add_argument(
        "--table",
        help="limit the dump to one table (only meaningful for app/memory/thread)",
    )
    parser.add_argument(
        "--run-id",
        help="for the runs store, dump the per-run checkpoint db (otherwise lists runs)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="max rows per table (default 50)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="override the default data directory",
    )
    args = parser.parse_args(argv)

    data_dir = args.data_dir or default_data_dir()
    if not data_dir.exists():
        print(f"data dir does not exist: {data_dir}", file=sys.stderr)
        return 1

    if args.store == "runs":
        return _dump_runs(data_dir, run_id=args.run_id, limit=args.limit)
    return _dump_app_slice(
        data_dir,
        store=args.store,
        only_table=args.table,
        limit=args.limit,
    )


def _dump_app_slice(
    data_dir: Path,
    *,
    store: str,
    only_table: str | None,
    limit: int,
) -> int:
    """Dump tables that live in ``app.db`` for the given logical store."""
    apply_pending_migrations(data_dir=data_dir)
    db_path = data_dir / "app.db"
    if not db_path.exists():
        print(f"app.db does not exist at {db_path}", file=sys.stderr)
        return 1
    tables = _STORE_TABLE_GROUPS.get(store, ())
    if only_table:
        if only_table not in tables:
            print(
                f"table {only_table!r} not part of store {store!r}; available: {', '.join(tables)}",
                file=sys.stderr,
            )
            return 2
        tables = (only_table,)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for table in tables:
            _print_table(conn, table, limit=limit)
    return 0


def _dump_runs(data_dir: Path, *, run_id: str | None, limit: int) -> int:
    """Dump either the runs index or a specific run's checkpoint db."""
    if run_id is None:
        apply_pending_migrations(data_dir=data_dir)
        db_path = data_dir / "app.db"
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _print_table(conn, "agent_runs", limit=limit)
        return 0
    db_path = run_db_path(run_id, data_dir=data_dir)
    if not db_path.exists():
        print(f"run db does not exist: {db_path}", file=sys.stderr)
        return 1
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for table in _list_user_tables(conn):
            _print_table(conn, table, limit=limit)
    return 0


def _list_user_tables(conn: sqlite3.Connection) -> Iterable[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE '_yoyo_%' "
        "AND name NOT LIKE 'yoyo_%' "
        "AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def _print_table(conn: sqlite3.Connection, table: str, *, limit: int) -> None:
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not info:
        print(f"\n=== {table} ===")
        print("  (table not found)")
        return
    columns = [row["name"] for row in info]
    rows = conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,)).fetchall()
    (count_row,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    print(f"\n=== {table} ({count_row} row{'s' if count_row != 1 else ''}) ===")
    if not rows:
        print("  (no rows)")
        return
    widths = [max(len(c), *(len(_truncate(r[c])) for r in rows)) for c in columns]
    header = "  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))
    sep = "  " + "  ".join("-" * w for w in widths)
    print(header)
    print(sep)
    for r in rows:
        line = "  " + "  ".join(
            _truncate(r[c]).ljust(w) for c, w in zip(columns, widths, strict=True)
        )
        print(line)
    if count_row > limit:
        print(f"  ... ({count_row - limit} more)")


def _truncate(value: object, *, width: int = 40) -> str:
    text = "" if value is None else str(value)
    if len(text) > width:
        return text[: width - 1] + "…"
    return text


if __name__ == "__main__":
    sys.exit(main())
