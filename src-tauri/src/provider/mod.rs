//! Provider abstraction — Rust side.
//!
//! The actual LLM traffic lives in the brain sidecar (Python). The
//! Rust trait here exists for capability probing, provider listing,
//! and the bookkeeping the desktop core needs to do without crossing
//! the IPC boundary on every call: surface what providers are
//! available, what they can do, and whether they are configured
//! enough to be selected. Mirrors the Python `LlmProvider` Protocol in
//! `brain/thalyn_brain/provider/`.
//!
//! Several helpers (probe, find, ProviderError variants) are exposed
//! as the public surface that subsequent commits in this phase wire
//! into Tauri commands and the keychain adapter; suppress the
//! dead-code lint here so the trait surface can be authored coherently
//! before being fully consumed.

#![allow(dead_code)]

mod capability;
mod registry;

pub use capability::{Capability, CapabilityProfile, ProviderKind, ProviderMeta, ReliabilityTier};
pub use registry::{builtin_providers, ProviderRegistry};

use async_trait::async_trait;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ProviderError {
    #[error("provider {0} is not configured (no API key)")]
    NotConfigured(String),
    #[error("provider {0} probe failed: {1}")]
    Probe(String, String),
    #[error("provider {0} is not implemented yet")]
    NotImplemented(String),
}

/// The Rust-facing provider interface.
///
/// Methods are intentionally narrow — `complete`, `stream`, and `embed`
/// live in the brain because that's where the LLM traffic flows. The
/// Rust core uses this trait to enumerate providers, surface their
/// capability profiles to the UI, and verify reachability before a
/// session starts.
#[async_trait]
pub trait LlmProvider: Send + Sync {
    /// Stable identifier (e.g. `"anthropic"`, `"ollama"`, `"openai_compat"`).
    fn id(&self) -> &str;

    /// Human-readable name for the provider switcher.
    fn display_name(&self) -> &str;

    /// What the provider can do at the chosen default model.
    fn capability_profile(&self) -> &CapabilityProfile;

    /// Convenience helper that maps to the capability profile fields.
    fn supports(&self, capability: Capability) -> bool {
        self.capability_profile().supports(capability)
    }

    /// Lightweight reachability check. Implementations decide what
    /// "ready" means — the Anthropic adapter, for instance, simply
    /// confirms an API key is in the keychain; live API calls happen in
    /// the brain.
    async fn probe(&self) -> Result<(), ProviderError>;
}
