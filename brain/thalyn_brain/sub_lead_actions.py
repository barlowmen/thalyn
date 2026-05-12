"""Sub-lead spawn action for the action registry.

Wires the F2.3 conversational flow: the parent lead suggests
spawning a sub-lead for a facet, the user replies "yes / spawn one /
sounds good", and the matcher routes to ``subLead.spawn``. The
executor resolves the parent lead by display name (or by `under <name>`
fragment) and runs ``LeadLifecycle.spawn_sub_lead``.

Two phrasings the matcher recognises:

1. Imperative: ``"spawn a sub-lead for <facet> under <lead>"``,
   ``"create SubLead-<facet> under <lead>"``, ``"spin up a sub-lead
   for <facet> under <lead>"``.
2. Reply-shape: ``"<lead-name>, spin up a sub-lead for <facet>"``
   (matches the parent-suggests-sub-lead pattern where the user
   addresses the parent first, then asks for the spawn). The
   matcher captures the parent name from the leading address and the
   facet from the imperative tail.

Depth-cap is enforced at the lifecycle layer; an over-cap spawn
surfaces as a depth message back to the user with explicit-override
guidance. The action is **not** hard-gated — spawning a sub-lead is
additive and reversible (the user can archive it back), and gating
each spawn would defeat the conversational pattern. Destructive ops
on sub-leads (archive / pause / merge re-parent) live behind their
own gates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionMatch,
    ActionRegistry,
    ActionResult,
)
from thalyn_brain.agents import AgentRecord, AgentRecordsStore
from thalyn_brain.lead_lifecycle import (
    DEPTH_CAP,
    DepthCapExceededError,
    LeadLifecycle,
    LeadLifecycleError,
    SubLeadSpawnRequest,
)

SUB_LEAD_SPAWN_ACTION = "subLead.spawn"

# "spawn|create|spin up [a] sub[-]lead for <facet> under <lead-name>"
_IMPERATIVE_RE = re.compile(
    r"""
    ^\s*
    (?:thalyn[,:\s]+)?
    (?:please\s+)?
    (?:spawn|create|spin\s+up|make|start)
    \s+(?:a\s+)?
    (?:sub[-\s]?lead|sublead)
    \s+for\s+
    (?P<facet>.+?)
    \s+under\s+
    (?P<parent>.+?)
    \s*[.!?]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "<lead-name>, spawn|spin up [a] sub-lead for <facet>" — the user
