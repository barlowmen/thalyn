//! Secret-store abstraction.
//!
//! The OS keychain is the production backend. An in-memory store is
//! provided for tests and as a fallback when the keychain is
//! unavailable (CI / non-graphical / containerised environments).

use std::collections::HashMap;
use std::sync::Mutex;

use keyring::Entry;

use super::SecretError;

/// A pluggable secret backend. `service` and `account` together
/// identify a single secret entry; the same pair always addresses the
/// same secret across calls.
pub trait SecretStore: Send + Sync {
    fn set(&self, service: &str, account: &str, value: &str) -> Result<(), SecretError>;
    fn get(&self, service: &str, account: &str) -> Result<String, SecretError>;
    fn delete(&self, service: &str, account: &str) -> Result<(), SecretError>;
}

/// Keychain-backed store. Uses the OS-native credential vault on each
/// platform via the `keyring` crate.
#[derive(Debug)]
pub struct KeyringSecretStore;

impl KeyringSecretStore {
    pub fn new() -> Result<Self, SecretError> {
        // Probe the platform store with a benign read against an
        // intentionally-missing entry; this surfaces "no DBus / no
        // Keychain access" failures up front rather than at the first
        // user save.
        match Entry::new("app.thalyn", "__probe__")
            .map_err(|err| SecretError::Unavailable(err.to_string()))?
            .get_password()
        {
            Ok(_) => Ok(Self),
            Err(keyring::Error::NoEntry) => Ok(Self),
            Err(err) => Err(SecretError::Unavailable(err.to_string())),
        }
    }
}

impl SecretStore for KeyringSecretStore {
    fn set(&self, service: &str, account: &str, value: &str) -> Result<(), SecretError> {
        let entry =
            Entry::new(service, account).map_err(|err| SecretError::Other(err.to_string()))?;
        entry
            .set_password(value)
            .map_err(|err| SecretError::Other(err.to_string()))
    }

    fn get(&self, service: &str, account: &str) -> Result<String, SecretError> {
        let entry =
            Entry::new(service, account).map_err(|err| SecretError::Other(err.to_string()))?;
        match entry.get_password() {
            Ok(value) => Ok(value),
            Err(keyring::Error::NoEntry) => Err(SecretError::NotFound),
            Err(err) => Err(SecretError::Other(err.to_string())),
        }
    }

    fn delete(&self, service: &str, account: &str) -> Result<(), SecretError> {
        let entry =
            Entry::new(service, account).map_err(|err| SecretError::Other(err.to_string()))?;
        match entry.delete_credential() {
            Ok(()) => Ok(()),
            Err(keyring::Error::NoEntry) => Err(SecretError::NotFound),
            Err(err) => Err(SecretError::Other(err.to_string())),
        }
    }
}

/// In-memory store. Test-only — its contents disappear when the
/// process exits. Safe to share across threads via the inner mutex.
#[derive(Debug, Default)]
pub struct InMemorySecretStore {
    inner: Mutex<HashMap<(String, String), String>>,
}

impl SecretStore for InMemorySecretStore {
    fn set(&self, service: &str, account: &str, value: &str) -> Result<(), SecretError> {
        let mut guard = self.inner.lock().expect("secret store mutex");
        guard.insert(
            (service.to_string(), account.to_string()),
            value.to_string(),
        );
        Ok(())
    }

    fn get(&self, service: &str, account: &str) -> Result<String, SecretError> {
        let guard = self.inner.lock().expect("secret store mutex");
        guard
            .get(&(service.to_string(), account.to_string()))
            .cloned()
            .ok_or(SecretError::NotFound)
    }

    fn delete(&self, service: &str, account: &str) -> Result<(), SecretError> {
        let mut guard = self.inner.lock().expect("secret store mutex");
        guard
            .remove(&(service.to_string(), account.to_string()))
            .map(|_| ())
            .ok_or(SecretError::NotFound)
    }
}
