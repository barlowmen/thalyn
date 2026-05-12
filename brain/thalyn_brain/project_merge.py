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

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from thalyn_brain.agents import AgentRecord, AgentRecordsStore
from thalyn_brain.lead_lifecycle import LEAD_KIND
from thalyn_brain.memory import MemoryEntry, MemoryStore
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


__all__ = [
    "ConnectorGrantConflict",
    "MergePlan",
    "ProjectMergeError",
    "RoutingOverrideConflict",
    "RoutingOverrideMigration",
    "compute_merge_plan",
    "new_merge_id",
]
