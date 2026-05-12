"""Project-mobility actions for the action registry.

"Thalyn, merge UI into Thalyn" / "move UI into Thalyn" lands here. The
matcher captures the absorbed and surviving project names from the
prompt; the executor resolves them through ``ProjectsStore`` and runs
the full two-phase plan + apply path. Merge is hard-gated — destructive
enough that we want explicit per-action approval even when initiated
via Thalyn, mirroring the IPC layer's plan-first / apply-on-confirm
shape.

Project naming inside the matcher is case-insensitive and forgiving of
trailing punctuation; resolution against the projects store tries an
exact-name match first, then slug, then a unique case-insensitive
prefix. Ambiguity surfaces as a confirmation message rather than a
silent pick — the user re-asks with a more specific name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionMatch,
    ActionRegistry,
    ActionResult,
)
from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.memory import MemoryStore
from thalyn_brain.project_merge import (
    ProjectMergeError,
    apply_merge_plan,
    compute_merge_plan,
)
from thalyn_brain.projects import Project, ProjectsStore
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.threads import ThreadsStore

PROJECT_MERGE_ACTION = "project.merge"
PROJECT_MERGE_HARD_GATE_KIND = "project_merge"

# "merge|move <from> into|to <to>" — the most common phrasings F3.4
# uses interchangeably. Captures the targets greedily on both sides so
# multi-word project names ("Tax Prep 2026") survive intact.
_MERGE_RE = re.compile(
    r"""
    ^\s*
    (?:thalyn[,:\s]+)?
    (?:please\s+)?
    (?:merge|move)
    \s+
    (?P<from>.+?)
    \s+
    (?:into|to|in\s+to)
    \s+
    (?P<to>.+?)
    \s*[.!?]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class ProjectMergeMatcher:
    """Matches "merge / move X into Y" phrasings.

    Captures the from / to project names verbatim. The executor does
    the name → id resolution at execute time so the matcher stays
    sync (and so a project rename between matcher and execute doesn't
    surface as a stale id).
    """

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any],
    ) -> ActionMatch | None:
        match = _MERGE_RE.match(prompt.strip())
        if match is None:
            return None
        from_name = match.group("from").strip()
        to_name = match.group("to").strip()
        if not from_name or not to_name:
            return None
        # Same target on both sides — caught at execute time too, but
        # surfacing the match early lets us walk-input rather than
        # silently dropping.
        return ActionMatch(
            action_name=PROJECT_MERGE_ACTION,
            inputs={"from_project": from_name, "into_project": to_name},
            preview=f"Merge '{from_name}' into '{to_name}' (archives '{from_name}')",
        )


def register_project_actions(
    registry: ActionRegistry,
    *,
    projects: ProjectsStore,
    threads: ThreadsStore,
    memory: MemoryStore,
    agents: AgentRecordsStore,
    routing_overrides: RoutingOverridesStore,
    data_dir: Path | None = None,
) -> None:
    """Register ``project.merge`` + its matcher on ``registry``.

    The executor runs the full plan + apply in one go; hard-gating
    holds it until the user approves through the
    ``action.approval_required`` dialog. The renderer can still hit
    the IPC ``project.merge`` directly for a UI-driven flow that
    shows the plan before confirming — this surface is the
    conversational shortcut for users who know what they want.
    """

    async def merge(inputs: Mapping[str, Any]) -> ActionResult:
        from_name = str(inputs.get("from_project", "")).strip()
        to_name = str(inputs.get("into_project", "")).strip()
        if not from_name or not to_name:
            return ActionResult(
                confirmation=(
                    "I need both projects to merge — try 'merge <project> into <project>'."
                )
            )
        active = await projects.list_all(status="active")
        paused = await projects.list_all(status="paused")
        candidates = active + paused
        from_project, from_err = _resolve_project(candidates, from_name)
        if from_err is not None:
            return ActionResult(confirmation=from_err)
        to_project, to_err = _resolve_project(candidates, to_name)
        if to_err is not None:
            return ActionResult(confirmation=to_err)
        assert from_project is not None  # _resolve_project narrows
        assert to_project is not None
        if from_project.project_id == to_project.project_id:
            return ActionResult(
                confirmation=(
                    f"'{from_project.name}' and '{to_project.name}' are the "
                    "same project — nothing to merge."
                )
            )
        try:
            plan = await compute_merge_plan(
                from_project_id=from_project.project_id,
                into_project_id=to_project.project_id,
                projects=projects,
                threads=threads,
                memory=memory,
                agents=agents,
                routing_overrides=routing_overrides,
            )
            outcome = await apply_merge_plan(plan, data_dir=data_dir)
        except ProjectMergeError as exc:
            return ActionResult(confirmation=f"Couldn't merge: {exc}")
        confirmation = (
            f"Merged '{from_project.name}' into '{to_project.name}'. "
            f"Rewrote {outcome.thread_turns_rewritten} turn(s), migrated "
            f"{outcome.memory_entries_migrated} memory entr(ies); "
            f"'{from_project.name}' is now archived."
        )
        followup: dict[str, Any] = {
            "mergeId": outcome.merge_id,
            "fromProjectId": from_project.project_id,
            "intoProjectId": to_project.project_id,
            "plan": plan.to_wire(),
            "outcome": outcome.to_wire(),
        }
        return ActionResult(confirmation=confirmation, followup=followup)

    registry.register(
        Action(
            name=PROJECT_MERGE_ACTION,
            description=(
                "Merge one project into another (e.g. 'merge UI into Thalyn'). "
                "Rewrites conversation tags, migrates memory entries, retires "
                "the absorbed lead, and archives the absorbed project. "
                "Hard-gated — Thalyn surfaces a confirmation dialog before "
                "applying."
            ),
            inputs=(
                ActionInput(
                    name="from_project",
                    description="The project to absorb (will be archived).",
                    kind="project_id",
                ),
                ActionInput(
                    name="into_project",
                    description="The project to merge into (the surviving project).",
                    kind="project_id",
                ),
            ),
            executor=merge,
            hard_gate=True,
            hard_gate_kind=PROJECT_MERGE_HARD_GATE_KIND,
        )
    )
    registry.register_matcher(ProjectMergeMatcher())


def _resolve_project(
    candidates: list[Project],
    target: str,
) -> tuple[Project | None, str | None]:
    """Resolve a free-form name against the active+paused project list.

    Returns either a project + None (success), or None + a message
    explaining what went wrong (unknown name, ambiguous prefix). The
    error message is intentionally user-friendly — it flows straight
    into the conversational confirmation.
    """
    cleaned = target.strip().rstrip(".!?,").strip()
    if not cleaned:
        return None, "I didn't catch the project name."
    lowered = cleaned.lower()
    # Exact name → exact slug → unique case-insensitive prefix.
    for project in candidates:
        if project.name.lower() == lowered:
            return project, None
    for project in candidates:
        if project.slug.lower() == lowered:
            return project, None
    prefix_matches = [p for p in candidates if p.name.lower().startswith(lowered)]
    if len(prefix_matches) == 1:
        return prefix_matches[0], None
    if len(prefix_matches) > 1:
        names = ", ".join(sorted(p.name for p in prefix_matches))
        return None, (f"'{cleaned}' could be any of {names} — try the full project name.")
    return None, f"I don't have a project named '{cleaned}'."


__all__ = [
    "PROJECT_MERGE_ACTION",
    "PROJECT_MERGE_HARD_GATE_KIND",
    "ProjectMergeMatcher",
    "register_project_actions",
]
