"""Action registry — the conversational surface for configurable actions.

Every configurable surface in Thalyn (connectors, memory, routing,
project lifecycle, theme, schedules, …) registers a structured
``Action`` here so Thalyn can discover it, describe its inputs, walk
the user through missing fields, and execute it on confirmation. The
registry doesn't define a *new* substrate — the executors are the
same code paths the GUI calls. The registry exposes them
conversationally (per F9.4 / F9.5).

The registry exposes three shapes:

1. ``list_summaries()`` — name + description for every registered
   action. Lean; this is what Thalyn carries in his session context
   so he knows what surfaces exist without paying for every schema.
2. ``describe(name)`` — full input schema for one action. Pulled on
   demand when Thalyn needs to walk the user through inputs.
3. ``execute(name, inputs)`` — validate inputs against the schema,
   run the executor, return an ``ActionResult``. Hard-gated actions
   (per F12.5) return a ``hard_gate_pending`` shape rather than
   running the executor — the caller stages an Approval row and the
   resolver completes the action when the user approves.

Matchers are an additional pluggable layer: each ``ActionMatcher``
parses a natural-language prompt into ``(action_name, inputs,
missing_inputs)``. The conversational pipeline tries matchers in
order; the first hit drives ``execute``. Matchers stay tightly
scoped per action so the registry doesn't grow a parallel NLU layer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ActionInput:
    """A single input slot for an action.

    ``required`` inputs must be present before the executor runs;
    ``execute`` raises ``ActionValidationError`` when they aren't.
    ``choices`` is an enum-like hint: when present, ``execute``
    rejects inputs outside the set.

    ``kind`` is a Thalyn-side hint for how to phrase the input back
    to the user ("which connector?" vs. "which provider?"); it is
    not a runtime type-check. The executor is responsible for its
    own value coercion.
    """

    name: str
    description: str
    kind: Literal[
        "string",
        "provider_id",
        "project_id",
        "connector_id",
        "task_tag",
        "memory_body",
        "memory_scope",
        "bool",
        "theme",
    ] = "string"
    required: bool = True
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Action:
    """A configurable surface, registered for conversational discovery.

    ``executor`` runs the same code path the GUI invokes. The
    registry's only job is to validate inputs and route the call.

    ``hard_gate`` flags actions that may not execute on a single
    user turn — publish, send-on-behalf, destructive ops. The
    registry refuses to run them directly; the caller is expected
    to stage an Approval (per ``approvals`` / F12.5) and resolve
    the gate before invoking ``execute_resolved``.
    """

    name: str
    description: str
    inputs: tuple[ActionInput, ...] = ()
    executor: Callable[[Mapping[str, Any]], Awaitable[ActionResult]] | None = None
    hard_gate: bool = False
    hard_gate_kind: str | None = None

    def find_input(self, name: str) -> ActionInput | None:
        for slot in self.inputs:
            if slot.name == name:
                return slot
        return None


@dataclass(frozen=True)
class ActionSummary:
    """The lean shape Thalyn carries in session context.

    Names + descriptions only; no input schema. Schemas come from
    ``describe`` on demand so the per-turn context stays cheap.
    """

    name: str
    description: str
    hard_gate: bool


@dataclass(frozen=True)
class ActionResult:
    """The outcome of an executor.

    ``confirmation`` is the brain's reply text the caller surfaces
    back to the user. ``followup`` is an optional structured
    payload (e.g. an OAuth URL for the browser drawer) the
    renderer subscribes to via the streamed notification channel.
    """

    confirmation: str
    followup: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ActionMatch:
    """A matcher's verdict against an utterance.

    ``inputs`` carries the values the matcher pulled from the
    prompt. ``missing_inputs`` is the list of required slots the
    matcher couldn't fill from the prompt alone; when non-empty
    the dispatch path walks the user through them rather than
    executing immediately.

    ``preview`` is a short one-line description of what the
    action would do; the walk-input prompt surfaces it so the user
    knows what they're being asked to confirm.
    """

    action_name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    missing_inputs: tuple[str, ...] = ()
    preview: str | None = None

    def with_inputs(self, extra: Mapping[str, Any]) -> ActionMatch:
        merged = dict(self.inputs)
        merged.update(extra)
        still_missing = tuple(name for name in self.missing_inputs if name not in extra)
        return replace(self, inputs=merged, missing_inputs=still_missing)


class ActionMatcher(Protocol):
    """A natural-language parser for one (or a few related) actions.

    Matchers are deliberately tight in scope: a single matcher
    handles routing edits, another handles memory writes, etc. The
    registry tries each matcher in turn; the first hit drives the
    dispatch. ``None`` means no match — the caller falls back to
    the normal reply flow.
    """

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any],
    ) -> ActionMatch | None: ...


class ActionRegistryError(Exception):
    """Base for registry-level errors."""


class UnknownActionError(ActionRegistryError):
    """Raised when describe / execute is called on an unregistered name."""


class ActionValidationError(ActionRegistryError):
    """Raised when ``execute`` is called with invalid or incomplete inputs."""


class HardGateNotResolvedError(ActionRegistryError):
    """Raised when a hard-gated action is invoked without an approval."""


class ActionRegistry:
    """A registry of configurable actions + their matchers.

    The registry is small on purpose — it doesn't own state, just
    indexes ``Action`` objects by name and keeps an ordered list
    of matchers. Wiring lives in ``__main__`` where each surface
    (routing, memory, connectors, …) calls ``register`` with its
    own actions + matchers.
    """

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}
        self._matchers: list[ActionMatcher] = []

    def register(self, action: Action) -> None:
        if action.name in self._actions:
            raise ActionRegistryError(f"action already registered: {action.name}")
        self._actions[action.name] = action

    def register_matcher(self, matcher: ActionMatcher) -> None:
        self._matchers.append(matcher)

    def get(self, name: str) -> Action:
        action = self._actions.get(name)
        if action is None:
            raise UnknownActionError(f"unknown action: {name}")
        return action

    def list_summaries(self) -> list[ActionSummary]:
        return [
            ActionSummary(
                name=action.name,
                description=action.description,
                hard_gate=action.hard_gate,
            )
            for action in sorted(self._actions.values(), key=lambda a: a.name)
        ]

    def describe(self, name: str) -> dict[str, Any]:
        action = self.get(name)
        return {
            "name": action.name,
            "description": action.description,
            "hardGate": action.hard_gate,
            "hardGateKind": action.hard_gate_kind,
            "inputs": [
                {
                    "name": slot.name,
                    "description": slot.description,
                    "kind": slot.kind,
                    "required": slot.required,
                    "choices": list(slot.choices) if slot.choices is not None else None,
                }
                for slot in action.inputs
            ],
        }

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> ActionMatch | None:
        ctx = context or {}
        for matcher in self._matchers:
            match = matcher.try_match(prompt, context=ctx)
            if match is not None:
                return match
        return None

    async def execute(
        self,
        name: str,
        inputs: Mapping[str, Any],
        *,
        hard_gate_resolved: bool = False,
    ) -> ActionResult:
        action = self.get(name)
        if action.executor is None:
            raise ActionRegistryError(
                f"action {name!r} has no executor wired",
            )
        if action.hard_gate and not hard_gate_resolved:
            raise HardGateNotResolvedError(
                f"action {name!r} is hard-gated; resolve the approval first",
            )
        validated = _validate_inputs(action, inputs)
        return await action.executor(validated)


def _validate_inputs(
    action: Action,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate ``inputs`` against ``action.inputs``.

    Required inputs must be present; choice-constrained inputs must
    fall in the allowed set. Unknown keys are dropped silently —
    the executor only sees inputs the schema declared.
    """

    out: dict[str, Any] = {}
    known: set[str] = set()
    for slot in action.inputs:
        known.add(slot.name)
        if slot.name not in inputs:
            if slot.required:
                raise ActionValidationError(
                    f"missing required input {slot.name!r} for action {action.name!r}",
                )
            continue
        value = inputs[slot.name]
        if slot.choices is not None and value not in slot.choices:
            raise ActionValidationError(
                f"input {slot.name!r} for action {action.name!r} must be "
                f"one of {sorted(slot.choices)} (got {value!r})",
            )
        out[slot.name] = value
    return out


def collect_missing_required(
    action: Action,
    inputs: Mapping[str, Any],
) -> tuple[str, ...]:
    """Helper: which required inputs are still missing for this action.

    Used by the walk-input pipeline to decide whether to ask the
    user for more before executing.
    """

    return tuple(slot.name for slot in action.inputs if slot.required and slot.name not in inputs)


def sequence_matchers(matchers: Iterable[ActionMatcher]) -> Sequence[ActionMatcher]:
    """Stable sequence of matchers; preserved here for symmetry with
    callers that want to read the order without poking the registry
    internals.
    """

    return list(matchers)


__all__ = [
    "Action",
    "ActionInput",
    "ActionMatch",
    "ActionMatcher",
    "ActionRegistry",
    "ActionRegistryError",
    "ActionResult",
    "ActionSummary",
    "ActionValidationError",
    "HardGateNotResolvedError",
    "UnknownActionError",
    "collect_missing_required",
    "sequence_matchers",
]
