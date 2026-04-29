"""Lead lifecycle — the brain-side state machine for project leads.

A *lead* is a persistent ``agent_records`` row of ``kind='lead'`` that
owns one project's deep context (per ``01-requirements.md`` §F2.2 and
ADR-0021). Each lead is one-to-one with its project: ``projects.lead_agent_id``
points at it, and the lead's ``project_id`` points back. The status
column carries the lifecycle state — ``active``, ``paused``, or
``archived`` — and transitions flow through this module so the
invariants stay in one place.

Transitions allowed by the state machine:

- ``spawn``  → creates a new ``active`` lead for a project. Refused if
  the project already has an ``active`` or ``paused`` lead (idempotency
  is the caller's concern; we don't silently return the existing row
  because that hides a likely bug). An ``archived`` lead does not
  block a fresh spawn — the user has explicitly retired the prior
  one.
- ``pause``   ``active`` → ``paused``.
- ``resume``  ``paused`` → ``active``. (``archived`` stays archived
  until a fresh ``spawn`` replaces it.)
- ``archive`` ``active | paused`` → ``archived``. Also clears the
  project's ``lead_agent_id`` so the project's pointer doesn't
  dangle at a retired lead.

Sub-leads are out of scope for v0.23 (the plan defers them to v0.34);
the spawn surface here only creates top-level leads. The data model
already permits sub-leads, so when v0.34 lands the lifecycle module
extends rather than rewrites.
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
LIFECYCLE_KINDS = frozenset({"lead", "sub_lead"})


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


def _provider_from_project_config(
    provider_config: dict[str, object] | None,
) -> str | None:
    if not provider_config:
        return None
    candidate = provider_config.get("providerId")
    return candidate if isinstance(candidate, str) and candidate else None
