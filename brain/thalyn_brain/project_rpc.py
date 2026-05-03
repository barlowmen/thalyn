"""JSON-RPC bindings for the project lifecycle surface.

Five methods replace the v0.20 ``NOT_IMPLEMENTED`` stubs (the
``project.classify`` stub is kept until the classifier work lands):

- ``project.create``  — create a new active project.
- ``project.list``    — enumerate projects (filterable by status).
- ``project.update``  — rename / flip the local-only flag.
- ``project.archive`` — retire a project. Archives the active lead
  too so the project's pointer doesn't dangle.
- ``project.pause`` / ``project.resume`` — flip the lifecycle state.

The handlers thin-wrap ``ProjectsStore`` plus ``LeadLifecycle`` for
the lead-archival cascade. The state machine itself owns the
invariants; the RPC layer parses params and translates errors into
``INVALID_PARAMS``.
"""

from __future__ import annotations

from typing import Any

from thalyn_brain.lead_lifecycle import (
    LeadLifecycle,
    LeadLifecycleError,
)
from thalyn_brain.projects import Project, ProjectsStore
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_project_methods(
    dispatcher: Dispatcher,
    *,
    projects: ProjectsStore,
    lead_lifecycle: LeadLifecycle | None = None,
) -> None:
    """Wire the ``project.*`` methods onto ``dispatcher``.

    ``lead_lifecycle`` is optional so narrow tests can register the
    project surface without a full agent registry. When wired,
    ``project.archive`` cascades into the lead's archive transition
    so the project's pointer doesn't dangle at a still-active lead.
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

    dispatcher.register("project.create", project_create)
    dispatcher.register("project.list", project_list)
    dispatcher.register("project.update", project_update)
    dispatcher.register("project.pause", project_pause)
    dispatcher.register("project.resume", project_resume)
    dispatcher.register("project.archive", project_archive)


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
