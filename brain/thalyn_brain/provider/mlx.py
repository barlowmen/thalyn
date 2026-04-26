"""MLX provider — Apple Silicon local-model streaming.

MLX runs Apple's metal-shader inference path; the Python entry
point is the ``mlx-lm`` package (``pip install mlx-lm``). The
package is Apple-Silicon-only so we keep the import lazy: an
adapter constructed on Linux still loads, just yields a clear
error chunk on first use rather than refusing to import.

The `LlmProvider` Protocol's tool-call surface is a no-op here.
MLX's streaming path produces text tokens; tool-use orchestration
in v0.10 stays on Anthropic / Ollama. When MLX models gain
native tool-calling support, the spot to plug it in is
``_translate_text``.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from thalyn_brain.provider.base import (
    Capability,
    CapabilityProfile,
    ChatChunk,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ReliabilityTier,
)

DEFAULT_MODEL = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
DEFAULT_CONTEXT_TOKENS = 32_768
DEFAULT_MAX_GEN_TOKENS = 1024


# A streaming generator that, given a prompt, yields token text.
StreamFn = Callable[[str], Awaitable[AsyncIterator[str]]]


class MlxProvider:
    """Concrete ``LlmProvider`` for Apple Silicon MLX models."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        max_gen_tokens: int = DEFAULT_MAX_GEN_TOKENS,
        stream_fn: StreamFn | None = None,
    ) -> None:
        self._model = model
        self._max_gen_tokens = max_gen_tokens
        self._stream_fn = stream_fn

    @property
    def id(self) -> str:
        return "mlx"

    @property
    def display_name(self) -> str:
        return "MLX (Apple Silicon)"

    @property
    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            max_context_tokens=DEFAULT_CONTEXT_TOKENS,
            supports_tool_use=False,
            tool_use_reliability=ReliabilityTier.LOW,
            supports_vision=False,
            supports_streaming=True,
            local=True,
        )

    @property
    def default_model(self) -> str:
        return self._model

    def supports(self, capability: Capability) -> bool:
        return self.capability_profile.supports(capability)

    async def stream_chat(
        self,
        prompt: str,
        *,
        history: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        full_prompt = _build_prompt(prompt, history=history, system_prompt=system_prompt)

        try:
            stream = await (self._stream_fn or self._default_stream_fn())(full_prompt)
        except MlxUnavailableError as exc:
            yield ChatErrorChunk(message=str(exc), code="mlx_unavailable")
            return
        except Exception as exc:
            yield ChatErrorChunk(message=f"mlx error: {exc}", code="mlx_error")
            return

        yield ChatStartChunk(model=self._model)
        try:
            async for token in stream:
                if token:
                    yield ChatTextChunk(delta=token)
        except Exception as exc:
            yield ChatErrorChunk(message=f"mlx stream error: {exc}", code="mlx_stream_error")
            return
        yield ChatStopChunk(reason="end_of_stream")

    def _default_stream_fn(self) -> StreamFn:
        max_gen_tokens = self._max_gen_tokens
        model_id = self._model

        async def stream(full_prompt: str) -> AsyncIterator[str]:
            try:
                mlx_lm = importlib.import_module("mlx_lm")
            except ImportError as exc:
                raise MlxUnavailableError(
                    "mlx-lm not available. MLX runs on Apple Silicon; "
                    "install `mlx-lm` and ensure you're on macOS arm64."
                ) from exc
            return _drive_mlx_stream(mlx_lm, model_id, full_prompt, max_gen_tokens)

        return stream


class MlxUnavailableError(RuntimeError):
    """The ``mlx-lm`` package is not installed or the runtime is
    not Apple Silicon."""


async def _drive_mlx_stream(
    mlx_lm: Any,
    model_id: str,
    prompt: str,
    max_gen_tokens: int,
) -> AsyncIterator[str]:
    """Drive ``mlx_lm.stream_generate`` and yield each token's text.

    ``mlx-lm`` returns a synchronous generator of objects with a
    ``.text`` attribute; we adapt to async by yielding through the
    event loop. The model + tokenizer load runs in a thread so the
    main loop stays responsive while MLX warms up.
    """
    import asyncio

    def load() -> tuple[Any, Any]:
        return mlx_lm.load(model_id)  # type: ignore[no-any-return]

    model, tokenizer = await asyncio.to_thread(load)

    def step_iterator() -> Any:
        return mlx_lm.stream_generate(
            model,
            tokenizer,
            prompt,
            max_tokens=max_gen_tokens,
        )

    iterator = await asyncio.to_thread(step_iterator)
    while True:
        item = await asyncio.to_thread(_next_or_none, iterator)
        if item is None:
            return
        text = getattr(item, "text", None)
        if isinstance(text, str):
            yield text


def _next_or_none(iterator: Any) -> Any:
    try:
        return next(iterator)
    except StopIteration:
        return None


def _build_prompt(
    user_message: str,
    *,
    history: list[dict[str, Any]] | None,
    system_prompt: str | None,
) -> str:
    """Flatten a system + history + user turn into a single chat-template string.

    MLX models speak whatever chat template their tokenizer ships;
    in 2026 most fine-tunes use the OpenAI-style ``<role>: content``
    layout. Without access to the tokenizer here we use a plain
    layout and rely on the model's instruction-tuning to interpret
    the structure. Fancier templating lands when the brain gains
    a per-model template registry.
    """
    parts: list[str] = []
    if system_prompt:
        parts.append(f"system: {system_prompt}")
    if history:
        for entry in history:
            role = entry.get("role")
            content = entry.get("content")
            if isinstance(role, str) and isinstance(content, str):
                parts.append(f"{role}: {content}")
    parts.append(f"user: {user_message}")
    parts.append("assistant:")
    return "\n".join(parts)


__all__ = [
    "DEFAULT_CONTEXT_TOKENS",
    "DEFAULT_MAX_GEN_TOKENS",
    "DEFAULT_MODEL",
    "MlxProvider",
    "MlxUnavailableError",
]
