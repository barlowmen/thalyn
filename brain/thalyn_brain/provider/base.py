"""Base types for the provider abstraction.

The Protocol mirrors the Rust trait in `src-tauri/src/provider/`. Concrete
implementations live in sibling modules; the dispatcher uses the
Protocol to remain agnostic of the concrete provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable


class ReliabilityTier(StrEnum):
    """Coarse rating for tool-call reliability per provider."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class Capability(StrEnum):
    """Capabilities a provider may declare."""

    TOOL_USE = "tool_use"
    VISION = "vision"
    STREAMING = "streaming"


class ProviderKind(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    LLAMA_CPP = "llama_cpp"
    MLX = "mlx"


@dataclass(frozen=True)
class CapabilityProfile:
    """What the provider can do, against the chosen default model."""

    max_context_tokens: int
    supports_tool_use: bool
    tool_use_reliability: ReliabilityTier
    supports_vision: bool
    supports_streaming: bool
    local: bool

    def supports(self, capability: Capability) -> bool:
        match capability:
            case Capability.TOOL_USE:
                return self.supports_tool_use
            case Capability.VISION:
                return self.supports_vision
            case Capability.STREAMING:
                return self.supports_streaming

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        d["tool_use_reliability"] = self.tool_use_reliability.value
        return _camel_keys(d)


@dataclass(frozen=True)
class ProviderMeta:
    """Wire-friendly metadata for the provider switcher."""

    id: str
    display_name: str
    kind: ProviderKind
    default_model: str
    capability_profile: CapabilityProfile
    configured: bool
    enabled: bool

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "kind": self.kind.value,
            "defaultModel": self.default_model,
            "capabilityProfile": self.capability_profile.to_wire(),
            "configured": self.configured,
            "enabled": self.enabled,
        }


# --- Streamed chat chunk shapes -----------------------------------------------
#
# The wire-level events that a provider produces while answering a query.
# Each chunk maps 1:1 onto a JSON-RPC notification the brain emits.


@dataclass(frozen=True)
class ChatStartChunk:
    kind: Literal["start"] = field(default="start", init=False)
    model: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind, "model": self.model}


@dataclass(frozen=True)
class ChatTextChunk:
    delta: str
    kind: Literal["text"] = field(default="text", init=False)

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind, "delta": self.delta}


@dataclass(frozen=True)
class ChatToolCallChunk:
    call_id: str
    tool: str
    input: dict[str, Any]
    kind: Literal["tool_call"] = field(default="tool_call", init=False)

    def to_wire(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "callId": self.call_id,
            "tool": self.tool,
            "input": self.input,
        }


@dataclass(frozen=True)
class ChatToolResultChunk:
    call_id: str
    output: str
    is_error: bool = False
    kind: Literal["tool_result"] = field(default="tool_result", init=False)

    def to_wire(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "callId": self.call_id,
            "output": self.output,
            "isError": self.is_error,
        }


@dataclass(frozen=True)
class ChatStopChunk:
    reason: str
    total_cost_usd: float | None = None
    kind: Literal["stop"] = field(default="stop", init=False)

    def to_wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "reason": self.reason}
        if self.total_cost_usd is not None:
            d["totalCostUsd"] = self.total_cost_usd
        return d


@dataclass(frozen=True)
class ChatErrorChunk:
    message: str
    code: str | None = None
    kind: Literal["error"] = field(default="error", init=False)

    def to_wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "message": self.message}
        if self.code is not None:
            d["code"] = self.code
        return d


ChatChunk = (
    ChatStartChunk
    | ChatTextChunk
    | ChatToolCallChunk
    | ChatToolResultChunk
    | ChatStopChunk
    | ChatErrorChunk
)


class ProviderError(Exception):
    """Generic failure surfaced to the caller."""


class ProviderNotImplementedError(ProviderError):
    """A v0.3 placeholder provider was selected."""


@runtime_checkable
class LlmProvider(Protocol):
    """Provider interface — the Python equivalent of the Rust trait."""

    @property
    def id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def capability_profile(self) -> CapabilityProfile: ...

    @property
    def default_model(self) -> str: ...

    def supports(self, capability: Capability) -> bool: ...

    def stream_chat(
        self,
        prompt: str,
        *,
        history: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """Stream a single user-turn response.

        Returns an async iterator the caller drives with ``async for``.
        ``history`` (when implemented) is the prior conversation in the
        normalized message shape; v0.3 sessions pin it to a single turn
        at a time. ``system_prompt`` is the system instruction, if any.
        """
        ...


def _camel_keys(d: dict[str, Any]) -> dict[str, Any]:
    """snake_case → camelCase for keys, recursively. Matches the Rust
    serde rename_all = "camelCase" so the wire shape is symmetric."""

    def to_camel(key: str) -> str:
        parts = key.split("_")
        return parts[0] + "".join(part.title() for part in parts[1:])

    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = _camel_keys(v)
        out[to_camel(k)] = v
    return out
