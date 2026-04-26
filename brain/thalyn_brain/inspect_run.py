"""Ad-hoc CLI for poking at a run's persisted state.

Usage::

    uv run python -m thalyn_brain.inspect_run <run_id>
    uv run python -m thalyn_brain.inspect_run <run_id> --json
    uv run python -m thalyn_brain.inspect_run --list

The two on-disk surfaces a run leaves behind are the header in
`app.db` and the LangGraph checkpoint history in
`runs/{run_id}.db`. The CLI reconciles both into a human-readable
report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import default_data_dir, run_db_path
from thalyn_brain.runs import RunHeader, RunsStore


async def _list(data_dir: Path, *, as_json: bool) -> int:
    store = RunsStore(data_dir=data_dir)
    headers = await store.list_runs(limit=200)
    if as_json:
        print(json.dumps([h.to_wire() for h in headers], indent=2))
        return 0
    if not headers:
        print("(no runs found)")
        return 0
    for h in headers:
        print(_summary_line(h))
    return 0


async def _show(data_dir: Path, run_id: str, *, as_json: bool) -> int:
    store = RunsStore(data_dir=data_dir)
    header = await store.get(run_id)
    if header is None:
        print(f"run not found: {run_id}", file=sys.stderr)
        return 1

    db_path = run_db_path(run_id, data_dir=data_dir)
    has_checkpoint = db_path.exists()
    checkpoint_state = await _latest_checkpoint(run_id, data_dir) if has_checkpoint else None

    if as_json:
        print(
            json.dumps(
                {
                    "header": header.to_wire(),
                    "checkpointDb": str(db_path) if has_checkpoint else None,
                    "checkpointState": checkpoint_state,
                },
                indent=2,
            )
        )
        return 0

    _print_header(header)
    print()
    if has_checkpoint and checkpoint_state is not None:
        _print_checkpoint(checkpoint_state)
    elif has_checkpoint:
        print("checkpoint db present but empty.")
    else:
        print("(no checkpoint db on disk)")
    return 0


async def _latest_checkpoint(run_id: str, data_dir: Path) -> dict[str, Any] | None:
    """Pull the most recent state snapshot from the run's SqliteSaver."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = run_db_path(run_id, data_dir=data_dir)
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
        config: Any = {"configurable": {"thread_id": run_id}}
        state = await saver.aget(config)
        if state is None:
            return None
        # state is a Checkpoint dict — normalise to wire-friendly fields.
        channel_values = state.get("channel_values", {}) if isinstance(state, dict) else {}
        return {
            "ts": state.get("ts") if isinstance(state, dict) else None,
            "values": _jsonable(channel_values),
        }


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-coercion for whatever the checkpoint hands back."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return repr(value)


def _summary_line(h: RunHeader) -> str:
    duration = ""
    if h.completed_at_ms and h.started_at_ms:
        duration = f"  ({(h.completed_at_ms - h.started_at_ms) / 1000:.1f}s)"
    return f"{h.run_id}  {h.status:<10} {h.title[:60]!s}{duration}"


def _print_header(h: RunHeader) -> None:
    print(f"run    : {h.run_id}")
    print(f"status : {h.status}")
    print(f"title  : {h.title}")
    print(f"provider: {h.provider_id}")
    print(f"started : {h.started_at_ms}")
    if h.completed_at_ms:
        print(f"completed: {h.completed_at_ms}")
    if h.plan:
        print()
        print("plan:")
        print(f"  goal: {h.plan.get('goal', '')}")
        for node in h.plan.get("nodes", []):
            print(f"  - [{node.get('status', '')}] {node.get('description', '')}")
    if h.final_response:
        print()
        print("final response:")
        for line in h.final_response.splitlines() or [h.final_response]:
            print(f"  {line}")


def _print_checkpoint(state: dict[str, Any]) -> None:
    print("latest checkpoint:")
    if state.get("ts"):
        print(f"  ts: {state['ts']}")
    values = state.get("values", {})
    if not values:
        print("  (empty)")
        return
    action_log = values.get("action_log") or []
    print(f"  action log entries: {len(action_log)}")
    if values.get("status"):
        print(f"  status            : {values['status']}")
    if values.get("error"):
        print(f"  error             : {values['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thalyn-inspect-run")
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--list", action="store_true", help="list known runs")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
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

    if args.list:
        return asyncio.run(_list(data_dir, as_json=args.json))
    if not args.run_id:
        parser.print_help(sys.stderr)
        return 2
    return asyncio.run(_show(data_dir, args.run_id, as_json=args.json))


if __name__ == "__main__":
    sys.exit(main())