# addresses the parent first then asks. The leading-address shape
# mirrors how lead delegation matches its addressed lead.
_REPLY_RE = re.compile(
    r"""
    ^\s*
    (?P<parent>[A-Za-z][A-Za-z0-9._\-]*(?:\s+[A-Za-z0-9._\-]+)*?)
    \s*[,:]\s*
    (?:please\s+)?
    (?:spawn|create|spin\s+up|make|start)
    \s+(?:a\s+)?
    (?:sub[-\s]?lead|sublead)
    \s+for\s+
    (?P<facet>.+?)
    \s*[.!?]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class SubLeadSpawnMatcher:
    """Matches the imperative + reply-shape phrasings.

    The matcher only captures structure; resolving the parent name to
    an ``AGENT_RECORD`` happens at execute time so a rename between
    matcher and execute doesn't surface as a stale id (mirrors
    ``ProjectMergeMatcher``'s deferred resolve).
    """

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any],
    ) -> ActionMatch | None:
        cleaned = prompt.strip()
        for pattern in (_IMPERATIVE_RE, _REPLY_RE):
            match = pattern.match(cleaned)
            if match is None:
                continue
            facet = _clean_facet(match.group("facet"))
            parent = match.group("parent").strip()
            if not facet or not parent:
                return None
            return ActionMatch(
                action_name=SUB_LEAD_SPAWN_ACTION,
                inputs={"parent_lead": parent, "scope_facet": facet},
                preview=f"Spawn a sub-lead for '{facet}' under '{parent}'",
            )
        return None


def register_sub_lead_actions(
    registry: ActionRegistry,
    *,
    agents: AgentRecordsStore,
    lifecycle: LeadLifecycle,
) -> None:
    """Register ``subLead.spawn`` + its matcher on ``registry``.

    Wires the conversational substrate F2.3 calls for: parent lead
    suggests, user accepts in chat, the registry runs the spawn
    against the lifecycle. The depth-cap path surfaces as a
    user-facing message rather than crashing the action.
    """

    async def spawn(inputs: Mapping[str, Any]) -> ActionResult:
        parent_name = str(inputs.get("parent_lead", "")).strip()
        facet = str(inputs.get("scope_facet", "")).strip()
        if not parent_name or not facet:
            return ActionResult(
                confirmation=(
                    "I need both a parent lead and a facet — try 'spawn a "
                    "sub-lead for <facet> under <lead>'."
                )
            )
        candidates = await agents.list_all(status="active")
        candidates += await agents.list_all(status="paused")
        parent, parent_err = _resolve_parent(candidates, parent_name)
        if parent_err is not None:
            return ActionResult(confirmation=parent_err)
        assert parent is not None
        if parent.status != "active":
            return ActionResult(
                confirmation=(
                    f"'{parent.display_name}' is {parent.status} — resume it "
                    "before spawning a sub-lead under it."
                )
            )
        try:
            record = await lifecycle.spawn_sub_lead(
                SubLeadSpawnRequest(
                    parent_agent_id=parent.agent_id,
                    scope_facet=facet,
                ),
            )
        except DepthCapExceededError as exc:
            return ActionResult(
                confirmation=(
                    f"That spawn would land at depth {exc.attempted_depth}; v1 "
                    f"caps depth at {DEPTH_CAP} (top-level lead → sub-lead). "
                    "If you really want it deeper, archive the intermediate "
                    "sub-lead first or override the cap from settings."
                )
            )
        except LeadLifecycleError as exc:
            return ActionResult(confirmation=f"Couldn't spawn the sub-lead: {exc}")
        confirmation = (
            f"Spawned {record.display_name} under {parent.display_name} for '{record.scope_facet}'."
        )
        followup: dict[str, Any] = {
            "subLeadId": record.agent_id,
            "parentAgentId": parent.agent_id,
            "scopeFacet": record.scope_facet,
            "agent": record.to_wire(),
        }
        return ActionResult(confirmation=confirmation, followup=followup)

    registry.register(
        Action(
            name=SUB_LEAD_SPAWN_ACTION,
            description=(
                "Spawn a persistent sub-lead under an existing project lead "
                "(e.g. 'spawn a sub-lead for UI under Lead-Alpha'). The "
                "sub-lead inherits the parent's project and owns the named "
                "facet of the work."
            ),
            inputs=(
                ActionInput(
                    name="parent_lead",
                    description="The lead the sub-lead reports to (display name).",
                    kind="string",
                ),
                ActionInput(
                    name="scope_facet",
                    description="The slice of the parent's project this sub-lead owns.",
                    kind="string",
                ),
            ),
            executor=spawn,
        )
    )
    registry.register_matcher(SubLeadSpawnMatcher())


def _resolve_parent(
    candidates: list[AgentRecord],
    target: str,
) -> tuple[AgentRecord | None, str | None]:
    """Resolve a free-form parent name against the lead/sub-lead list.

    Mirrors ``project_actions._resolve_project``: exact name → unique
    case-insensitive prefix → ambiguous, with a friendly error when
    the name doesn't resolve. Only ``lead`` / ``sub_lead`` rows are
    considered — the brain itself can't be a parent.
    """
    cleaned = target.strip().rstrip(".!?,").strip()
    if not cleaned:
        return None, "I didn't catch the parent lead's name."
    lowered = cleaned.lower()
    leads = [record for record in candidates if record.kind in {"lead", "sub_lead"}]
    for record in leads:
        if record.display_name.lower() == lowered:
            return record, None
    prefix_matches = [r for r in leads if r.display_name.lower().startswith(lowered)]
    if len(prefix_matches) == 1:
        return prefix_matches[0], None
    if len(prefix_matches) > 1:
        names = ", ".join(sorted(r.display_name for r in prefix_matches))
        return None, (f"'{cleaned}' could be any of {names} — try the full lead name.")
    return None, f"I don't know a lead named '{cleaned}'."


def _clean_facet(raw: str) -> str:
    """Strip articles + trailing punctuation from the facet capture.

    Users naturally say "for the harness" or "for cost monitoring." —
    keep the meaningful slug-ish phrase ("harness" / "cost monitoring")
    so the lifecycle's namespace derivation works the same way as a
    bare ``scope_facet`` argument.
    """
    cleaned = raw.strip().rstrip(".!?,").strip()
    if cleaned.lower().startswith("the "):
        cleaned = cleaned[4:].strip()
    return cleaned


__all__ = [
    "SUB_LEAD_SPAWN_ACTION",
    "SubLeadSpawnMatcher",
    "register_sub_lead_actions",
]
