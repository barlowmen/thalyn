"""Lead lifecycle — the brain-side state machine for project leads.

A *lead* is a persistent ``agent_records`` row of ``kind='lead'`` (or
``'sub_lead'`` for facet-scoped sub-leads) that owns one project's
deep context (per ``01-requirements.md`` §F2.2 / §F2.3 and ADR-0021).
A top-level lead is one-to-one with its project — ``projects.lead_agent_id``
points at it, and the lead's ``project_id`` points back. Sub-leads
share the project's ``project_id`` and reference their parent lead via
``parent_agent_id``; the project's pointer remains the top-level lead.

Transitions allowed by the state machine:

- ``spawn``  → creates a new ``active`` top-level lead for a project.
  Refused if the project already has an ``active`` or ``paused`` lead
  (idempotency is the caller's concern; we don't silently return the
  existing row because that hides a likely bug). An ``archived`` lead
  does not block a fresh spawn — the user has explicitly retired the
  prior one.
- ``spawn_sub_lead`` → creates a new ``active`` ``sub_lead`` parented
  to an active top-level lead. Depth is capped at 2 in v1
  (``01-requirements.md`` §F2.3); deeper spawns require an explicit
  ``override_depth_cap`` flag the caller only sets after the
  ``gateKind: "depth"`` approval resolves.
- ``pause``   ``active`` → ``paused``.
- ``resume``  ``paused`` → ``active``. (``archived`` stays archived
  until a fresh ``spawn`` replaces it.)
- ``archive`` ``active | paused`` → ``archived``. Also clears the
  project's ``lead_agent_id`` when the archived agent was a top-level
  lead so the project's pointer doesn't dangle at a retired lead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from thalyn_brain.agents import (
    AGENT_KINDS,
    AGENT_STATUSES,
    AgentRecord,
    AgentRecordsStore,
    new_agent_id,
)
from thalyn_brain.projects import ProjectsStore

LEAD_KIND = "lead"
SUB_LEAD_KIND = "sub_lead"
LIFECYCLE_KINDS = frozenset({"lead", "sub_lead"})

# v1 caps depth at 2 — top-level lead → sub-lead. Deeper spawns
# require an explicit ``override_depth_cap`` from a caller that's
# already cleared a ``gateKind: "depth"`` approval (per
# ``01-requirements.md`` §F2.3). The constant is exported so the
# action-layer / RPC layer can surface the cap conversationally
# without duplicating the magic number.
DEPTH_CAP = 2


class LeadLifecycleError(Exception):
    """Raised when a lifecycle transition violates an invariant.

    Caught by ``lead_rpc`` and surfaced as ``INVALID_PARAMS`` so the
    renderer can show the user a useful message. The state machine
    raises this instead of a generic ``ValueError`` so callers can
    distinguish lifecycle invariants from input parsing issues.
    """


@dataclass(frozen=True)
class SpawnRequest:
    project_id: str
    display_name: str | None = None
    default_provider_id: str | None = None
    system_prompt: str | None = None


@dataclass(frozen=True)
class SubLeadSpawnRequest:
    """Request to spawn a sub-lead under an existing top-level lead.

    ``parent_agent_id`` is the lead the sub-lead reports to;
    ``scope_facet`` is the slice of the parent's project this sub-lead
    owns (``"ui"``, ``"harness"``, ``"cost-monitoring"``, …) and
    becomes the suffix on the sub-lead's memory namespace so direct-DB
    queries respect isolation by construction. ``override_depth_cap``
    is the explicit-user-override knob: only true when the caller has
    already cleared a ``gateKind: "depth"`` approval, which the
    lifecycle treats as a user intent to deviate from F2.3.
    """

    parent_agent_id: str
    scope_facet: str
    display_name: str | None = None
    default_provider_id: str | None = None
    system_prompt: str | None = None
    override_depth_cap: bool = False


class DepthCapExceededError(LeadLifecycleError):
    """Raised when a sub-lead spawn would exceed the v1 depth cap.

    Distinct subtype so the action layer can surface a
    ``gateKind: "depth"`` approval rather than a generic invariant
    error — the user's response to "your sub-lead wants its own
    sub-lead" is qualitatively different from "you typed an invalid
    project id".
    """

    def __init__(self, parent_agent_id: str, depth: int) -> None:
        super().__init__(
            f"sub-lead under {parent_agent_id!r} would land at depth "
            f"{depth}; v1 caps depth at {DEPTH_CAP} — set "
            "override_depth_cap=True after the depth approval resolves.",
        )
        self.parent_agent_id = parent_agent_id
        self.attempted_depth = depth


class LeadLifecycle:
    """State-machine wrapper around ``AgentRecordsStore`` + ``ProjectsStore``.

    The class deliberately holds no in-memory state of its own; the
    SQLite stores are the source of truth. That keeps multi-process
    behaviour predictable (the supervisor + brain can both observe
    transitions) and makes restart-recovery a non-event — the next
    call sees whatever the stores hold.
    """

    def __init__(
        self,
        *,
        agents: AgentRecordsStore,
        projects: ProjectsStore,
    ) -> None:
        self._agents = agents
        self._projects = projects

    async def spawn(self, request: SpawnRequest) -> AgentRecord:
        """Create a new top-level lead for ``request.project_id``.

        Resolves defaults (display name, memory namespace, provider)
        from the project row when the caller omitted them. Updates
        ``projects.lead_agent_id`` in the same lifecycle transition so
        the bidirectional link is consistent.
        """
        project = await self._projects.get(request.project_id)
        if project is None:
            raise LeadLifecycleError(f"project {request.project_id!r} does not exist")

        existing = await self._active_or_paused_lead(request.project_id)
        if existing is not None:
            raise LeadLifecycleError(
                f"project {request.project_id!r} already has a "
                f"{existing.status} lead {existing.agent_id!r}; "
                "archive it before spawning a new one"
            )

        display_name = request.display_name or _default_display_name(project.name)
        provider_id = (
            request.default_provider_id
            or _provider_from_project_config(project.provider_config)
            or "anthropic"
        )
        memory_namespace = f"lead-{project.slug}"
        now = int(time.time() * 1000)
        record = AgentRecord(
            agent_id=new_agent_id(),
            kind=LEAD_KIND,
            display_name=display_name,
            parent_agent_id=None,
            project_id=request.project_id,
            scope_facet=None,
            memory_namespace=memory_namespace,
            default_provider_id=provider_id,
            system_prompt=request.system_prompt or "",
            status="active",
            created_at_ms=now,
            last_active_at_ms=now,
        )
        await self._agents.insert(record)
        await self._projects.set_lead(request.project_id, record.agent_id)
        return record

    async def spawn_sub_lead(self, request: SubLeadSpawnRequest) -> AgentRecord:
        """Create a sub-lead under ``request.parent_agent_id``.

        The parent lead must exist, be lifecycle-managed (a ``lead``
        or ``sub_lead``) and currently active. Sub-leads inherit
        their parent's project so the data model holds even under
        merge: the parent's ``project_id`` is the sub-lead's
        ``project_id``. The depth-cap check walks the parent chain;
        v1 caps at 2 (top-level lead → sub-lead), and a
        ``DepthCapExceededError`` surfaces unless the caller passes
        ``override_depth_cap=True`` after the depth approval resolves.

        ``scope_facet`` is required and slugified into the memory
        namespace so direct-DB queries against ``memory_entries``
        respect isolation by construction (see
        ``thalyn_brain.memory`` for the read/write helpers that
        consult it).
        """
        scope_facet = (request.scope_facet or "").strip()
        if not scope_facet:
            raise LeadLifecycleError("sub-lead spawn requires a non-empty scope_facet")
        parent = await self._agents.get(request.parent_agent_id)
        if parent is None:
            raise LeadLifecycleError(
                f"parent agent {request.parent_agent_id!r} does not exist",
            )
        if parent.kind not in LIFECYCLE_KINDS:
            raise LeadLifecycleError(
                f"parent agent {parent.agent_id!r} is a {parent.kind!r}; "
                "sub-leads can only be parented to leads or sub-leads"
            )
        if parent.status != "active":
            raise LeadLifecycleError(
                f"parent agent {parent.agent_id!r} is {parent.status!r}; "
                "sub-leads can only be spawned under an active parent"
            )
        if parent.project_id is None:
            raise LeadLifecycleError(
                f"parent agent {parent.agent_id!r} has no project; "
                "sub-leads must be parented to a project-bound lead"
            )
        depth = await self._depth_under(parent) + 1
        if depth > DEPTH_CAP and not request.override_depth_cap:
            raise DepthCapExceededError(parent.agent_id, depth)

        project = await self._projects.get(parent.project_id)
        if project is None:
            raise LeadLifecycleError(
                f"parent agent {parent.agent_id!r} references missing "
                f"project {parent.project_id!r}",
            )

        facet_slug = _slugify_facet(scope_facet)
        display_name = request.display_name or _default_sub_lead_display_name(scope_facet)
        provider_id = (
            request.default_provider_id
            or parent.default_provider_id
            or _provider_from_project_config(project.provider_config)
            or "anthropic"
        )
        memory_namespace = f"{parent.memory_namespace}/{facet_slug}"
        now = int(time.time() * 1000)
        record = AgentRecord(
            agent_id=new_agent_id(),
            kind=SUB_LEAD_KIND,
            display_name=display_name,
            parent_agent_id=parent.agent_id,
            project_id=parent.project_id,
            scope_facet=scope_facet,
            memory_namespace=memory_namespace,
            default_provider_id=provider_id,
            system_prompt=request.system_prompt or "",
            status="active",
            created_at_ms=now,
            last_active_at_ms=now,
        )
        await self._agents.insert(record)
        return record

    async def list_leads(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        kind: str | None = None,
    ) -> list[AgentRecord]:
        """Enumerate leads (and sub-leads) for the renderer.

        Default filter is ``kind='lead'`` so the v1 inspector tile
        only shows top-level leads; callers that want sub-leads pass
        ``kind='sub_lead'`` (or ``None`` for the union once v0.34 lands).
        """
        kinds: tuple[str, ...]
        if kind is not None:
            if kind not in AGENT_KINDS:
                raise LeadLifecycleError(f"invalid agent kind: {kind}")
            if kind not in LIFECYCLE_KINDS:
                raise LeadLifecycleError(f"kind {kind!r} is not a lifecycle-managed kind")
            kinds = (kind,)
        else:
            kinds = tuple(LIFECYCLE_KINDS)
        if status is not None and status not in AGENT_STATUSES:
            raise LeadLifecycleError(f"invalid agent status: {status}")

        out: list[AgentRecord] = []
        for k in kinds:
            out.extend(
                await self._agents.list_all(
                    kind=k,
                    project_id=project_id,
                    status=status,
                )
            )
        out.sort(key=lambda r: r.created_at_ms)
        return out

    async def pause(self, agent_id: str) -> AgentRecord:
        return await self._transition(
            agent_id,
            allowed_from={"active"},
            new_status="paused",
        )

    async def resume(self, agent_id: str) -> AgentRecord:
        return await self._transition(
            agent_id,
            allowed_from={"paused"},
            new_status="active",
        )

    async def archive(self, agent_id: str) -> AgentRecord:
        record = await self._transition(
            agent_id,
            allowed_from={"active", "paused"},
            new_status="archived",
        )
        # Clear the project's pointer so it doesn't dangle at a
        # retired lead. A future ``spawn`` for the same project will
        # set a fresh pointer; until then the project has no active
        # lead — matching the data semantics F3.6 calls for.
        if record.project_id is not None and record.kind == LEAD_KIND:
            await self._projects.set_lead(record.project_id, None)
        return record

    async def _transition(
        self,
        agent_id: str,
        *,
        allowed_from: set[str],
        new_status: str,
    ) -> AgentRecord:
        record = await self._agents.get(agent_id)
        if record is None:
            raise LeadLifecycleError(f"agent {agent_id!r} does not exist")
        if record.kind not in LIFECYCLE_KINDS:
            raise LeadLifecycleError(
                f"agent {agent_id!r} is a {record.kind!r}; lifecycle is "
                "only defined for leads and sub-leads"
            )
        if record.status not in allowed_from:
            raise LeadLifecycleError(
                f"agent {agent_id!r} is {record.status!r}; "
                f"cannot transition to {new_status!r} from this state"
            )
        now = int(time.time() * 1000)
        await self._agents.update_status(agent_id, new_status, last_active_at_ms=now)
        # Re-read so the returned record reflects the persisted state
        # rather than a hand-mutated copy. Cheap, and removes the
        # class of bug where the caller acts on optimistic data the
        # transaction never committed.
        refreshed = await self._agents.get(agent_id)
        assert refreshed is not None  # update_status returned a row
        return refreshed

    async def _depth_under(self, parent: AgentRecord) -> int:
        """Walk parent_agent_id upward; return how deep ``parent`` sits.

        A top-level lead's depth is 1 (the lead itself); a sub-lead
        under it sits at depth 2. Walking the chain instead of trusting
        ``parent.kind`` tolerates the edge case where the schema
        permits but the kind hasn't been backfilled, and gives the
        depth-cap check the same semantics regardless of whether the
        caller passed the top-level lead or a deeper sub-lead.
        """
        depth = 1
        cursor: AgentRecord | None = parent
        # The walk is bounded by the actual chain length; v1 caps at 2,
        # but we tolerate a longer chain (e.g. an override that already
        # landed) without infinite-looping. The agent table is small
        # enough that the linear walk is free.
        seen: set[str] = set()
        while cursor is not None and cursor.parent_agent_id is not None:
            if cursor.agent_id in seen:
                # Defensive: a cycle in parent_agent_id would lock the
                # walk forever. Refuse rather than silently truncating
                # — a cycle here is a genuine data-corruption bug.
                raise LeadLifecycleError(
                    f"parent chain for {parent.agent_id!r} contains a cycle",
                )
            seen.add(cursor.agent_id)
            cursor = await self._agents.get(cursor.parent_agent_id)
            depth += 1
        return depth

    async def _active_or_paused_lead(self, project_id: str) -> AgentRecord | None:
        """Find an existing top-level lead for the project that's not
        archived. Used by ``spawn`` to enforce one-active-lead-per-project."""
        candidates = await self._agents.list_all(
            kind=LEAD_KIND,
            project_id=project_id,
        )
        for record in candidates:
            if record.parent_agent_id is None and record.status in {
                "active",
                "paused",
            }:
                return record
        return None


def _default_display_name(project_name: str) -> str:
    """``Lead-<Project>`` per ``01-requirements.md`` §F2.2.

    Project names can carry spaces and punctuation; the lead name is
    the user-facing string the brain addresses in chat, so we keep it
    readable rather than slugifying.
    """
    return f"Lead-{project_name.strip() or 'Project'}"


def _default_sub_lead_display_name(scope_facet: str) -> str:
    """``SubLead-<Facet>`` per ``01-requirements.md`` §F2.3.

    Mirrors ``_default_display_name``: the facet is human-readable,
    no slugification, so chat addressing matches what the user typed.
    """
    cleaned = scope_facet.strip()
    if not cleaned:
        return "SubLead"
    # Title-case multiword facets ("cost monitoring" → "Cost-Monitoring")
    # so the rendered name reads as a proper noun. Single-word facets
    # round-trip case-insensitively against the addressing matcher.
    parts = [part for part in cleaned.replace("_", " ").split() if part]
    formatted = "-".join(part[:1].upper() + part[1:] for part in parts)
    return f"SubLead-{formatted}"


def _slugify_facet(scope_facet: str) -> str:
    """Lower-kebab the facet for the memory-namespace suffix.

    Mirrors ``projects.slugify`` but is local to the lifecycle module
    so the dependency stays one-way (``lead_lifecycle`` already
    imports from ``projects`` for the store, not the slug helper).
    """
    cleaned = scope_facet.strip().lower()
    out_chars: list[str] = []
    last_dash = True
    for ch in cleaned:
        if ch.isalnum():
            out_chars.append(ch)
            last_dash = False
        elif not last_dash:
            out_chars.append("-")
            last_dash = True
    slug = "".join(out_chars).strip("-")
    return slug or "facet"


def _provider_from_project_config(
    provider_config: dict[str, object] | None,
) -> str | None:
    if not provider_config:
        return None
    candidate = provider_config.get("providerId")
    return candidate if isinstance(candidate, str) and candidate else None
