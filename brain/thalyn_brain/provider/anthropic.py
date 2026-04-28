"""Anthropic provider — wraps the Claude Agent SDK.

The provider composes one of two auth backends per ADR-0020:
``ClaudeSubscriptionAuth`` (the bundled CLI's stored OAuth token —
default) or ``AnthropicApiAuth`` (an Anthropic API key resolved from
env / keychain). The selection is per-instance and resolved at call
time by ``_build_options`` so a hot-rotated key becomes visible
without restarting the brain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from thalyn_brain.provider.auth import AuthBackend
from thalyn_brain.provider.auth_anthropic import AnthropicApiAuth
from thalyn_brain.provider.base import (
    Capability,
    CapabilityProfile,
    ChatChunk,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
    ProviderError,
    ReliabilityTier,
)

DEFAULT_MODEL = "claude-sonnet-4-6"

_PROFILE = CapabilityProfile(
    max_context_tokens=200_000,
    supports_tool_use=True,
    tool_use_reliability=ReliabilityTier.HIGH,
    supports_vision=True,
    supports_streaming=True,
    local=False,
)


# Indirection so tests can swap in a fake client without monkey-patching
# the SDK module. The factory returns an async-context-manager-shaped
# object (the SDK's ClaudeSDKClient already qualifies).
SdkClientFactory = Callable[[ClaudeAgentOptions], Any]


def _default_factory(options: ClaudeAgentOptions) -> ClaudeSDKClient:
    return ClaudeSDKClient(options=options)


class AnthropicProvider:
    """Provider that streams responses through the Claude Agent SDK.

    Composes an ``AuthBackend`` so the same provider class works with
    either the user's Claude subscription (the bundled CLI's stored
    OAuth token) or a pasted Anthropic API key. ``token()`` returning
    ``None`` is the explicit signal to leave ``ANTHROPIC_API_KEY``
    unset and let the CLI's own auth state apply (per ADR-0020).
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        client_factory: SdkClientFactory | None = None,
        auth_backend: AuthBackend | None = None,
    ) -> None:
        self._model = model
        self._client_factory = client_factory or _default_factory
        # Default to the v1-compatible API-key path: read
        # ANTHROPIC_API_KEY from the spawn env. Wiring the
        # subscription-default selection in the registry / RPC layer
        # is the next step in the auth split.
        self._auth_backend = auth_backend or AnthropicApiAuth()

    # --- LlmProvider Protocol surface -----------------------------------

    @property
    def id(self) -> str:
        return "anthropic"

    @property
    def display_name(self) -> str:
        return "Anthropic — Claude Sonnet 4.6"

    @property
    def capability_profile(self) -> CapabilityProfile:
        return _PROFILE

    @property
    def default_model(self) -> str:
        return self._model

    @property
    def auth_backend(self) -> AuthBackend:
        return self._auth_backend

    def set_auth_backend(self, backend: AuthBackend) -> None:
        """Hot-swap the auth backend. The next ``stream_chat`` call sees
        the new credential resolution; in-flight calls keep their
        original backend (the closure captures the instance, not the
        attribute)."""
        self._auth_backend = backend

    def supports(self, capability: Capability) -> bool:
        return _PROFILE.supports(capability)

    def stream_chat(
        self,
        prompt: str,
        *,
        history: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        # v0.3 is single-turn: history is accepted but not yet replayed
        # — the SDK session itself maintains turn state. A future commit
        # threads multi-turn history through.
        del history
        return _stream(
            self._client_factory,
            self._auth_backend,
            self._model,
            prompt,
            system_prompt,
        )

    async def _build_options(self, system_prompt: str | None) -> ClaudeAgentOptions:
        env: dict[str, str] = {"ANTHROPIC_MODEL": self._model}
        token = await self._auth_backend.token()
        if token is not None:
            env["ANTHROPIC_API_KEY"] = token
        if system_prompt is None:
            return ClaudeAgentOptions(env=env)
        return ClaudeAgentOptions(env=env, system_prompt=system_prompt)


async def _stream(
    factory: SdkClientFactory,
    auth_backend: AuthBackend,
    model: str,
    prompt: str,
    system_prompt: str | None,
) -> AsyncIterator[ChatChunk]:
    """Drive the SDK and yield normalized chunks.

    Builds the SDK options here so the auth backend's ``token()`` is
    consulted on every call. A subscription backend returns ``None``
    and we leave ``ANTHROPIC_API_KEY`` unset; an API-key backend
    returns the current key and we inject it.
    """
    from thalyn_brain.tracing import annotate_llm_response, llm_call_span

    yield ChatStartChunk(model=model)
    with llm_call_span(provider_id="anthropic", model=model) as span:
        try:
            env: dict[str, str] = {"ANTHROPIC_MODEL": model}
            token = await auth_backend.token()
            if token is not None:
                env["ANTHROPIC_API_KEY"] = token
            options = (
                ClaudeAgentOptions(env=env)
                if system_prompt is None
                else ClaudeAgentOptions(env=env, system_prompt=system_prompt)
            )
            async with cast(Any, factory(options)) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    for chunk in _normalize_message(message):
                        yield chunk
                    if isinstance(message, ResultMessage):
                        annotate_llm_response(
                            span,
                            finish_reason="end_turn",
                            response_model=model,
                        )
                        yield ChatStopChunk(
                            reason="end_turn",
                            total_cost_usd=getattr(message, "total_cost_usd", None),
                        )
                        return
        except ProviderError as exc:
            annotate_llm_response(span, finish_reason="error")
            yield ChatErrorChunk(message=str(exc))
        except Exception as exc:
            # SDK failures arrive as a wide variety of shapes; we route the
            # message and class name to the renderer so the user sees
            # something actionable rather than letting the error escape the
            # async generator.
            annotate_llm_response(span, finish_reason="error")
            yield ChatErrorChunk(message=str(exc), code=type(exc).__name__)


def _normalize_message(message: object) -> list[ChatChunk]:
    """Translate one Claude SDK message into wire chunks."""
    chunks: list[ChatChunk] = []

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                chunks.append(ChatTextChunk(delta=block.text))
            elif isinstance(block, ToolUseBlock):
                chunks.append(
                    ChatToolCallChunk(
                        call_id=block.id,
                        tool=block.name,
                        input=dict(block.input or {}),
                    )
                )
    elif isinstance(message, UserMessage):
        # User messages reaching the receive loop carry tool results; the
        # user's own prompt comes from our query() side and isn't echoed
        # back as input here. The SDK can hand us either a list of
        # blocks or a bare string for the simple-prompt path; the
        # latter has nothing to translate.
        if isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    output = _stringify_tool_output(block.content)
                    chunks.append(
                        ChatToolResultChunk(
                            call_id=block.tool_use_id,
                            output=output,
                            is_error=bool(getattr(block, "is_error", False)),
                        )
                    )
    elif isinstance(message, SystemMessage):
        # System messages are SDK-internal noise we currently ignore.
        pass
    elif isinstance(message, ResultMessage):
        # The caller handles ResultMessage to emit ChatStopChunk and end
        # the iteration; nothing to translate here.
        pass

    return chunks


def _stringify_tool_output(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # The SDK sometimes hands us a list of content blocks; flatten them
    # to plain text so the chat surface can render the result inline.
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, dict) and "text" in entry:
                parts.append(str(entry["text"]))
            elif isinstance(entry, TextBlock):
                parts.append(entry.text)
            else:
                parts.append(str(entry))
        return "\n".join(parts)
    return str(content)
