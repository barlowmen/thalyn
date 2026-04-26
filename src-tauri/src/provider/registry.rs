//! Built-in provider registry.
//!
//! Ships with a static list of provider metadata. v0.3 enables the
//! Anthropic provider; the OpenAI-compatible / Ollama / llama.cpp /
//! MLX entries surface in the UI as disabled placeholders so users
//! can see what's coming without being able to select them yet.

use std::sync::Arc;

use async_trait::async_trait;

use super::{
    CapabilityProfile, LlmProvider, ProviderError, ProviderKind, ProviderMeta, ReliabilityTier,
};

/// Static metadata for the Claude Sonnet 4.6 default. The capability
/// numbers track public Anthropic documentation as of the v0.3
/// release; they're inputs to the UI delta surface, not API calls.
pub const ANTHROPIC_DEFAULT_MODEL: &str = "claude-sonnet-4-6";

pub struct AnthropicProvider {
    profile: CapabilityProfile,
    configured: bool,
}

impl AnthropicProvider {
    pub fn new(configured: bool) -> Self {
        Self {
            profile: CapabilityProfile {
                max_context_tokens: 200_000,
                supports_tool_use: true,
                tool_use_reliability: ReliabilityTier::High,
                supports_vision: true,
                supports_streaming: true,
                local: false,
            },
            configured,
        }
    }
}

#[async_trait]
impl LlmProvider for AnthropicProvider {
    fn id(&self) -> &str {
        "anthropic"
    }
    fn display_name(&self) -> &str {
        "Anthropic — Claude Sonnet 4.6"
    }
    fn capability_profile(&self) -> &CapabilityProfile {
        &self.profile
    }
    async fn probe(&self) -> Result<(), ProviderError> {
        if self.configured {
            Ok(())
        } else {
            Err(ProviderError::NotConfigured("anthropic".into()))
        }
    }
}

/// A v0.3 placeholder — the UI lists these so the user can see what
/// providers are coming, but selecting them is disabled. The probe
/// always returns `NotImplemented`.
struct PlaceholderProvider {
    id: &'static str,
    display_name: &'static str,
    profile: CapabilityProfile,
}

#[async_trait]
impl LlmProvider for PlaceholderProvider {
    fn id(&self) -> &str {
        self.id
    }
    fn display_name(&self) -> &str {
        self.display_name
    }
    fn capability_profile(&self) -> &CapabilityProfile {
        &self.profile
    }
    async fn probe(&self) -> Result<(), ProviderError> {
        Err(ProviderError::NotImplemented(self.id.to_string()))
    }
}

fn placeholder_profile(reliability: ReliabilityTier, local: bool) -> CapabilityProfile {
    CapabilityProfile {
        max_context_tokens: 0,
        supports_tool_use: false,
        tool_use_reliability: reliability,
        supports_vision: false,
        supports_streaming: false,
        local,
    }
}

/// Build the v0.3 provider list. The configured flag for Anthropic is
/// supplied by the caller so the registry stays decoupled from the
/// keychain.
pub fn builtin_providers(anthropic_configured: bool) -> Vec<Arc<dyn LlmProvider>> {
    vec![
        Arc::new(AnthropicProvider::new(anthropic_configured)),
        Arc::new(PlaceholderProvider {
            id: "openai_compat",
            display_name: "OpenAI-compatible endpoint",
            profile: placeholder_profile(ReliabilityTier::Unknown, false),
        }),
        Arc::new(PlaceholderProvider {
            id: "ollama",
            display_name: "Ollama (local)",
            profile: placeholder_profile(ReliabilityTier::Unknown, true),
        }),
        Arc::new(PlaceholderProvider {
            id: "llama_cpp",
            display_name: "llama.cpp (local)",
            profile: placeholder_profile(ReliabilityTier::Unknown, true),
        }),
        Arc::new(PlaceholderProvider {
            id: "mlx",
            display_name: "MLX (Apple Silicon)",
            profile: placeholder_profile(ReliabilityTier::Unknown, true),
        }),
    ]
}

/// Top-level registry the rest of the runtime addresses. Holds the
/// `Arc`-shared providers and exposes them as wire-friendly metadata
/// for the renderer.
pub struct ProviderRegistry {
    providers: Vec<Arc<dyn LlmProvider>>,
}

impl ProviderRegistry {
    pub fn new(providers: Vec<Arc<dyn LlmProvider>>) -> Self {
        Self { providers }
    }

    pub fn list_meta(&self) -> Vec<ProviderMeta> {
        self.providers
            .iter()
            .map(|provider| {
                let id = provider.id();
                let kind = match id {
                    "anthropic" => ProviderKind::Anthropic,
                    "openai_compat" => ProviderKind::OpenAiCompatible,
                    "ollama" => ProviderKind::Ollama,
                    "llama_cpp" => ProviderKind::LlamaCpp,
                    "mlx" => ProviderKind::Mlx,
                    _ => ProviderKind::OpenAiCompatible,
                };
                let default_model = match kind {
                    ProviderKind::Anthropic => ANTHROPIC_DEFAULT_MODEL.to_string(),
                    _ => String::new(),
                };
                let configured = matches!(kind, ProviderKind::Anthropic)
                    && provider.capability_profile().max_context_tokens > 0;
                let enabled = matches!(kind, ProviderKind::Anthropic);
                ProviderMeta {
                    id: id.to_string(),
                    display_name: provider.display_name().to_string(),
                    kind,
                    default_model,
                    capability_profile: provider.capability_profile().clone(),
                    configured,
                    enabled,
                }
            })
            .collect()
    }

    pub fn find(&self, id: &str) -> Option<Arc<dyn LlmProvider>> {
        self.providers.iter().find(|p| p.id() == id).map(Arc::clone)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::provider::Capability;

    #[tokio::test]
    async fn anthropic_probe_requires_configuration() {
        let provider = AnthropicProvider::new(false);
        assert!(matches!(
            provider.probe().await,
            Err(ProviderError::NotConfigured(_))
        ));
        let provider = AnthropicProvider::new(true);
        assert!(provider.probe().await.is_ok());
    }

    #[test]
    fn anthropic_supports_streaming_and_tools() {
        let provider = AnthropicProvider::new(true);
        assert!(provider.supports(Capability::Streaming));
        assert!(provider.supports(Capability::ToolUse));
        assert!(provider.supports(Capability::Vision));
    }

    #[tokio::test]
    async fn placeholders_are_not_implemented() {
        let registry = ProviderRegistry::new(builtin_providers(false));
        let ollama = registry.find("ollama").expect("ollama present");
        assert!(matches!(
            ollama.probe().await,
            Err(ProviderError::NotImplemented(_))
        ));
    }

    #[test]
    fn registry_marks_anthropic_enabled_others_disabled() {
        let registry = ProviderRegistry::new(builtin_providers(true));
        let metas = registry.list_meta();
        let anthropic = metas
            .iter()
            .find(|m| m.id == "anthropic")
            .expect("anthropic present");
        assert!(anthropic.enabled);
        assert!(anthropic.configured);
        for other in metas.iter().filter(|m| m.id != "anthropic") {
            assert!(!other.enabled, "{} should be disabled in v0.3", other.id);
        }
    }
}
