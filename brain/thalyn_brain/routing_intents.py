"""Conversational edit path for the worker-routing surface.

A focused intent parser the brain consults before delegating a turn:
when the user's prompt asks Thalyn to change the project's routing
(``"route coding to Sonnet 4.6 in this project"``), the parser
recognises the intent, dispatches to the routing-actions module, and
the brain replies with a confirmation instead of opening an LLM
turn. Patterns that don't match fall through and the regular reply
flow runs.

This is the *action-registry stub* the build plan calls for: a thin
shim over ``RoutingOverridesStore`` + ``ProjectsStore`` that exposes
the routing-specific actions in a shape the action registry (v0.32)
can absorb without rewriting. The full LLM tool-use path lands when
the registry materialises.

Scope on purpose tight:
- "route <tag> to <name>" / "use <name> for <tag>" — set an override.
- "make this project local-only" / "stop being local-only" — flip the
  project's privacy flag.
- Patterns that don't match return ``None`` so the regular reply
  flow runs unchanged.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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


@dataclass(frozen=True)
class RoutingIntent:
    """Parsed shape of a routing-edit utterance.

    ``confirmation`` is the brain's reply text the caller should
    surface back to the user — short, unambiguous, and explicit
    about what changed.
    """

    action: str
    confirmation: str


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


class RoutingActionsDispatcher:
    """Action-registry stub for routing-specific edits.

    The dispatcher executes the recognised intent against
    ``RoutingOverridesStore`` / ``ProjectsStore`` and returns a
    ``RoutingIntent`` carrying the brain's confirmation reply. When a
    requested provider id isn't installed, the dispatcher returns a
    refusal message rather than writing a dangling override.
    """

    def __init__(
        self,
        *,
        overrides_store: RoutingOverridesStore,
        projects_store: ProjectsStore,
        valid_provider_ids: Iterable[str] | None = None,
    ) -> None:
        self._overrides_store = overrides_store
        self._projects_store = projects_store
        self._allowed_providers = (
            frozenset(valid_provider_ids) if valid_provider_ids is not None else None
        )

    async def dispatch(
        self,
        prompt: str,
        *,
        project_id: str | None,
        aliases: Mapping[str, str] | None = None,
    ) -> RoutingIntent | None:
        intent = find_routing_intent(prompt, aliases=aliases)
        if intent is None:
            return None
        verb, tag, provider = intent

        if verb in {"set", "clear"} and project_id is None:
            return RoutingIntent(
                action=verb,
                confirmation=(
                    "I can change routing once a project is in focus — "
                    "this turn isn't tied to one yet."
                ),
            )

        if verb == "set":
            if self._allowed_providers is not None and provider not in self._allowed_providers:
                return RoutingIntent(
                    action="set",
                    confirmation=(
                        f"I don't have a provider that matches that. "
                        f"Try one of: {', '.join(sorted(self._allowed_providers))}."
                    ),
                )
            assert project_id is not None
            await self._overrides_store.upsert(
                RoutingOverride(
                    routing_override_id=new_routing_override_id(),
                    project_id=project_id,
                    task_tag=tag,
                    provider_id=provider,
                    updated_at_ms=int(time.time() * 1000),
                )
            )
            return RoutingIntent(
                action="set",
                confirmation=(
                    f"Routing updated: ``{tag}`` tasks in this project now go to ``{provider}``."
                ),
            )

        if verb == "clear":
            assert project_id is not None
            cleared = await self._overrides_store.delete(project_id, tag)
            if cleared:
                return RoutingIntent(
                    action="clear",
                    confirmation=(
                        f"Cleared the ``{tag}`` override for this project — "
                        f"it routes through the global default again."
                    ),
                )
            return RoutingIntent(
                action="clear",
                confirmation=(
                    f"No override was set for ``{tag}`` in this project; nothing to clear."
                ),
            )

        if verb in {"local_only_on", "local_only_off"} and project_id is None:
            return RoutingIntent(
                action=verb,
                confirmation=(
                    "I can flip the local-only flag once a project is in focus — "
                    "this turn isn't tied to one yet."
                ),
            )

        if verb == "local_only_on":
            assert project_id is not None
            project = await self._projects_store.get(project_id)
            if project is None:
                return RoutingIntent(
                    action=verb,
                    confirmation="I couldn't find that project to flip its local-only flag.",
                )
            await self._projects_store.set_local_only(project_id, True)
            return RoutingIntent(
                action="local_only_on",
                confirmation=(
                    "This project is now local-only — workers will route to local providers, "
                    "and the spawn path will refuse cloud tokens for this project's runs."
                ),
            )

        if verb == "local_only_off":
            assert project_id is not None
            project = await self._projects_store.get(project_id)
            if project is None:
                return RoutingIntent(
                    action=verb,
                    confirmation="I couldn't find that project to flip its local-only flag.",
                )
            await self._projects_store.set_local_only(project_id, False)
            return RoutingIntent(
                action="local_only_off",
                confirmation=(
                    "Local-only is off for this project — overrides + the global default "
                    "are back in charge of routing."
                ),
            )
        return None


__all__ = [
    "DEFAULT_PROVIDER_ALIASES",
    "RoutingActionsDispatcher",
    "RoutingIntent",
    "find_routing_intent",
    "parse_provider_alias",
]
