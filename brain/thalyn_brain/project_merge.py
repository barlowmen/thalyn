"""Project merge — plan + apply, with audit trail.

Project mobility per F3.4: the user can merge two projects (`move A
into B` = `merge A into B + archive A`). The implementation is two
phases so the renderer can show the user the consequences before they
land:

- ``compute_merge_plan`` is a pure read against the stores. It builds
  a ``MergePlan`` capturing every row that would be rewritten — turn
  ids, memory entry ids, the absorbed lead, sub-lead re-parent list,
  routing-override migrations and conflicts, the merged
  connector-grant payload and its conflicts. The plan is the contract
  with the renderer: serialise it, show the user, only apply on
  confirmation.
- ``apply_merge_plan`` lives in a sibling module and runs the plan in
  one ``BEGIN IMMEDIATE`` transaction. It lands once the renderer
  resolves the confirm dialog with ``apply: true``.

The merge is keyed by ``from_project_id`` (the absorbed project) and
``into_project_id`` (the surviving project). The naming mirrors the
F3.4 semantics: `merge A into B` archives A and grows B; B wins on
every conflict because the user's stated intent is "keep B."
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecord, AgentRecordsStore
from thalyn_brain.lead_lifecycle import LEAD_KIND
from thalyn_brain.memory import MemoryEntry, MemoryStore
from thalyn_brain.orchestration.storage import default_data_dir
from thalyn_brain.projects import Project, ProjectsStore
from thalyn_brain.routing import RoutingOverride, RoutingOverridesStore
from thalyn_brain.threads import ThreadsStore, ThreadTurn


def new_merge_id() -> str:
    return f"merge_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class RoutingOverrideMigration:
    """One routing override that will move from absorbed → surviving.

    Captured in the plan so the apply step has the row id to update
    (rather than recomputing the lookup). Migrations land cleanly when
    the surviving project has no override for ``task_tag``; conflicts
    surface as ``RoutingOverrideConflict`` instead.
    """

    task_tag: str
    routing_override_id: str
    provider_id: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "taskTag": self.task_tag,
            "routingOverrideId": self.routing_override_id,
            "providerId": self.provider_id,
        }


@dataclass(frozen=True)
class RoutingOverrideConflict:
    """Both projects override the same ``task_tag`` — surviving wins.

    The absorbed override is dropped in the apply step; the conflict is
    recorded in the audit entry so the user can see what was lost. The
    re-grant is a one-line conversational fix if the user wanted the
    other route.
    """

    task_tag: str
    surviving_provider_id: str
    absorbed_provider_id: str
    absorbed_routing_override_id: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "taskTag": self.task_tag,
            "survivingProviderId": self.surviving_provider_id,
            "absorbedProviderId": self.absorbed_provider_id,
            "absorbedRoutingOverrideId": self.absorbed_routing_override_id,
        }


@dataclass(frozen=True)
class ConnectorGrantConflict:
    """Both projects grant the same connector key but with different
    values. Surviving wins in v1; the absorbed value lands in the audit
    entry so the conflict is discoverable.

    Connector-grant shape is intentionally untyped at this layer — the
    blob is opaque to the merge planner. The conflict captures the
    JSON-roundtripped values so audit consumers can rehydrate them.
    """

    key: str
    surviving_value: Any
    absorbed_value: Any

    def to_wire(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "survivingValue": self.surviving_value,
            "absorbedValue": self.absorbed_value,
        }


@dataclass(frozen=True)
class MergePlan:
    """Everything the apply step needs to know — pure data, no IO.

    The plan is the contract with the renderer: it's
    JSON-serialisable, captures counts the user can scan in a confirm
    dialog, and lets the apply step run in one transaction without
    re-querying. ``re_parent_sub_lead_ids`` is empty in v1 (sub-leads
    don't ship until v0.36), but the field exists so the v0.36 phase
    extends rather than rewrites.
    """

    merge_id: str
    from_project: Project
    into_project: Project
    thread_turn_ids: tuple[str, ...]
    memory_entry_ids: tuple[str, ...]
    absorbed_lead: AgentRecord | None
    surviving_lead: AgentRecord | None
    re_parent_sub_lead_ids: tuple[str, ...]
    routing_overrides_to_migrate: tuple[RoutingOverrideMigration, ...]
    routing_override_conflicts: tuple[RoutingOverrideConflict, ...]
    merged_connector_grants: dict[str, Any] | None
    connector_grant_conflicts: tuple[ConnectorGrantConflict, ...]
    computed_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "mergeId": self.merge_id,
            "fromProject": self.from_project.to_wire(),
            "intoProject": self.into_project.to_wire(),
            "threadTurnIds": list(self.thread_turn_ids),
            "memoryEntryIds": list(self.memory_entry_ids),
            "absorbedLead": self.absorbed_lead.to_wire() if self.absorbed_lead else None,
            "survivingLead": self.surviving_lead.to_wire() if self.surviving_lead else None,
            "reParentSubLeadIds": list(self.re_parent_sub_lead_ids),
            "routingOverridesToMigrate": [
                row.to_wire() for row in self.routing_overrides_to_migrate
            ],
            "routingOverrideConflicts": [row.to_wire() for row in self.routing_override_conflicts],
            "mergedConnectorGrants": self.merged_connector_grants,
            "connectorGrantConflicts": [row.to_wire() for row in self.connector_grant_conflicts],
            "computedAtMs": self.computed_at_ms,
            # Counts duplicate the list lengths so a renderer can show
            # the consequence sheet without parsing every list.
            "counts": {
                "threadTurns": len(self.thread_turn_ids),
                "memoryEntries": len(self.memory_entry_ids),
                "subLeadReParents": len(self.re_parent_sub_lead_ids),
                "routingMigrations": len(self.routing_overrides_to_migrate),
                "routingConflicts": len(self.routing_override_conflicts),
                "connectorConflicts": len(self.connector_grant_conflicts),
            },
        }


class ProjectMergeError(Exception):
    """Raised when a merge can't be planned (missing project, same
    project on both sides, archived project on the surviving side).

    Caught at the RPC layer and surfaced as ``INVALID_PARAMS`` so the
    renderer can show the user a useful message rather than a generic
    error.
    """


async def compute_merge_plan(
    *,
    from_project_id: str,
    into_project_id: str,
    projects: ProjectsStore,
    threads: ThreadsStore,
    memory: MemoryStore,
    agents: AgentRecordsStore,
    routing_overrides: RoutingOverridesStore,
    thread_id: str | None = None,
) -> MergePlan:
    """Read the stores and produce a ``MergePlan``.

    No mutation — every store call is a read. The plan is what the
    renderer sees and what ``apply_merge_plan`` consumes. ``thread_id``
    is optional: when wired the planner narrows the turn rewrite to
    one thread; v1 has one thread per user so the narrowing is a
    no-op but the parameter is kept so future multi-thread expansion
    doesn't require a plan-shape change.
    """

    if from_project_id == into_project_id:
        raise ProjectMergeError(
            "cannot merge a project into itself",
        )
    from_project = await projects.get(from_project_id)
    if from_project is None:
        raise ProjectMergeError(f"project {from_project_id!r} does not exist")
    into_project = await projects.get(into_project_id)
    if into_project is None:
        raise ProjectMergeError(f"project {into_project_id!r} does not exist")
    if into_project.status == "archived":
        # Merging *into* an archived project would revive it via the
        # turn rewrites, which is confusing. Refuse and tell the user
        # to resume the project first.
        raise ProjectMergeError(
            f"project {into_project_id!r} is archived; resume it before merging into it",
        )

    # Sub-lead re-parent list is empty in v1; the v0.36 phase fills it
    # by walking agent_records.parent_agent_id from the absorbed lead.
    absorbed_lead = await _find_lead(agents, from_project_id)
    surviving_lead = await _find_lead(agents, into_project_id)
    re_parent_sub_lead_ids = await _list_sub_lead_ids(agents, absorbed_lead)

    # Thread turns: every completed-or-in-progress turn currently tagged
    # to the absorbed project. The query reads against thread_turns
    # directly (rather than ThreadsStore.list_recent's window) so the
    # planner sees the full history regardless of how long the absorbed
    # project has been around.
    thread_turn_ids = tuple(
        turn.turn_id for turn in await _list_turns_for_project(threads, from_project_id, thread_id)
    )

    # Memory entries: every row tagged to the absorbed project,
    # regardless of scope. The absorbed lead's authored rows (where
    # agent_id references the absorbed lead) re-anchor onto the
    # surviving lead in the apply step; the planner just enumerates ids.
    memory_entry_ids = tuple(
        entry.memory_id for entry in await _list_memory_entries_for_project(memory, from_project_id)
    )

    routing_overrides_to_migrate, routing_override_conflicts = await _plan_routing_overrides(
        routing_overrides,
        from_project_id=from_project_id,
        into_project_id=into_project_id,
    )

    merged_connector_grants, connector_grant_conflicts = _merge_connector_grants(
        absorbed=from_project.connector_grants,
        surviving=into_project.connector_grants,
    )

    return MergePlan(
        merge_id=new_merge_id(),
        from_project=from_project,
        into_project=into_project,
        thread_turn_ids=thread_turn_ids,
        memory_entry_ids=memory_entry_ids,
        absorbed_lead=absorbed_lead,
        surviving_lead=surviving_lead,
        re_parent_sub_lead_ids=re_parent_sub_lead_ids,
        routing_overrides_to_migrate=routing_overrides_to_migrate,
        routing_override_conflicts=routing_override_conflicts,
        merged_connector_grants=merged_connector_grants,
        connector_grant_conflicts=connector_grant_conflicts,
        computed_at_ms=int(time.time() * 1000),
    )


async def _find_lead(
    agents: AgentRecordsStore,
    project_id: str,
) -> AgentRecord | None:
    """The lead is the active/paused top-level ``lead`` for the project.

    Archived leads aren't candidates — the merge plan retires the
    absorbed lead, and the surviving lead is the one that's actually
    on duty. Mirrors LeadLifecycle's own filter.
    """
    candidates = await agents.list_all(kind=LEAD_KIND, project_id=project_id)
    for record in candidates:
        if record.parent_agent_id is None and record.status in {"active", "paused"}:
            return record
    return None


async def _list_sub_lead_ids(
    agents: AgentRecordsStore,
    absorbed_lead: AgentRecord | None,
) -> tuple[str, ...]:
    """Sub-leads parented to the absorbed lead need to re-parent in v0.36.

    In v0.35 the data model permits sub-leads but the spawn surface
    doesn't ship them, so this list is always empty in practice. The
    query still runs so the v0.36 phase only has to wire the apply
    path — the plan shape is already correct.
    """
    if absorbed_lead is None:
        return ()
    candidates = await agents.list_all(project_id=absorbed_lead.project_id)
    return tuple(
        record.agent_id
        for record in candidates
        if record.parent_agent_id == absorbed_lead.agent_id
        and record.status in {"active", "paused"}
    )


async def _list_turns_for_project(
    threads: ThreadsStore,
    project_id: str,
    thread_id: str | None,
) -> list[ThreadTurn]:
    """All turns tagged to the project, regardless of status.

    Reads via ``ThreadsStore``'s public surface to keep the planner
    decoupled from the SQL layout. v0.35 ships a single-thread world,
    so iterating threads and filtering by project_id is fine. The
    cost is one extra scan per merge planning call, which dwarfs
    against the user-confirm latency.
    """
    out: list[ThreadTurn] = []
    threads_to_walk = (
        [await threads.get_thread(thread_id)] if thread_id else await threads.list_threads()
    )
    for thread in threads_to_walk:
        if thread is None:
            continue
        for turn in await threads.list_turns(thread.thread_id):
            if turn.project_id == project_id:
                out.append(turn)
    return out


async def _list_memory_entries_for_project(
    memory: MemoryStore,
    project_id: str,
) -> list[MemoryEntry]:
    """Memory rows tagged to the project, across every scope.

    Uses a generous limit so the listing isn't truncated mid-merge;
    the production cap on per-project memory size (per F6) keeps
    the absolute count well under 10K rows for v1.
    """
    return await memory.list_entries(project_id=project_id, limit=10_000)


async def _plan_routing_overrides(
    routing_overrides: RoutingOverridesStore,
    *,
    from_project_id: str,
    into_project_id: str,
) -> tuple[tuple[RoutingOverrideMigration, ...], tuple[RoutingOverrideConflict, ...]]:
    """Split the absorbed project's overrides into migrate / conflict.

    Each absorbed-project override either lands cleanly on the
    surviving project (no existing override for that ``task_tag``) or
    collides with an existing surviving override. Surviving wins on
    collision — F3.4 picks the user-chosen target as the source of
    truth.
    """
    absorbed_overrides = await routing_overrides.list_for_project(from_project_id)
    surviving_overrides = await routing_overrides.list_for_project(into_project_id)
    surviving_by_tag: dict[str, RoutingOverride] = {
        row.task_tag: row for row in surviving_overrides
    }

    migrations: list[RoutingOverrideMigration] = []
    conflicts: list[RoutingOverrideConflict] = []
    for row in absorbed_overrides:
        existing = surviving_by_tag.get(row.task_tag)
        if existing is None:
            migrations.append(
                RoutingOverrideMigration(
                    task_tag=row.task_tag,
                    routing_override_id=row.routing_override_id,
                    provider_id=row.provider_id,
                )
            )
        else:
            conflicts.append(
                RoutingOverrideConflict(
                    task_tag=row.task_tag,
                    surviving_provider_id=existing.provider_id,
                    absorbed_provider_id=row.provider_id,
                    absorbed_routing_override_id=row.routing_override_id,
                )
            )
    return tuple(migrations), tuple(conflicts)


def _merge_connector_grants(
    *,
    absorbed: Mapping[str, Any] | None,
    surviving: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, tuple[ConnectorGrantConflict, ...]]:
    """Union the two blobs, surviving wins on value conflicts.

    Returns ``None`` when both inputs are absent (so the merged value
    doesn't gain an empty dict where there wasn't one before). When
    either side has values, the merge is the union: keys present on
    one side land verbatim, keys present on both with equal values
    land once, keys present on both with different values surface a
    ``ConnectorGrantConflict`` and the surviving value wins.
    """
    if not absorbed and not surviving:
        return None, ()
    merged: dict[str, Any] = dict(surviving or {})
    conflicts: list[ConnectorGrantConflict] = []
    for key, absorbed_value in (absorbed or {}).items():
        if key not in merged:
            merged[key] = absorbed_value
            continue
        if merged[key] == absorbed_value:
            continue
        conflicts.append(
            ConnectorGrantConflict(
                key=key,
                surviving_value=merged[key],
                absorbed_value=absorbed_value,
            )
        )
    return merged, tuple(conflicts)


@dataclass(frozen=True)
class MergeOutcome:
    """What the apply step actually wrote.

    Counts mirror the plan's expectations; a successful apply has the
    same counts the plan computed (modulo races, which BEGIN IMMEDIATE
    serialises out). The ``log_path`` is the audit file the apply
    wrote — the renderer can surface it or grep it for explainability.
    """

    merge_id: str
    applied_at_ms: int
    thread_turns_rewritten: int
    memory_entries_migrated: int
    absorbed_lead_archived: bool
    sub_leads_reparented: int
    routing_overrides_migrated: int
    routing_overrides_dropped: int
    connector_grants_updated: bool
    log_path: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "mergeId": self.merge_id,
            "appliedAtMs": self.applied_at_ms,
            "threadTurnsRewritten": self.thread_turns_rewritten,
            "memoryEntriesMigrated": self.memory_entries_migrated,
            "absorbedLeadArchived": self.absorbed_lead_archived,
            "subLeadsReparented": self.sub_leads_reparented,
            "routingOverridesMigrated": self.routing_overrides_migrated,
            "routingOverridesDropped": self.routing_overrides_dropped,
            "connectorGrantsUpdated": self.connector_grants_updated,
            "logPath": self.log_path,
        }


class MergeAuditWriter:
    """NDJSON audit trail, one file per merge.

    Lives under ``data_dir/merges/<merge_id>.log`` so it sits next to
    the per-run ``runs/<run_id>.log`` files. Two lines minimum:
    ``plan`` (written before the transaction commits — so a crash
    leaves an incomplete file that self-identifies) and ``outcome``
    (written after commit, the apply-actually-happened record).
    Append-only; no signing in v1, matching ``audit.py``'s conventions.
    """

    def __init__(self, merge_id: str, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        merges_dir = base / "merges"
        merges_dir.mkdir(parents=True, exist_ok=True)
        self.merge_id = merge_id
        self.path = merges_dir / f"{merge_id}.log"

    def write_plan(self, plan_wire: Mapping[str, Any]) -> None:
        self._append("plan", plan_wire)

    def write_outcome(self, outcome_wire: Mapping[str, Any]) -> None:
        self._append("outcome", outcome_wire)

    def _append(self, kind: str, payload: Mapping[str, Any]) -> None:
        line = json.dumps(
            {
                "ts": _now_iso(),
                "mergeId": self.merge_id,
                "kind": kind,
                "payload": payload,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


_apply_lock = asyncio.Lock()


async def apply_merge_plan(
    plan: MergePlan,
    *,
    data_dir: Path | None = None,
) -> MergeOutcome:
    """Run ``plan`` in one ``BEGIN IMMEDIATE`` transaction over ``app.db``.

    Holds a module-level asyncio lock for the duration so the brain
    serialises merges against itself; SQLite's busy-handler covers the
    cross-process case if the supervisor ever spawned a second brain
    (it doesn't today, but the invariant should hold). The audit
    writer drops the plan line *before* the transaction commits so a
    mid-commit crash leaves a recoverable artefact identifying the
    intent; the outcome line lands after commit.
    """
    base = data_dir or default_data_dir()
    audit = MergeAuditWriter(plan.merge_id, data_dir=base)
    audit.write_plan(plan.to_wire())
    if plan.re_parent_sub_lead_ids and plan.surviving_lead is None:
        raise ProjectMergeError(
            f"cannot re-parent sub-leads {list(plan.re_parent_sub_lead_ids)!r}: "
            "surviving project has no active lead",
        )
    async with _apply_lock:
        outcome = await asyncio.to_thread(_apply_sync, plan, base, audit.path)
    audit.write_outcome(outcome.to_wire())
    return outcome


def _apply_sync(
    plan: MergePlan,
    data_dir: Path,
    audit_path: Path,
) -> MergeOutcome:
    """Synchronous body of ``apply_merge_plan``.

    Runs on a thread so the surrounding asyncio code isn't blocked on
    the SQLite file lock. One connection, one ``BEGIN IMMEDIATE``,
    every write inside the transaction. The grant-update write
    matches the rest of the codebase by going through the same
    JSON-blob column the projects store reads/writes.
    """
    db_path = data_dir / "app.db"
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            now_ms = int(time.time() * 1000)
            turns_rewritten = _rewrite_thread_turns(
                conn,
                from_project_id=plan.from_project.project_id,
                into_project_id=plan.into_project.project_id,
            )
            memory_migrated = _migrate_memory_entries(
                conn,
                from_project_id=plan.from_project.project_id,
                into_project_id=plan.into_project.project_id,
                absorbed_lead_id=plan.absorbed_lead.agent_id if plan.absorbed_lead else None,
                surviving_lead_id=plan.surviving_lead.agent_id if plan.surviving_lead else None,
            )
            sub_leads_reparented = _reparent_sub_leads(
                conn,
                sub_lead_ids=plan.re_parent_sub_lead_ids,
                into_project_id=plan.into_project.project_id,
                surviving_lead_id=plan.surviving_lead.agent_id if plan.surviving_lead else None,
                now_ms=now_ms,
            )
            routing_migrated = _migrate_routing_overrides(
                conn,
                migrations=plan.routing_overrides_to_migrate,
                into_project_id=plan.into_project.project_id,
                now_ms=now_ms,
            )
            routing_dropped = _drop_conflicting_routing_overrides(
                conn,
                conflicts=plan.routing_override_conflicts,
            )
            connector_updated = _update_surviving_connector_grants(
                conn,
                into_project_id=plan.into_project.project_id,
                merged_grants=plan.merged_connector_grants,
                existing_grants=plan.into_project.connector_grants,
            )
            absorbed_archived = _archive_absorbed_lead(
                conn,
                absorbed_lead_id=plan.absorbed_lead.agent_id if plan.absorbed_lead else None,
                now_ms=now_ms,
            )
            _archive_absorbed_project(
                conn,
                from_project_id=plan.from_project.project_id,
                now_ms=now_ms,
            )
            _touch_surviving_project(
                conn,
                into_project_id=plan.into_project.project_id,
                now_ms=now_ms,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return MergeOutcome(
        merge_id=plan.merge_id,
        applied_at_ms=now_ms,
        thread_turns_rewritten=turns_rewritten,
        memory_entries_migrated=memory_migrated,
        absorbed_lead_archived=absorbed_archived,
        sub_leads_reparented=sub_leads_reparented,
        routing_overrides_migrated=routing_migrated,
        routing_overrides_dropped=routing_dropped,
        connector_grants_updated=connector_updated,
        log_path=str(audit_path),
    )


def _rewrite_thread_turns(
    conn: sqlite3.Connection,
    *,
    from_project_id: str,
    into_project_id: str,
) -> int:
    cur = conn.execute(
        "UPDATE thread_turns SET project_id = ? WHERE project_id = ?",
        (into_project_id, from_project_id),
    )
    return cur.rowcount


def _migrate_memory_entries(
    conn: sqlite3.Connection,
    *,
    from_project_id: str,
    into_project_id: str,
    absorbed_lead_id: str | None,
    surviving_lead_id: str | None,
) -> int:
    """Re-tag memory rows to the surviving project; re-anchor lead-authored
    rows.

    The migration covers two columns in lock-step: ``project_id``
    always rewrites; ``agent_id`` re-anchors only when the row was
    authored by the absorbed lead (otherwise the row's author was a
    worker / brain / sub-lead and stays as-is so provenance survives
    the merge). When the surviving project has no lead, lead-authored
    rows lose their ``agent_id`` rather than dangling — the rows are
    preserved but the authoring lead pointer goes null.
    """
    cur = conn.execute(
        "UPDATE memory_entries SET project_id = ? WHERE project_id = ?",
        (into_project_id, from_project_id),
    )
    rewritten = cur.rowcount
    if absorbed_lead_id is not None:
        conn.execute(
            "UPDATE memory_entries SET agent_id = ? WHERE agent_id = ?",
            (surviving_lead_id, absorbed_lead_id),
        )
    return rewritten


def _reparent_sub_leads(
    conn: sqlite3.Connection,
    *,
    sub_lead_ids: tuple[str, ...],
    into_project_id: str,
    surviving_lead_id: str | None,
    now_ms: int,
) -> int:
    if not sub_lead_ids:
        return 0
    # The applier already refused at the asyncio layer when surviving
    # is None and the list is non-empty; this guard is belt-and-braces.
    if surviving_lead_id is None:
        raise ProjectMergeError(
            "cannot re-parent sub-leads without a surviving lead",
        )
    placeholders = ",".join("?" * len(sub_lead_ids))
    params: list[Any] = [
        surviving_lead_id,
        into_project_id,
        now_ms,
        *sub_lead_ids,
    ]
    cur = conn.execute(
        f"""
        UPDATE agent_records
        SET parent_agent_id = ?, project_id = ?, last_active_at_ms = ?
        WHERE agent_id IN ({placeholders})
        """,
        params,
    )
    return cur.rowcount


def _migrate_routing_overrides(
    conn: sqlite3.Connection,
    *,
    migrations: tuple[RoutingOverrideMigration, ...],
    into_project_id: str,
    now_ms: int,
) -> int:
    migrated = 0
    for migration in migrations:
        cur = conn.execute(
            """
            UPDATE routing_overrides
            SET project_id = ?, updated_at_ms = ?
            WHERE routing_override_id = ?
            """,
            (into_project_id, now_ms, migration.routing_override_id),
        )
        migrated += cur.rowcount
    return migrated


def _drop_conflicting_routing_overrides(
    conn: sqlite3.Connection,
    *,
    conflicts: tuple[RoutingOverrideConflict, ...],
) -> int:
    if not conflicts:
        return 0
    placeholders = ",".join("?" * len(conflicts))
    ids = [c.absorbed_routing_override_id for c in conflicts]
    cur = conn.execute(
        f"DELETE FROM routing_overrides WHERE routing_override_id IN ({placeholders})",
        ids,
    )
    return cur.rowcount


def _update_surviving_connector_grants(
    conn: sqlite3.Connection,
    *,
    into_project_id: str,
    merged_grants: dict[str, Any] | None,
    existing_grants: dict[str, Any] | None,
) -> bool:
    """Write the merged grant blob back to the surviving project.

    Only fires when the merge actually changed something — the union
    being identical to the surviving project's existing blob is a
    no-op (saves the write and keeps the audit-outcome line truthful
    about what changed).
    """
    if merged_grants is None and existing_grants is None:
        return False
    if merged_grants == existing_grants:
        return False
    encoded = json.dumps(merged_grants) if merged_grants is not None else None
    conn.execute(
        "UPDATE projects SET connector_grants_json = ? WHERE project_id = ?",
        (encoded, into_project_id),
    )
    return True


def _archive_absorbed_lead(
    conn: sqlite3.Connection,
    *,
    absorbed_lead_id: str | None,
    now_ms: int,
) -> bool:
    """Retire the absorbed project's lead in lock-step with the merge.

    The lead's project_id pointer blanks so the absorbed project's
    archive (next call) doesn't dangle. Sub-lead re-parenting already
    happened earlier in the transaction, so the lead is safe to
    archive without orphaning its children.
    """
    if absorbed_lead_id is None:
        return False
    cur = conn.execute(
        """
        UPDATE agent_records
        SET status = 'archived', project_id = NULL, last_active_at_ms = ?
        WHERE agent_id = ?
        """,
        (now_ms, absorbed_lead_id),
    )
    return cur.rowcount > 0


def _archive_absorbed_project(
    conn: sqlite3.Connection,
    *,
    from_project_id: str,
    now_ms: int,
) -> None:
    conn.execute(
        """
        UPDATE projects
        SET status = 'archived', lead_agent_id = NULL, last_active_at_ms = ?
        WHERE project_id = ?
        """,
        (now_ms, from_project_id),
    )


def _touch_surviving_project(
    conn: sqlite3.Connection,
    *,
    into_project_id: str,
    now_ms: int,
) -> None:
    """Stamp the surviving project so the switcher's recency sort sees it.

    The merge is the strongest possible "I just used this project"
    signal — every absorbed row's history is now under the surviving
    project's banner. Updating ``last_active_at_ms`` mirrors what
    ``ProjectsStore.touch_active_at`` would do at the end of a turn.
    """
    conn.execute(
        "UPDATE projects SET last_active_at_ms = ? WHERE project_id = ?",
        (now_ms, into_project_id),
    )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="microseconds")


__all__ = [
    "ConnectorGrantConflict",
    "MergeAuditWriter",
    "MergeOutcome",
    "MergePlan",
    "ProjectMergeError",
    "RoutingOverrideConflict",
    "RoutingOverrideMigration",
    "apply_merge_plan",
    "compute_merge_plan",
    "new_merge_id",
]
