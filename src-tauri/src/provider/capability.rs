//! Capability profiles + provider metadata.
//!
//! The schema mirrors the Python side and is what powers the
//! "capability delta" warning the UI surfaces when a user swaps
//! providers (`01-requirements.md` F3.4).

use serde::{Deserialize, Serialize};

/// What the provider can do, against the model the user has selected.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CapabilityProfile {
    /// Maximum input + output context length (tokens).
    pub max_context_tokens: u32,
    /// Whether the provider exposes structured tool/function calling.
    pub supports_tool_use: bool,
    /// Coarse reliability rating for tool calls. Surfaces in the UI
    /// when the user swaps to a less-reliable provider.
    pub tool_use_reliability: ReliabilityTier,
    /// Whether the model accepts image inputs.
    pub supports_vision: bool,
    /// Whether responses can be streamed token-by-token.
    pub supports_streaming: bool,
    /// True if inference happens on the user's machine.
    pub local: bool,
}

impl CapabilityProfile {
    pub fn supports(&self, capability: Capability) -> bool {
        match capability {
            Capability::ToolUse => self.supports_tool_use,
            Capability::Vision => self.supports_vision,
            Capability::Streaming => self.supports_streaming,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ReliabilityTier {
    High,
    Medium,
    Low,
    /// Used for placeholder / not-yet-supported providers.
    Unknown,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    ToolUse,
    Vision,
    Streaming,
}

/// Coarse provider classification — drives icon + grouping in the
/// switcher and tells the brain how to construct the underlying
/// session.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProviderKind {
    Anthropic,
    OpenAiCompatible,
    Ollama,
    LlamaCpp,
    Mlx,
}

/// Pre-aggregated metadata for the provider switcher in the UI.
/// Carries everything the renderer needs without crossing the IPC
/// boundary on every render.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderMeta {
    pub id: String,
    pub display_name: String,
    pub kind: ProviderKind,
    /// The default model id this profile applies to.
    pub default_model: String,
    pub capability_profile: CapabilityProfile,
    /// True once the provider is set up enough to be selected (e.g.
    /// the API key is stored in the keychain).
    pub configured: bool,
    /// True for providers we've shipped only as placeholders. The UI
    /// disables the row and explains why.
    pub enabled: bool,
}
