//! Per-Thalyn Chromium profile.
//!
//! The CEF Browser host writes its cookie store, login state, form
//! history, cache, and the `DevToolsActivePort` file under one
//! profile directory. Keeping that directory under the canonical
//! Thalyn data dir (per ADR-0028) means user state survives app
//! upgrades and the user's main browser profile is left untouched.
//!
//! The CefSdk's binaries live elsewhere — this module is purely
//! about *user* state, not engine assets.

use std::fs;
use std::path::{Path, PathBuf};

use thiserror::Error;

const PROFILE_DIR_NAME: &str = "chromium-profile";
const PORT_FILE_NAME: &str = "DevToolsActivePort";

#[derive(Debug, Error)]
pub enum ProfileError {
    #[error("could not create CEF profile directory at {path}: {source}")]
    Create {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("could not clear stale port file at {path}: {source}")]
    ClearPortFile {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
}

/// Owner of the per-Thalyn Chromium profile directory.
#[derive(Debug, Clone)]
pub struct CefProfile {
    profile_dir: PathBuf,
}

impl CefProfile {
    /// Open (creating if necessary) the profile directory under
    /// `root`. The actual layout is `root/chromium-profile/`; the
    /// nested `chromium-profile` segment exists so the same root can
    /// host other CEF state side-by-side later (per-Thalyn helper
    /// caches, future per-project profiles).
    pub fn open(root: &Path) -> Result<Self, ProfileError> {
        let profile_dir = root.join(PROFILE_DIR_NAME);
        fs::create_dir_all(&profile_dir).map_err(|source| ProfileError::Create {
            path: profile_dir.clone(),
            source,
        })?;
        Ok(Self { profile_dir })
    }

    pub fn dir(&self) -> &Path {
        &self.profile_dir
    }

    /// Path of the `DevToolsActivePort` file CEF writes once the
    /// remote-debugging socket is ready.
    pub fn port_file(&self) -> PathBuf {
        self.profile_dir.join(PORT_FILE_NAME)
    }

    /// Best-effort: remove a stale port file from a prior session
    /// before we start CEF, so the watcher does not latch onto last
    /// run's data. Missing file is not an error; only a present-and-
    /// unlinkable file is.
    pub fn clear_stale_port_file(&self) -> Result<(), ProfileError> {
        let path = self.port_file();
        match fs::remove_file(&path) {
            Ok(()) => Ok(()),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(source) => Err(ProfileError::ClearPortFile { path, source }),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-cef-profile-test-{}-{}",
            label,
            std::process::id()
        ));
        if dir.exists() {
            fs::remove_dir_all(&dir).unwrap();
        }
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn open_creates_the_profile_directory() {
        let root = temp_root("create");
        let profile = CefProfile::open(&root).expect("open");
        assert!(profile.dir().is_dir());
        assert_eq!(profile.dir(), root.join(PROFILE_DIR_NAME).as_path());
    }

    #[test]
    fn open_is_idempotent_on_existing_dir() {
        let root = temp_root("idempotent");
        let _first = CefProfile::open(&root).unwrap();
        let _second = CefProfile::open(&root).unwrap();
        // Both calls succeed; nothing else to assert.
    }

    #[test]
    fn clear_stale_port_file_removes_existing() {
        let root = temp_root("clear-existing");
        let profile = CefProfile::open(&root).unwrap();
        fs::write(profile.port_file(), b"42\n/devtools/browser/abc").unwrap();
        assert!(profile.port_file().exists());

        profile.clear_stale_port_file().unwrap();
        assert!(!profile.port_file().exists());
    }

    #[test]
    fn clear_stale_port_file_tolerates_missing() {
        let root = temp_root("clear-missing");
        let profile = CefProfile::open(&root).unwrap();
        // No port file present — this should not error.
        profile.clear_stale_port_file().unwrap();
    }

    #[test]
    fn port_file_path_is_inside_profile_dir() {
        let root = temp_root("port-path");
        let profile = CefProfile::open(&root).unwrap();
        let port = profile.port_file();
        assert_eq!(port.parent(), Some(profile.dir()));
        assert_eq!(
            port.file_name().and_then(|n| n.to_str()),
            Some(PORT_FILE_NAME)
        );
    }
}
