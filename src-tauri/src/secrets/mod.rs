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

    /// Generic secret slot — used for the user-supplied Sentry DSN
    /// and any future opt-in credentials that aren't tied to a
    /// provider. Account naming is namespaced under ``secret.{name}``
    /// so it cannot collide with the per-provider ``{id}.api-key``
    /// slots.
    pub fn save_secret(&self, name: &str, value: &str) -> Result<(), SecretError> {
        self.store.set(SERVICE, &secret_account(name), value)
    }

    pub fn read_secret(&self, name: &str) -> Result<Option<String>, SecretError> {
        match self.store.get(SERVICE, &secret_account(name)) {
            Ok(value) => Ok(Some(value)),
            Err(SecretError::NotFound) => Ok(None),
            Err(err) => Err(err),
        }
    }

    pub fn delete_secret(&self, name: &str) -> Result<(), SecretError> {
        match self.store.delete(SERVICE, &secret_account(name)) {
            Ok(()) | Err(SecretError::NotFound) => Ok(()),
            Err(err) => Err(err),
        }
    }

    pub fn has_secret(&self, name: &str) -> bool {
        self.read_secret(name).unwrap_or(None).is_some()
    }

    pub fn has_api_key(&self, provider_id: &str) -> bool {
        matches!(self.read_api_key(provider_id), Ok(Some(_)))
    }
}

fn api_key_account(provider_id: &str) -> String {
    format!("{provider_id}.api-key")
}

fn secret_account(name: &str) -> String {
    format!("secret.{name}")
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
    fn generic_secrets_round_trip() {
        let manager = manager();
        manager
            .save_secret("sentry_dsn", "https://abc@example/1")
            .unwrap();
        assert_eq!(
            manager.read_secret("sentry_dsn").unwrap().as_deref(),
            Some("https://abc@example/1")
        );
        assert!(manager.has_secret("sentry_dsn"));
        manager.delete_secret("sentry_dsn").unwrap();
        assert!(!manager.has_secret("sentry_dsn"));
    }

    #[test]
    fn generic_secrets_are_namespaced_separately_from_api_keys() {
        let manager = manager();
        manager.save_api_key("anthropic", "sk-test").unwrap();
        manager.save_secret("anthropic", "fake-secret").unwrap();
        // Same name string, different stored value — namespace prefix
        // protects against collision.
        assert_eq!(
            manager.read_api_key("anthropic").unwrap().as_deref(),
            Some("sk-test")
        );
        assert_eq!(
            manager.read_secret("anthropic").unwrap().as_deref(),
            Some("fake-secret")
        );
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
