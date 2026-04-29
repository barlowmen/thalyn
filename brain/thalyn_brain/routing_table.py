"""Pure routing function for worker model selection.

Per ``01-requirements.md`` §F4.6 and ``02-architecture.md`` §7.4 (and
ADR-0023), the routing layer maps ``(task_tag, project_id) → provider_id``
through a small task-tag vocabulary, per-project user-editable overrides,
and a built-in global defaults table. The function is **pure**: the
caller (the runner's spawn path) is responsible for loading the
overrides from ``RoutingOverridesStore``, the project's ``local_only``
flag from ``ProjectsStore``, and for the side effects (audit-log entry,
local-only invariant assertion, run-row tagging).

Keeping the lookup pure means tests can cover the resolution-order
matrix without spinning up SQLite or a provider registry, and the same
function is called identically from the spawner, the conversational
edit path's preview, and the ``routing.get`` IPC handler.
"""

from __future__ import annotations

import platform
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

# Six tags is enough headroom for v1; new tags get added when a real
# use case forces them. The ``default`` slot is the fallback for plan
# nodes that arrive untagged (legacy plans, non-lead spawns, the
# planner before it learns to tag) — every routing target must define
# a ``default`` row so the fallback can always resolve.
_BUILT_IN_TAGS: tuple[str, ...] = (
    "default",
    "coding",
    "image",
    "research",
    "writing",
    "quick",
)

TASK_TAGS: frozenset[str] = frozenset(_BUILT_IN_TAGS)
"""The recognized task-tag vocabulary. Tags outside this set fall
through to the ``default`` route. Lowercase strings; the lead is
responsible for normalizing before tagging a plan node."""


# v1 baseline: every tag → ``anthropic``. The model dimension stays at
# the provider's default for v1 (per ADR-0023, "tune from real data");
# routing per-model is a schema extension when usage data warrants it.
DEFAULT_GLOBAL_DEFAULTS: Mapping[str, str] = {
    "default": "anthropic",
    "coding": "anthropic",
    "image": "anthropic",
    "research": "anthropic",
    "writing": "anthropic",
    "quick": "anthropic",
}


class MatchedRule(StrEnum):
    """Which resolution branch produced the route."""

    LOCAL_ONLY = "local_only"
    OVERRIDE = "override"
    GLOBAL = "global"


@dataclass(frozen=True)
class RouteDecision:
    """Resolved route for one ``(task_tag, project_id)`` pair.

    ``effective_tag`` is the tag the resolver matched on — if the
    caller passed an unknown tag, it falls through to ``default`` and
    that fallthrough is recorded here so the audit trail is honest.
    """

    provider_id: str
    task_tag: str
    effective_tag: str
    matched: MatchedRule

    def to_audit_payload(self, *, project_id: str | None) -> dict[str, str | None]:
        """Shape the decision for the cross-run action log.

        The ``decision`` action kind is a generic bucket; the
        ``action`` key narrows it so listings can filter routing
        decisions out from drift / tool decisions without a separate
        action kind.
        """
        return {
            "action": "route_worker",
            "projectId": project_id,
            "taskTag": self.task_tag,
            "effectiveTag": self.effective_tag,
            "providerId": self.provider_id,
            "matched": self.matched.value,
        }


def normalize_task_tag(task_tag: str | None) -> str:
    """Map a raw tag to one of the recognized vocabulary entries.

    Whitespace and case are normalized; unknown tags collapse to
    ``default`` so the routing function can guarantee a hit.
    """
    if task_tag is None:
        return "default"
    cleaned = task_tag.strip().lower()
    if cleaned in TASK_TAGS:
        return cleaned
    return "default"


def select_local_provider_for(task_tag: str) -> str:
    """Pick a local provider id for ``task_tag``.

    The choice is platform-driven for v1: Apple Silicon prefers MLX
    for on-device inference; everything else falls back to Ollama.
    Per-tag local routing (e.g., image → MLX with a vision model) is
    a future tuning that uses the same hook.
    """
    del task_tag  # Reserved for per-tag local tuning.
    # Apple Silicon prefers MLX; everything else (Linux, Intel Macs,
    # Windows) falls back to Ollama. ``platform.system()`` is used
    # rather than ``sys.platform`` so mypy doesn't specialise the
    # check to the local type-checker's platform and complain that
    # the other branch is unreachable.
    if platform.system() == "Darwin":
        return "mlx"
    return "ollama"


def route_worker(
    *,
    task_tag: str | None,
    project_overrides: Mapping[str, str] | None = None,
    project_local_only: bool = False,
    global_defaults: Mapping[str, str] | None = None,
) -> RouteDecision:
    """Resolve ``(task_tag, project_id) → RouteDecision``.

    Resolution order:

    1. ``project_local_only`` short-circuits to a local provider —
       a privacy invariant (F3.8) trumps every override and default.
    2. A per-project override for the effective tag wins next.
    3. The built-in global default answers otherwise.

    The function is total: every input produces a ``RouteDecision``,
    and the ``default`` fallback in ``global_defaults`` is the
    backstop that catches unknown tags. A caller that supplies
    ``global_defaults`` without a ``default`` key gets a ``KeyError``
    — that's a programming error, not a runtime case.
    """
    effective_tag = normalize_task_tag(task_tag)
    raw_tag = task_tag if task_tag is not None else "default"

    if project_local_only:
        return RouteDecision(
            provider_id=select_local_provider_for(effective_tag),
            task_tag=raw_tag,
            effective_tag=effective_tag,
            matched=MatchedRule.LOCAL_ONLY,
        )

    overrides = project_overrides or {}
    if effective_tag in overrides:
        return RouteDecision(
            provider_id=overrides[effective_tag],
            task_tag=raw_tag,
            effective_tag=effective_tag,
            matched=MatchedRule.OVERRIDE,
        )

    defaults = global_defaults if global_defaults is not None else DEFAULT_GLOBAL_DEFAULTS
    provider_id = defaults.get(effective_tag) or defaults["default"]
    return RouteDecision(
        provider_id=provider_id,
        task_tag=raw_tag,
        effective_tag=effective_tag,
        matched=MatchedRule.GLOBAL,
    )


class LocalOnlyViolation(Exception):
    """A non-local provider was about to run inside a ``local_only`` project.

    Raised by the spawn path's belt-and-braces check (per F3.8 / ADR-0023).
    The caller is expected to surface this as a run-status ``errored``
    rather than letting the spawn proceed — a stale override or a
    code path that didn't consult the routing layer must not leak
    project data to a cloud token.
    """


__all__ = [
    "DEFAULT_GLOBAL_DEFAULTS",
    "TASK_TAGS",
    "LocalOnlyViolation",
    "MatchedRule",
    "RouteDecision",
    "normalize_task_tag",
    "route_worker",
    "select_local_provider_for",
]
