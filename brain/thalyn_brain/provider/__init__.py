"""Provider abstraction for the brain sidecar.

Mirrors the Rust trait in `src-tauri/src/provider/`. The Rust side
owns provider listing for the UI; the Python side owns the
LLM-traffic implementations.
"""

from thalyn_brain.provider.anthropic import AnthropicProvider
from thalyn_brain.provider.base import (
    Capability,
    CapabilityChange,
    CapabilityDelta,
    CapabilityProfile,
    ChatChunk,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
    LlmProvider,
    ProviderError,
    ProviderKind,
    ProviderMeta,
    ProviderNotImplementedError,
    ReliabilityTier,
    compare_profiles,
)
from thalyn_brain.provider.registry import (
    ProviderRegistry,
    build_registry,
    builtin_providers,
)

__all__ = [
    "AnthropicProvider",
    "Capability",
    "CapabilityChange",
    "CapabilityDelta",
    "CapabilityProfile",
    "ChatChunk",
    "ChatErrorChunk",
    "ChatStartChunk",
    "ChatStopChunk",
    "ChatTextChunk",
    "ChatToolCallChunk",
    "ChatToolResultChunk",
    "LlmProvider",
    "ProviderError",
    "ProviderKind",
    "ProviderMeta",
    "ProviderNotImplementedError",
    "ProviderRegistry",
    "ReliabilityTier",
    "build_registry",
    "builtin_providers",
    "compare_profiles",
]
