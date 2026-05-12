"""JSON-RPC bindings for the project lifecycle surface.

Eight methods replace the v0.20 ``NOT_IMPLEMENTED`` stubs:

- ``project.create``  — create a new active project.
- ``project.list``    — enumerate projects (filterable by status).
- ``project.update``  — rename / flip the local-only flag.
- ``project.archive`` — retire a project. Archives the active lead
  too so the project's pointer doesn't dangle.
- ``project.pause`` / ``project.resume`` — flip the lifecycle state.
- ``project.classify`` — ask the wired classifier which active
  project a message belongs to. Used by tests and by future
  affordances that surface "this seems to be about X" prompts.
- ``project.merge`` — plan-first / apply-on-confirmation merge of
  project A into project B (F3.4 / ADR-0024).

The handlers thin-wrap ``ProjectsStore`` plus ``LeadLifecycle`` for
the lead-archival cascade. The state machine itself owns the
invariants; the RPC layer parses params and translates errors into
``INVALID_PARAMS``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import (
    LeadLifecycle,
    LeadLifecycleError,
)
from thalyn_brain.memory import MemoryStore
from thalyn_brain.project_classifier import Classifier, classify_for_routing
from thalyn_brain.project_merge import (
    ProjectMergeError,
    apply_merge_plan,
    compute_merge_plan,
)
from thalyn_brain.projects import Project, ProjectsStore
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)
from thalyn_brain.threads import ThreadsStore


def register_project_methods(
    dispatcher: Dispatcher,
    *,
    projects: ProjectsStore,
    lead_lifecycle: LeadLifecycle | None = None,
    classifier: Classifier | None = None,
    threads: ThreadsStore | None = None,
    memory: MemoryStore | None = None,
    agents: AgentRecordsStore | None = None,
    routing_overrides: RoutingOverridesStore | None = None,
    data_dir: Path | None = None,
) -> None:
    """Wire the ``project.*`` methods onto ``dispatcher``.

    ``lead_lifecycle`` is optional so narrow tests can register the
    project surface without a full agent registry. When wired,
    ``project.archive`` cascades into the lead's archive transition
    so the project's pointer doesn't dangle at a still-active lead.

    ``classifier`` is optional for the same reason — when omitted
    ``project.classify`` returns the foreground project unchanged
    (the no-classifier sticky-foreground default per F3.5).

    ``threads`` / ``memory`` / ``agents`` / ``routing_overrides`` are
    the stores ``project.merge`` reads to plan and the columns it
    writes during apply. They're optional so the existing v0.20 / v0.31
    tests can keep their narrow setups; when any of them is missing
    ``project.merge`` errors with a clear "not configured" message
    rather than silently degrading. The data dir flows through to the
    audit writer's NDJSON path.
    """

    async def project_create(params: RpcParams) -> JsonValue:
        name = _require_str(params, "name")
        try:
            project = await projects.create(
                name=name,
                workspace_path=_optional_str(params, "workspacePath"),
                repo_remote=_optional_str(params, "repoRemote"),
                local_only=bool(params.get("localOnly")) if "localOnly" in params else False,
                provider_config=_optional_dict(params, "providerConfig"),
                connector_grants=_optional_dict(params, "connectorGrants"),
                roadmap=_optional_str(params, "roadmap") or "",
            )
        except ValueError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return {"project": project.to_wire()}

    async def project_list(params: RpcParams) -> JsonValue:
        status = _optional_str(params, "status")
        if status is not None:
            from thalyn_brain.projects import PROJECT_STATUSES

            if status not in PROJECT_STATUSES:
                raise RpcError(
                    code=INVALID_PARAMS,
                    message=f"invalid project status: {status}",
                )
        rows = await projects.list_all(status=status)
        return {"projects": [row.to_wire() for row in rows]}

    async def project_update(params: RpcParams) -> JsonValue:
        project_id = _require_str(params, "projectId")
        existing = await projects.get(project_id)
        if existing is None:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"project {project_id!r} does not exist",
            )
        name = _optional_str(params, "name")
        if name is not None:
            try:
                await projects.update_name(project_id, name)
            except ValueError as exc:
                raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        if "localOnly" in params and params["localOnly"] is not None:
            await projects.set_local_only(project_id, bool(params["localOnly"]))
        refreshed = await projects.get(project_id)
        assert refreshed is not None  # update_name returned a row
        return {"project": refreshed.to_wire()}

    async def project_pause(params: RpcParams) -> JsonValue:
        return await _set_status(params, "paused")

    async def project_resume(params: RpcParams) -> JsonValue:
        return await _set_status(params, "active")

    async def project_archive(params: RpcParams) -> JsonValue:
        project_id = _require_str(params, "projectId")
        existing = await projects.get(project_id)
        if existing is None:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"project {project_id!r} does not exist",
            )
        # Cascade: an active or paused lead retires alongside the
        # project. The lifecycle module clears ``projects.lead_agent_id``
        # in ``archive``, so the project's pointer doesn't dangle.
        if lead_lifecycle is not None and existing.lead_agent_id is not None:
            try:
                await lead_lifecycle.archive(existing.lead_agent_id)
            except LeadLifecycleError:
                # Already archived — proceed with the project flip.
                # A genuine error (mid-transition crash) surfaces on the
                # next list call when the rows look inconsistent; we
                # don't block the user here on it.
                pass
        await projects.set_status(project_id, "archived")
        refreshed = await projects.get(project_id)
        assert refreshed is not None
        return {"project": refreshed.to_wire()}

    async def _set_status(params: RpcParams, status: str) -> JsonValue:
        project_id = _require_str(params, "projectId")
        existing = await projects.get(project_id)
        if existing is None:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"project {project_id!r} does not exist",
            )
        try:
            await projects.set_status(project_id, status)
        except ValueError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        refreshed = await projects.get(project_id)
        assert refreshed is not None
        return {"project": refreshed.to_wire()}

    async def project_classify(params: RpcParams) -> JsonValue:
        message = _require_str(params, "message")
        foreground_project_id = _optional_str(params, "foregroundProjectId")
        active = await projects.list_all(status="active")
        # The full verdict (when the classifier is wired) is useful
        # for "this seems to be about X" surfaces. The resolved
        # routing target collapses the threshold check into a single
        # ``projectId`` the renderer can act on without re-running
        # the policy in the frontend.
        if classifier is None:
            verdict_wire: dict[str, Any] | None = None
        else:
            verdict = await classifier.classify(
                message,
                active,
                foreground_project_id=foreground_project_id,
            )
            verdict_wire = verdict.to_wire()
        resolved = await classify_for_routing(
            classifier,
            message,
            active,
            foreground_project_id=foreground_project_id,
        )
        return {"projectId": resolved, "verdict": verdict_wire}

    async def project_merge(params: RpcParams) -> JsonValue:
        if threads is None or memory is None or agents is None or routing_overrides is None:
            raise RpcError(
                code=INVALID_PARAMS,
                message="project.merge is not configured on this dispatcher",
            )
        from_project_id = _require_str(params, "fromProjectId")
        into_project_id = _require_str(params, "intoProjectId")
        apply_flag = bool(params.get("apply", False))
        try:
            plan = await compute_merge_plan(
                from_project_id=from_project_id,
                into_project_id=into_project_id,
                projects=projects,
                threads=threads,
                memory=memory,
                agents=agents,
                routing_overrides=routing_overrides,
            )
        except ProjectMergeError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        if not apply_flag:
            # Dry-run path: the renderer reads the plan, asks the user
            # to confirm, then calls again with ``apply: true``. The
            # ``apply`` flag missing from the payload is treated as
            # False so the renderer can't accidentally apply by
            # forgetting to set it.
            return {"plan": plan.to_wire(), "outcome": None}
        try:
            outcome = await apply_merge_plan(plan, data_dir=data_dir)
        except ProjectMergeError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return {"plan": plan.to_wire(), "outcome": outcome.to_wire()}

    dispatcher.register("project.create", project_create)
    dispatcher.register("project.list", project_list)
    dispatcher.register("project.update", project_update)
    dispatcher.register("project.pause", project_pause)
    dispatcher.register("project.resume", project_resume)
    dispatcher.register("project.archive", project_archive)
    dispatcher.register("project.classify", project_classify)
    dispatcher.register("project.merge", project_merge)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value


def _optional_str(params: RpcParams, key: str) -> str | None:
    if key not in params or params[key] is None:
        return None
    value = params[key]
    if not isinstance(value, str):
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"'{key}' must be a string when present",
        )
    return value or None


def _optional_dict(params: RpcParams, key: str) -> dict[str, Any] | None:
    if key not in params or params[key] is None:
        return None
    value = params[key]
    if not isinstance(value, dict):
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"'{key}' must be an object when present",
        )
    return value


__all__ = ["Project", "register_project_methods"]
