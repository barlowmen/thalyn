"""Bridge between the runner's spawn path and the routing layer.

Loads per-project overrides + the project's ``local_only`` flag and
calls the pure ``route_worker`` function. Runner tests can replace it
with a stub that returns a canned ``RouteDecision`` so spawn-path
behaviour is exercised without spinning up SQLite.

The runner consults a ``WorkerRouter`` per spawn (per ADR-0023):

1. Resolve a ``RouteDecision`` for ``(task_tag, project_id)``.
2. Fetch the chosen provider from the registry.
3. Assert the ``local_only`` invariant against the provider's capability
   profile — a cloud provider sneaking through (e.g., a stale override
   left over from before the flag flipped) raises ``LocalOnlyViolation``
   instead of silently leaking project data to a cloud token.

The capability check is the *belt-and-braces* the spec calls for: the
``route_worker`` function already won't pick a cloud provider for a
``local_only`` project, but a defence-in-depth assertion at the spawn
site catches any code path that bypassed routing.
"""

from __future__ import annotations

from typing import Protocol

from thalyn_brain.projects import ProjectsStore
from thalyn_brain.provider import LlmProvider, ProviderRegistry
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.routing_table import (
    DEFAULT_GLOBAL_DEFAULTS,
    LocalOnlyViolation,
    RouteDecision,
    route_worker,
)


class WorkerRouter(Protocol):
    """Resolve a route + invariant-check it for a project's worker spawn."""

    async def route(
        self,
        *,
        task_tag: str | None,
        project_id: str | None,
    ) -> RouteDecision: ...


class StoreBackedWorkerRouter:
    """Default ``WorkerRouter`` backed by ``RoutingOverridesStore`` + ``ProjectsStore``.

    The router is stateless across calls; it re-loads overrides per
    spawn so a route change made mid-run takes effect on the next
    worker spawned. (Re-loading is cheap — overrides are a small
    table; v0.20's WAL-mode read avoids contending with concurrent
    writers.)
    """

    def __init__(
        self,
        *,
        overrides_store: RoutingOverridesStore,
        projects_store: ProjectsStore,
        registry: ProviderRegistry,
    ) -> None:
        self._overrides_store = overrides_store
        self._projects_store = projects_store
        self._registry = registry

    async def route(
        self,
        *,
        task_tag: str | None,
        project_id: str | None,
    ) -> RouteDecision:
        if project_id is None:
            # No project context — apply the global defaults straight,
            # no overrides + no privacy flag. The IPC surface enforces
            # project ownership; the runner's spawn path may still fire
            # before a project is wired (legacy chat.send callers, the
            # default brain run before a project lead exists).
            decision = route_worker(
                task_tag=task_tag,
                project_overrides=None,
                project_local_only=False,
                global_defaults=DEFAULT_GLOBAL_DEFAULTS,
            )
            self._assert_provider_known(decision)
            return decision

        overrides_rows = await self._overrides_store.list_for_project(project_id)
        overrides = {row.task_tag: row.provider_id for row in overrides_rows}
        project = await self._projects_store.get(project_id)
        local_only = bool(project.local_only) if project is not None else False

        decision = route_worker(
            task_tag=task_tag,
            project_overrides=overrides,
            project_local_only=local_only,
            global_defaults=DEFAULT_GLOBAL_DEFAULTS,
        )

        provider = self._assert_provider_known(decision)

        if local_only and not provider.capability_profile.local:
            # Belt-and-braces (F3.8 / ADR-0023): the route function
            # already filters non-local providers out for ``local_only``
            # projects, so this branch should be unreachable. If it
            # fires, a code path bypassed the routing layer or a
            # provider's capability profile lies — the project's
            # privacy invariant is the load-bearing thing, so refuse
            # rather than proceed.
            raise LocalOnlyViolation(
                f"provider {decision.provider_id!r} is not local; "
                f"project {project_id!r} is local_only"
            )
        return decision

    def _assert_provider_known(self, decision: RouteDecision) -> LlmProvider:
        """Resolve the registry entry for the routed provider id.

        Raised only when a routing override or global default points
        at a provider that isn't installed — a configuration bug, not
        a runtime case. Surfaces with the same shape as
        ``ProviderNotImplementedError`` so the chat.send error path
        handles it uniformly.
        """
        return self._registry.get(decision.provider_id)


__all__ = [
    "LocalOnlyViolation",
    "StoreBackedWorkerRouter",
    "WorkerRouter",
]
