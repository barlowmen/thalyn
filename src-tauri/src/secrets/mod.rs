//! Secrets at rest — OS keychain (`keyring` crate) on production
//! builds; an in-memory store in tests.
//!
//! Per `01-requirements.md` F7.7 we never put secrets in environment
//! variables on disk and never write them to a config file. The
//! keychain is the only place a long-lived API key is durable. When
//! the brain sidecar needs the key, the desktop core reads it from
//! the keychain and forwards it through the spawn env (in-memory,
//! to a child process) — that's an operational hygiene practice, not
//! adversarial defense.

mod store;

use thiserror::Error;
use tracing::warn;

pub use store::{InMemorySecretStore, KeyringSecretStore, SecretStore};

const SERVICE: &str = "app.thalyn";

#[derive(Debug, Error)]
pub enum SecretError {
    #[error("secret store unavailable: {0}")]
    Unavailable(String),
    #[error("secret not found")]
    NotFound,
    #[error("secret store error: {0}")]
    Other(String),
}

/// High-level secrets manager. Routes per-provider keys through a
/// pluggable [`SecretStore`].
pub struct SecretsManager {
    store: Box<dyn SecretStore>,
}

impl SecretsManager {
    pub fn new(store: Box<dyn SecretStore>) -> Self {
        Self { store }
    }

    /// Production constructor — the OS keychain on every supported
    /// platform. Falls back to an in-memory store with a warning when
    /// the keychain is unavailable so the rest of the app keeps
    /// working in CI / sandboxed environments.
    pub fn with_default_store() -> Self {
        let store: Box<dyn SecretStore> = match KeyringSecretStore::new() {
            Ok(store) => Box::new(store),
            Err(err) => {
                warn!(
                    ?err,
                    "OS keychain unavailable; using in-memory secret store"
                );
                Box::new(InMemorySecretStore::default())
            }
        };
        Self::new(store)
    }

    pub fn save_api_key(&self, provider_id: &str, value: &str) -> Result<(), SecretError> {
        self.store
            .set(SERVICE, &api_key_account(provider_id), value)
    }

    pub fn delete_api_key(&self, provider_id: &str) -> Result<(), SecretError> {
        match self.store.delete(SERVICE, &api_key_account(provider_id)) {
            Ok(()) | Err(SecretError::NotFound) => Ok(()),
            Err(err) => Err(err),
        }
    }

    pub fn read_api_key(&self, provider_id: &str) -> Result<Option<String>, SecretError> {
        match self.store.get(SERVICE, &api_key_account(provider_id)) {
            Ok(value) => Ok(Some(value)),
            Err(SecretError::NotFound) => Ok(None),
            Err(err) => Err(err),
        }
    }

    pub fn has_api_key(&self, provider_id: &str) -> bool {
        matches!(self.read_api_key(provider_id), Ok(Some(_)))
    }
}

fn api_key_account(provider_id: &str) -> String {
    format!("{provider_id}.api-key")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manager() -> SecretsManager {
        SecretsManager::new(Box::new(InMemorySecretStore::default()))
    }

    #[test]
    fn round_trip_persists_and_retrieves_a_key() {
        let manager = manager();
        manager.save_api_key("anthropic", "sk-test-1234").unwrap();
        assert_eq!(
            manager.read_api_key("anthropic").unwrap().as_deref(),
            Some("sk-test-1234")
        );
        assert!(manager.has_api_key("anthropic"));
    }

    #[test]
    fn delete_clears_the_key_and_subsequent_read_returns_none() {
        let manager = manager();
        manager.save_api_key("anthropic", "sk-test").unwrap();
        manager.delete_api_key("anthropic").unwrap();
        assert!(manager.read_api_key("anthropic").unwrap().is_none());
        assert!(!manager.has_api_key("anthropic"));
    }

    #[test]
    fn delete_of_missing_key_succeeds_silently() {
        let manager = manager();
        // No save first; delete should still succeed (idempotent).
        assert!(manager.delete_api_key("anthropic").is_ok());
    }

    #[test]
    fn keys_are_namespaced_per_provider() {
        let manager = manager();
        manager.save_api_key("anthropic", "anthropic-key").unwrap();
        manager.save_api_key("openai_compat", "openai-key").unwrap();
        assert_eq!(
            manager.read_api_key("anthropic").unwrap().as_deref(),
            Some("anthropic-key")
        );
        assert_eq!(
            manager.read_api_key("openai_compat").unwrap().as_deref(),
            Some("openai-key")
        );
    }
}
