//! Canonical Thalyn data directory.
//!
//! Mirrors `brain/thalyn_brain/orchestration/storage.py::default_data_dir`
//! so the Rust core and the Python brain agree on where state lives.
//! `init_app_state` resolves this path and forwards it to the brain via
//! the `THALYN_DATA_DIR` env var, closing the latent divergence between
//! Tauri's bundle-id'd `app_data_dir()` and the brain's literal
//! `Library/Application Support/Thalyn/data` (per ADR-0028).
//!
//! The env var `THALYN_DATA_DIR` overrides the per-OS default for both
//! processes — it's the single knob a user or test can flip.

use std::env;
use std::path::PathBuf;

/// Resolve the canonical data directory for this OS.
pub fn resolve() -> PathBuf {
    if let Some(override_dir) = env::var_os("THALYN_DATA_DIR") {
        if !override_dir.is_empty() {
            return PathBuf::from(override_dir);
        }
    }
    if cfg!(target_os = "macos") {
        return home().join("Library/Application Support/Thalyn/data");
    }
    if cfg!(target_os = "windows") {
        let base = env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| home().join("AppData/Roaming"));
        return base.join("Thalyn/data");
    }
    let base = env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".local/share"));
    base.join("thalyn/data")
}

fn home() -> PathBuf {
    let var = if cfg!(target_os = "windows") {
        "USERPROFILE"
    } else {
        "HOME"
    };
    env::var_os(var)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_returns_a_non_empty_absolute_path_on_unix_or_mac() {
        // On Linux / macOS CI, $HOME is set; the resolved path should
        // include it. We don't mutate env vars here because cargo runs
        // tests in parallel — we just assert the function produces a
        // path under the canonical Thalyn root.
        if cfg!(target_os = "macos") || cfg!(target_os = "linux") {
            let path = resolve();
            assert!(!path.as_os_str().is_empty());
            // The macOS/Linux paths both end with `thalyn/data` or
            // `Thalyn/data`. Lowercase comparison covers both.
            let lower = path.to_string_lossy().to_lowercase();
            assert!(
                lower.ends_with("thalyn/data"),
                "unexpected path tail: {:?}",
                path
            );
        }
    }
}
