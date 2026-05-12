"""Brain → lead delegation flow.

The ``thread.send`` handler asks ``find_addressed_lead`` whether the
incoming message names an active lead or sub-lead. When it does, the
delegate path runs:

1. The lead's ``stream_chat`` produces the underlying response. The
   lead has its own ``system_prompt`` (with a default identity if the
   row carries an empty one) and may have its own provider — looked
   up against the same ``ProviderRegistry`` the brain uses.
2. The lead's full reply runs through ``sanity_check_lead_reply``
   before the brain forwards it. The critic is heuristic-only:
   it flags empty replies and explicit hedges so the brain can
   surface a confidence note. Future stages plug an LLM-judge in
   without changing the critic's call site.
3. The brain composes its outgoing surface text with a preamble plus
   the lead's reply prefixed by ``"<lead-name> says: "`` (the shape
   ``02-architecture.md`` §6.3 records). When a sub-lead answers, the
   prefix carries the attribution chain — ``"SubLead-UI (under
   Lead-Alpha) says: "`` — so the user can see who the message came
   through without drilling into provenance. The renderer's
   drill-into-source still works the same way for either.
4. The reply is evaluated for question density via
   ``evaluate_lead_escalation``. When the lead's answer carries
   enough open questions to justify a side-conversation (F2.5), the
   handler emits a ``lead.escalation`` notification so the renderer
   can surface a "drop into Lead-X" CTA inline. Low-density replies
   stay on the relay path — ``evaluate_lead_escalation`` returns
   ``None`` so the brain doesn't have to special-case the absence.

Sub-leads share the same primitive as top-level leads (F2.3 / ADR-0021
recursive case): ``find_addressed_lead`` accepts the union of both
kinds, and ``effective_system_prompt`` works against an ``AgentRecord``
of either kind. The attribution-chain helpers walk
``parent_agent_id`` so the renderer can show the path back to the
brain regardless of nesting depth.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from thalyn_brain.agents import AgentRecord, AgentRecordsStore
from thalyn_brain.project_context import ProjectContext, merge_into_system_prompt
from thalyn_brain.provider import (
    ChatChunk,
    ChatErrorChunk,
    ChatTextChunk,
    LlmProvider,
)

LEAD_INTRO_TEMPLATE = "Asking {name} now…"
LEAD_REPLY_PREFIX_TEMPLATE = "{name} says: "
SUB_LEAD_REPLY_PREFIX_TEMPLATE = "{name} (under {parent_name}) says: "
DEFAULT_LEAD_SYSTEM_PROMPT_TEMPLATE = (
    "You are {name}, the project lead inside Thalyn. "
    "Respond with the project context you carry; "
    "stay concise and flag uncertainty rather than guessing."
)
DEFAULT_SUB_LEAD_SYSTEM_PROMPT_TEMPLATE = (
    "You are {name}, a sub-lead under {parent_name} owning the "
    "{scope_facet!r} slice of the project. Stay focused on that facet; "
    "report findings back to the parent lead and flag uncertainty "
    "rather than guessing."
)
# Hedging phrases that bump the sanity-check confidence note. The list
# is small on purpose — the v0.23 critic is permissive and only flags
# the clearest non-answers; hardening lands when the LLM-judge does.
_HEDGE_PHRASES: tuple[str, ...] = (
    "i don't know",
    "i do not know",
    "i'm not sure",
    "i am not sure",
    "no idea",
    "unclear",
)
LOW_CONFIDENCE_NOTE = "Low-confidence reply — flagging the response for context."

# Question-density threshold for escalation. Three or more questions
# from the lead in a single reply has been the rule of thumb the
# user-research synthesis converged on — fewer than that and the
# inline relay still feels lighter than dropping into a side
# conversation; more, and the user wants the parallel surface.
ESCALATION_QUESTION_THRESHOLD = 3


@dataclass(frozen=True)
class AddressedLead:
    """The lead whose name leads the user message, with the trimmed body."""

    lead: AgentRecord
    body: str


@dataclass(frozen=True)
class EscalationSignal:
    """F2.5 escalation hint emitted alongside a lead's reply.

    ``density`` and ``suggestion`` carry the rendered intent: at
    ``high`` density the brain wants the user to consider the
    side-pane chat, while ``low`` density never reaches the renderer
    (the helper returns ``None`` instead of constructing a signal).
    """

    lead_id: str
    question_count: int
    density: Literal["low", "high"]
    suggestion: Literal["relay_inline", "open_drawer"]

    def to_wire(self) -> dict[str, Any]:
        return {
            "leadId": self.lead_id,
            "questionCount": self.question_count,
            "density": self.density,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class SanityCheckVerdict:
    """Outcome of the lead → brain hop sanity check.

    ``ok`` is the binary forward / hedge bit; ``note`` is the
    user-facing string the brain appends when ``ok`` is false. Future
    phases extend with a confidence float and a structured reason
    field; this shape is the minimum the brain renders.
    """

    ok: bool
    note: str | None


def find_addressed_lead(
    message: str,
    leads: list[AgentRecord],
) -> AddressedLead | None:
    """Return the lead the user is addressing, plus the message body.

    Two address shapes are recognised:

    1. **Leading-name address.** The message starts with one lead's
       ``display_name`` (case-insensitive) followed by a separator
       (``,``, ``:``, ``-`` / ``—``, or whitespace+text). The body
       returned to the lead is the message minus the leading address.
    2. **Mid-message ``@`` mention.** The message contains
       ``@<display_name>`` anywhere (case-insensitive, word-bounded).
       The body returned is the *full* message — the mention isn't
       stripped because the surrounding sentence carries the
       routing intent ("hey @Lead-X, …", "ask @Lead-X about Y").

    A leading-name match outranks an ``@`` mention when both forms
    parse against the same message (the user clearly committed to
    addressing the lead). Ambiguous matches across leads yield
    ``None`` so the caller falls back to a direct reply rather than
    guessing.
    """
    if not message.strip():
        return None
    leading_matches: list[tuple[AgentRecord, str]] = []
    mention_matches: list[tuple[AgentRecord, str]] = []
    for lead in leads:
        if lead.status != "active":
            continue
        body = _strip_addressing(message, lead.display_name)
        if body is not None:
            leading_matches.append((lead, body))
            continue
        if _has_at_mention(message, lead.display_name):
            mention_matches.append((lead, message))
    matches = leading_matches or mention_matches
    if len(matches) != 1:
        return None
    lead, body = matches[0]
    return AddressedLead(lead=lead, body=body or message)


def effective_system_prompt(
    lead: AgentRecord,
    *,
    project_context: ProjectContext | None = None,
    parent_lead: AgentRecord | None = None,
) -> str:
    """Lead's stored prompt, or a default identity prompt by name.

    The default keeps a fresh lead useful out of the box — calling a
    blank-system-prompt provider produces ungrounded chat. The user's
    rename surfaces here too: if the user has renamed the lead, the
    default prompt names the renamed identity.

    Sub-leads (``kind='sub_lead'``) use a default that names the
    parent lead and the ``scope_facet`` so the provider sees the
    bounded slice of work the sub-lead owns. The caller is expected
    to pass ``parent_lead`` for sub-lead spans; when omitted, the
    parent's name falls back to ``"the project lead"`` so the prompt
    still parses.

    When ``project_context`` is supplied (a parsed ``THALYN.md`` /
    ``CLAUDE.md`` from the project's workspace root), it's merged in
    front of the lead's prompt so every delegation hop carries the
    human-editable project corpus. Per F6.3 this is the project
    memory tier the lead reads on session start; the merged prompt
    is what the provider actually sees.
    """
    if lead.system_prompt:
        base = lead.system_prompt
    elif lead.kind == "sub_lead":
        parent_name = parent_lead.display_name if parent_lead else "the project lead"
        base = DEFAULT_SUB_LEAD_SYSTEM_PROMPT_TEMPLATE.format(
            name=lead.display_name,
            parent_name=parent_name,
            scope_facet=lead.scope_facet or "facet",
        )
    else:
        base = DEFAULT_LEAD_SYSTEM_PROMPT_TEMPLATE.format(name=lead.display_name)
    merged = merge_into_system_prompt(base, project_context)
    return merged or base


@dataclass(frozen=True)
class AttributionChain:
    """Top-down chain from the brain to the addressed lead.

    ``names`` is in render order: ``["Thalyn", "Lead-Alpha", "SubLead-UI"]``
    means the brain delegated to Lead-Alpha, who delegated to
    SubLead-UI. ``agent_ids`` is the same order. The renderer's
    drill-into-source uses ``agent_ids`` to navigate; the chat
    surface uses ``names`` for the human-readable chain.
    """

    names: tuple[str, ...]
    agent_ids: tuple[str, ...]

    def to_wire(self) -> dict[str, Any]:
        return {
            "names": list(self.names),
            "agentIds": list(self.agent_ids),
        }


async def build_attribution_chain(
    addressed: AgentRecord,
    *,
    agents: AgentRecordsStore,
    brain_display_name: str = "Thalyn",
) -> AttributionChain:
    """Walk ``parent_agent_id`` up to the brain and return the chain.

    Top-level leads produce a 2-name chain (``Thalyn → Lead-X``);
    sub-leads produce a 3-name chain (``Thalyn → Lead-X →
    SubLead-Y``). Deeper trees (when an override let a sub-sub-lead
    spawn) produce a longer chain — the helper walks until the
    parent_agent_id is None or until it hits a node whose parent
    isn't in the registry.
    """
    walked: list[AgentRecord] = [addressed]
    cursor = addressed.parent_agent_id
    seen: set[str] = {addressed.agent_id}
    while cursor is not None:
        if cursor in seen:
            # Defensive: a parent_agent_id cycle shouldn't be
            # possible, but if one exists we surface what we have
            # rather than looping forever.
            break
        parent = await agents.get(cursor)
        if parent is None:
            break
        walked.append(parent)
        seen.add(cursor)
        cursor = parent.parent_agent_id
    walked.reverse()
    names = [brain_display_name] + [r.display_name for r in walked]
    ids = ["agent_brain"] + [r.agent_id for r in walked]
    return AttributionChain(names=tuple(names), agent_ids=tuple(ids))


async def collect_lead_reply(
    provider: LlmProvider,
    *,
    lead: AgentRecord,
    user_message: str,
    project_context: ProjectContext | None = None,
    parent_lead: AgentRecord | None = None,
) -> tuple[str, str | None]:
    """Drive the lead's provider once and return (text, error_or_none).

    Returns the buffered text rather than streaming it through to the
    caller — the brain wraps the reply with a preamble and a
    sanity-check note before re-emitting it on the eternal-thread
    surface, so partial streaming would surface the unwrapped text.

    ``project_context`` (when supplied) folds into the system prompt
    via ``effective_system_prompt`` so the lead's provider sees the
    project's ``THALYN.md`` corpus alongside the lead's identity.
    ``parent_lead`` lets a sub-lead's default system prompt name its
    parent so the model knows where to report findings.
    """
    text_parts: list[str] = []
    error_message: str | None = None
    chunks: AsyncIterator[ChatChunk] = provider.stream_chat(
        user_message,
        system_prompt=effective_system_prompt(
            lead,
            project_context=project_context,
            parent_lead=parent_lead,
        ),
    )
    async for chunk in chunks:
        if isinstance(chunk, ChatTextChunk):
            text_parts.append(chunk.delta)
        elif isinstance(chunk, ChatErrorChunk):
            error_message = chunk.message
    return "".join(text_parts), error_message


def evaluate_lead_escalation(
    lead: AgentRecord,
    reply_text: str,
    *,
    threshold: int = ESCALATION_QUESTION_THRESHOLD,
) -> EscalationSignal | None:
    """Return an escalation signal when the lead's reply is question-dense.

    The heuristic counts ``?`` characters in the reply and treats any
    reply with at least ``threshold`` questions as high-density. Below
    the threshold the helper returns ``None`` so the brain stays on
    the inline-relay path without an explicit "low" notification.

    The threshold is configurable so a future LLM-judge can override
    it; until then the rule-of-three matches the wording in F2.5
    ("Lead-Thalyn has 6 open questions on the auth refactor — want to
    drop into a quick chat?").
    """
    count = reply_text.count("?")
    if count < threshold:
        return None
    return EscalationSignal(
        lead_id=lead.agent_id,
        question_count=count,
        density="high",
        suggestion="open_drawer",
    )


def sanity_check_lead_reply(reply_text: str) -> SanityCheckVerdict:
    """Permissive heuristic critic at the lead → brain hop.

    Flags the obvious non-answer cases (empty body, leading hedge
    phrase) so the brain can surface a confidence note. Anything else
    passes through. The architecture marks this seat as the F1.8 /
    F12.7 information-flow drift check; the call point is what
    matters in v0.23, not the verdict's sophistication.
    """
    stripped = reply_text.strip()
    if not stripped:
        return SanityCheckVerdict(
            ok=False,
            note="Lead returned an empty reply.",
        )
    lowered = stripped.lower()
    for hedge in _HEDGE_PHRASES:
        if lowered.startswith(hedge):
            return SanityCheckVerdict(ok=False, note=LOW_CONFIDENCE_NOTE)
    return SanityCheckVerdict(ok=True, note=None)


def _strip_addressing(message: str, display_name: str) -> str | None:
    """Return the message without its leading address, or None if absent.

    The matcher accepts ``Name,``, ``Name:``, ``Name -`` (with optional
    whitespace), or ``Name <whitespace> rest``. Returns the suffix
    with leading whitespace stripped so the lead's provider sees the
    actual question.
    """
    pattern = r"^\s*" + re.escape(display_name) + r"\s*(?:[,:\-—]\s*|\s+)(?P<body>.+)\Z"
    match = re.match(pattern, message, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    return match.group("body").strip()


def _has_at_mention(message: str, display_name: str) -> bool:
    """Return True if ``message`` contains an ``@<display_name>`` mention.

    Case-insensitive, anchored by a word boundary on the right so a
    longer name doesn't accidentally swallow a shorter prefix
    (``@Sam`` doesn't match the lead ``Samantha``). The ``@`` is
    the literal anchor — bare names mid-sentence don't count,
    otherwise ordinary prose mentioning the project would re-route
    every turn. The character preceding ``@`` must not be a word
    character so ``user@host`` style addresses don't count as a
    mention.
    """
    pattern = r"(?<!\w)@" + re.escape(display_name) + r"\b"
    return re.search(pattern, message, flags=re.IGNORECASE) is not None
