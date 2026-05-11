"""Routing-edit actions for the action registry.

A focused intent parser + a trio of executors the brain consults
before delegating a turn. When the user's prompt asks Thalyn to
change the project's routing (``"route coding to Sonnet 4.6"``) or
flip its local-only flag (``"make this project local-only"``), the
matcher recognises the intent, the registry runs the executor, and
the brain replies with the executor's confirmation instead of
opening an LLM turn. Patterns that don't match return ``None`` so
the regular reply flow runs unchanged.

The actions registered here are the v0.34 conversational surface
for ADR-0023's per-project routing table:

- ``routing.set_override`` — set a per-project (task_tag → provider)
  override.
- ``routing.clear_override`` — clear a per-project override.
- ``project.set_local_only`` — flip the project's privacy flag.

Each executor takes its inputs from the matcher (or, in v1.x, from
the LLM tool-use path) and lands the write against the right store.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from typing import Any

from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionMatch,
    ActionRegistry,
    ActionResult,
)
from thalyn_brain.projects import ProjectsStore
from thalyn_brain.routing import (
    RoutingOverride,
    RoutingOverridesStore,
    new_routing_override_id,
)
from thalyn_brain.routing_table import TASK_TAGS, normalize_task_tag

# Aliases the user is likely to type for each provider id. Model names
# (``sonnet``, ``opus``, ``qwen``) collapse to the provider that hosts
# them — v1 routing is provider-level (per ADR-0023), so the aliases
# resolve the user's natural phrasing without surfacing the
# provider-vs-model distinction in the conversational layer.
DEFAULT_PROVIDER_ALIASES: Mapping[str, str] = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "opus": "anthropic",
    "sonnet": "anthropic",
    "haiku": "anthropic",
    "ollama": "ollama",
    "qwen": "ollama",
    "llama": "ollama",
    "deepseek": "ollama",
    "mlx": "mlx",
    "apple": "mlx",
    "openai": "openai_compat",
    "gpt": "openai_compat",
    "openai_compat": "openai_compat",
    "openai-compatible": "openai_compat",
    "llama_cpp": "llama_cpp",
    "llama.cpp": "llama_cpp",
}

ROUTING_SET_ACTION = "routing.set_override"
ROUTING_CLEAR_ACTION = "routing.clear_override"
PROJECT_LOCAL_ONLY_ACTION = "project.set_local_only"


_TAG_ALTERNATION = "|".join(re.escape(tag) for tag in sorted(TASK_TAGS, key=len, reverse=True))
_NAME_TOKEN = r"[A-Za-z][A-Za-z0-9._\-]*"
# Provider phrase: a name token plus any trailing alphanumeric / dotted
# / hyphenated tokens before the next keyword. Captures "sonnet 4.6"
# as a whole even though only "sonnet" resolves to a provider.
_PROVIDER_PHRASE = rf"(?P<provider>{_NAME_TOKEN}(?:[\s.-][A-Za-z0-9._\-]+)*)"

# "route coding to sonnet 4.6 (in this project)"
_ROUTE_TO = re.compile(
    rf"\broute\s+(?P<tag>{_TAG_ALTERNATION})\s+(?:tasks?\s+)?(?:to|through|via)\s+{_PROVIDER_PHRASE}",
    re.IGNORECASE,
)
# "use sonnet for coding (in this project)"
_USE_FOR = re.compile(
    rf"\buse\s+{_PROVIDER_PHRASE}\s+for\s+(?P<tag>{_TAG_ALTERNATION})\b",
    re.IGNORECASE,
)
# "stop routing coding (to <anything>)" / "clear coding override"
_STOP_ROUTING = re.compile(
    rf"\b(?:stop\s+routing|clear\s+(?:the\s+)?override\s+for|clear)\s+(?P<tag>{_TAG_ALTERNATION})\b",
    re.IGNORECASE,
)
_LOCAL_ONLY_ON = re.compile(
    r"\bmake\s+(?:this\s+project\s+)?local[\s\-]?only\b",
    re.IGNORECASE,
)
_LOCAL_ONLY_OFF = re.compile(
    r"\b(?:stop\s+being\s+local[\s\-]?only|disable\s+local[\s\-]?only|"
    r"turn\s+off\s+local[\s\-]?only)\b",
    re.IGNORECASE,
)


def parse_provider_alias(raw: str, *, aliases: Mapping[str, str] | None = None) -> str | None:
    """Resolve a user's typed provider/model name to a provider id.

    Aliases are case-insensitive and matched against the longest
    prefix of the input — so ``"sonnet 4.6"`` matches ``"sonnet"``
    and resolves to ``"anthropic"``. Unknown names return ``None``.
    """
    table = aliases if aliases is not None else DEFAULT_PROVIDER_ALIASES
    cleaned = raw.strip().lower()
    if cleaned in table:
        return table[cleaned]
    # Walk the longest registered alias prefix — "sonnet 4.6" → "sonnet".
    for alias, provider_id in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if cleaned.startswith(alias):
            return provider_id
    return None


def find_routing_intent(
    prompt: str,
    *,
    aliases: Mapping[str, str] | None = None,
) -> tuple[str, str, str] | None:
    """Match the prompt against the routing-edit pattern set.

    Returns ``(verb, tag, provider_or_blank)`` for ``set`` / ``clear``
    intents, ``(verb, "", "")`` for the project-level local_only
    intents. ``None`` means no recognized intent — the caller continues
    with the regular reply flow.
    """
    match = _ROUTE_TO.search(prompt) or _USE_FOR.search(prompt)
    if match is not None:
        provider = parse_provider_alias(match.group("provider"), aliases=aliases)
        if provider is None:
            return None
        return ("set", normalize_task_tag(match.group("tag")), provider)

    stop_match = _STOP_ROUTING.search(prompt)
    if stop_match is not None:
        return ("clear", normalize_task_tag(stop_match.group("tag")), "")

    if _LOCAL_ONLY_ON.search(prompt):
        return ("local_only_on", "", "")
    if _LOCAL_ONLY_OFF.search(prompt):
        return ("local_only_off", "", "")
    return None


class RoutingMatcher:
    """``ActionMatcher`` implementation for the routing-edit phrasings.

    Reads ``context["project_id"]`` (the project the user's turn is
    addressed to) and folds it into the match inputs so the executors
    can write through to ``RoutingOverridesStore`` / ``ProjectsStore``
    without re-deriving it.
    """

    def __init__(self, aliases: Mapping[str, str] | None = None) -> None:
        self._aliases = aliases

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any],
    ) -> ActionMatch | None:
        intent = find_routing_intent(prompt, aliases=self._aliases)
        if intent is None:
            return None
        verb, tag, provider = intent
        project_id = context.get("project_id")
        project_input = (
            {"project_id": project_id} if isinstance(project_id, str) and project_id else {}
        )
        if verb == "set":
            return ActionMatch(
                action_name=ROUTING_SET_ACTION,
                inputs={"task_tag": tag, "provider_id": provider, **project_input},
                preview=f"Route ``{tag}`` to ``{provider}`` in this project",
            )
        if verb == "clear":
            return ActionMatch(
                action_name=ROUTING_CLEAR_ACTION,
                inputs={"task_tag": tag, **project_input},
                preview=f"Clear the ``{tag}`` routing override for this project",
            )
        if verb == "local_only_on":
            return ActionMatch(
                action_name=PROJECT_LOCAL_ONLY_ACTION,
                inputs={"value": True, **project_input},
                preview="Make this project local-only",
            )
        if verb == "local_only_off":
            return ActionMatch(
                action_name=PROJECT_LOCAL_ONLY_ACTION,
                inputs={"value": False, **project_input},
                preview="Disable local-only on this project",
            )
        return None


def register_routing_actions(
    registry: ActionRegistry,
    *,
    overrides_store: RoutingOverridesStore,
    projects_store: ProjectsStore,
    valid_provider_ids: Iterable[str] | None = None,
) -> None:
    """Register the routing-edit actions + matcher on ``registry``.

    Caller passes the same stores the GUI's routing-RPC handlers use,
    so the conversational and GUI paths share their write surface
    (per F9.5 — "the same one the GUI calls").
    """

    allowed_providers = frozenset(valid_provider_ids) if valid_provider_ids is not None else None

    async def set_override(inputs: Mapping[str, Any]) -> ActionResult:
        project_id = inputs.get("project_id")
        task_tag = inputs["task_tag"]
        provider_id = inputs["provider_id"]
        if not isinstance(project_id, str) or not project_id:
            return ActionResult(
                confirmation=(
                    "I can change routing once a project is in focus — "
                    "this turn isn't tied to one yet."
                )
            )
        if allowed_providers is not None and provider_id not in allowed_providers:
            return ActionResult(
                confirmation=(
                    f"I don't have a provider that matches that. "
                    f"Try one of: {', '.join(sorted(allowed_providers))}."
                )
            )
        await overrides_store.upsert(
            RoutingOverride(
                routing_override_id=new_routing_override_id(),
                project_id=project_id,
                task_tag=task_tag,
                provider_id=provider_id,
                updated_at_ms=int(time.time() * 1000),
            )
        )
        return ActionResult(
            confirmation=(
                f"Routing updated: ``{task_tag}`` tasks in this project now "
                f"go to ``{provider_id}``."
            )
        )

    async def clear_override(inputs: Mapping[str, Any]) -> ActionResult:
        project_id = inputs.get("project_id")
        task_tag = inputs["task_tag"]
        if not isinstance(project_id, str) or not project_id:
            return ActionResult(
                confirmation=(
                    "I can change routing once a project is in focus — "
                    "this turn isn't tied to one yet."
                )
            )
        cleared = await overrides_store.delete(project_id, task_tag)
        if cleared:
            return ActionResult(
                confirmation=(
                    f"Cleared the ``{task_tag}`` override for this project — "
                    "it routes through the global default again."
                )
            )
        return ActionResult(
            confirmation=(
                f"No override was set for ``{task_tag}`` in this project; nothing to clear."
            )
        )

    async def set_local_only(inputs: Mapping[str, Any]) -> ActionResult:
        project_id = inputs.get("project_id")
        value = bool(inputs["value"])
        if not isinstance(project_id, str) or not project_id:
            return ActionResult(
                confirmation=(
                    "I can flip the local-only flag once a project is in focus — "
                    "this turn isn't tied to one yet."
                )
            )
        project = await projects_store.get(project_id)
        if project is None:
            return ActionResult(
                confirmation="I couldn't find that project to flip its local-only flag.",
            )
        await projects_store.set_local_only(project_id, value)
        if value:
            return ActionResult(
                confirmation=(
                    "This project is now local-only — workers will route to "
                    "local providers, and the spawn path will refuse cloud "
                    "tokens for this project's runs."
                )
            )
        return ActionResult(
            confirmation=(
                "Local-only is off for this project — overrides + the global "
                "default are back in charge of routing."
            )
        )

    registry.register(
        Action(
            name=ROUTING_SET_ACTION,
            description=(
                "Route a task tag to a specific provider in the current project "
                "(e.g. 'route coding to ollama')."
            ),
            inputs=(
                ActionInput(
                    name="task_tag",
                    description="Which task tag to route (coding / research / image / etc.).",
                    kind="task_tag",
                ),
                ActionInput(
                    name="provider_id",
                    description="The provider to route the tag to.",
                    kind="provider_id",
                ),
                ActionInput(
                    name="project_id",
                    description="The project whose routing table to edit.",
                    kind="project_id",
                    required=False,
                ),
            ),
            executor=set_override,
        )
    )
    registry.register(
        Action(
            name=ROUTING_CLEAR_ACTION,
            description=(
                "Clear a routing override so the task tag falls back to the global default."
            ),
            inputs=(
                ActionInput(
                    name="task_tag",
                    description="Which task tag's override to clear.",
                    kind="task_tag",
                ),
                ActionInput(
                    name="project_id",
                    description="The project whose routing table to edit.",
                    kind="project_id",
                    required=False,
                ),
            ),
            executor=clear_override,
        )
    )
    registry.register(
        Action(
            name=PROJECT_LOCAL_ONLY_ACTION,
            description=(
                "Flip a project's local-only flag — when on, workers route to "
                "local providers and cloud tokens are refused for the project."
            ),
            inputs=(
                ActionInput(
                    name="value",
                    description="True to make local-only, False to disable.",
                    kind="bool",
                ),
                ActionInput(
                    name="project_id",
                    description="The project to flip.",
                    kind="project_id",
                    required=False,
                ),
            ),
            executor=set_local_only,
        )
    )
    registry.register_matcher(RoutingMatcher())


__all__ = [
    "DEFAULT_PROVIDER_ALIASES",
    "PROJECT_LOCAL_ONLY_ACTION",
    "ROUTING_CLEAR_ACTION",
    "ROUTING_SET_ACTION",
    "RoutingMatcher",
    "find_routing_intent",
    "parse_provider_alias",
    "register_routing_actions",
]
