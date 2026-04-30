//! Locate the pinned CEF binary distribution on disk.
//!
//! The cef-dll-sys build script downloads the SDK to a versioned
//! subdirectory under `$CEF_PATH`, laid out as
//! `{CEF_PATH}/{version}/cef_{os}_{arch}/`. The same path layout
//! works at runtime — `LD_LIBRARY_PATH` / `DYLD_FALLBACK_LIBRARY_PATH`
//! / `PATH` need to point inside the platform-specific sub-folder so
//! the CEF helper processes can find `libcef`. This module is the
//! single source of truth for that resolution; everywhere else in
//! the engine should consume a [`CefSdk`] handle rather than
//! reconstructing the path itself.
//!
//! Off-feature (`#[cfg(not(feature = "cef"))]`) the resolver still
//! exists so the rest of the engine can build against the API; it
//! just never gets called by anything that ships in the default
//! binary.

use std::path::{Path, PathBuf};

use thiserror::Error;

const CEF_VERSION_FILE: &str = include_str!("../../cef-version.txt");

/// CEF version pinned by `src-tauri/cef-version.txt`. The trailing
/// newline is stripped so it can be used as a directory component
/// directly. Written as `cef-version.txt` rather than a Cargo
/// metadata field so the CI workflow can read it without parsing
/// `Cargo.toml`.
pub fn pinned_cef_version() -> &'static str {
    CEF_VERSION_FILE.trim()
}

#[derive(Debug, Error)]
pub enum SdkResolveError {
    #[error("CEF_PATH is not set; cannot locate the CEF binary distribution")]
    CefPathUnset,
    #[error("CEF_PATH '{0}' does not exist")]
    CefPathMissing(PathBuf),
    #[error("expected the CEF SDK at {0}, but the directory is missing")]
    SdkDirMissing(PathBuf),
    #[error("expected libcef at {0}, but it is missing")]
    LibCefMissing(PathBuf),
    #[error("unsupported target triple {0:?} — no CEF mapping")]
    UnsupportedTarget(String),
}

/// A resolved, on-disk CEF binary distribution. The path layout
/// matches what cef-dll-sys's build script produces, so once a
/// `CefSdk` is in hand the rest of the engine can treat the SDK as
/// "known good."
#[derive(Debug, Clone)]
pub struct CefSdk {
    /// Top of the platform-specific tree — e.g.
    /// `~/.cache/thalyn-cef/147.1.0+147.0.10/cef_linux_x86_64`.
    sdk_dir: PathBuf,
    /// CEF version this SDK matches; carried for diagnostics.
    version: String,
    /// Target triple of the host the SDK was resolved against.
    target_triple: String,
}

impl CefSdk {
    /// Resolve the SDK rooted at `$CEF_PATH` for the host's target
    /// triple and the pinned version. Validates that the platform
    /// sub-directory exists; does *not* attempt to download anything
    /// — that is the cef-dll-sys build script's job.
    pub fn resolve_default() -> Result<Self, SdkResolveError> {
        let cef_path = std::env::var_os("CEF_PATH").ok_or(SdkResolveError::CefPathUnset)?;
        Self::resolve_at(
            Path::new(&cef_path),
            host_target_triple(),
            pinned_cef_version(),
        )
    }

    /// Test-friendly resolver: callers pass the root, target triple,
    /// and version explicitly.
    pub fn resolve_at(
        cef_path: &Path,
        target: &str,
        version: &str,
    ) -> Result<Self, SdkResolveError> {
        if !cef_path.exists() {
            return Err(SdkResolveError::CefPathMissing(cef_path.to_path_buf()));
        }

        let os_arch = target_to_os_arch(target)?;
        let sdk_dir = cef_path.join(version).join(os_arch);
        if !sdk_dir.is_dir() {
            return Err(SdkResolveError::SdkDirMissing(sdk_dir));
        }

        let libcef = sdk_dir.join(libcef_relpath(target));
        if !libcef.exists() {
            return Err(SdkResolveError::LibCefMissing(libcef));
        }

        Ok(Self {
            sdk_dir,
            version: version.to_owned(),
            target_triple: target.to_owned(),
        })
    }

    pub fn sdk_dir(&self) -> &Path {
        &self.sdk_dir
    }

    pub fn version(&self) -> &str {
        &self.version
    }

    pub fn target_triple(&self) -> &str {
        &self.target_triple
    }

    /// Directory the runtime loader needs on its search path. macOS
    /// loads the framework from a sub-bundle, so the runtime path is
    /// nested differently from the link-time `sdk_dir`.
    pub fn runtime_library_dir(&self) -> PathBuf {
        match host_os(&self.target_triple) {
            Some("macos") => self
                .sdk_dir
                .join("Chromium Embedded Framework.framework")
                .join("Libraries"),
            Some("windows") => self.sdk_dir.join("Release"),
            // Linux ships libcef.so directly under Release/.
            _ => self.sdk_dir.join("Release"),
        }
    }
}

/// Map a Rust target triple to the directory cef-dll-sys uses
/// (`cef_{os}_{arch}`). Mirrors `download-cef`'s `OsAndArch::Display`
/// in cef-rs so the same path appears on both sides of the boundary.
fn target_to_os_arch(target: &str) -> Result<&'static str, SdkResolveError> {
    Ok(match target {
        "aarch64-apple-darwin" => "cef_macos_aarch64",
        "x86_64-apple-darwin" => "cef_macos_x86_64",
        "x86_64-pc-windows-msvc" => "cef_windows_x86_64",
        "aarch64-pc-windows-msvc" => "cef_windows_aarch64",
        "i686-pc-windows-msvc" => "cef_windows_x86",
        "x86_64-unknown-linux-gnu" => "cef_linux_x86_64",
        "aarch64-unknown-linux-gnu" => "cef_linux_aarch64",
        "arm-unknown-linux-gnueabi" => "cef_linux_arm",
        other => return Err(SdkResolveError::UnsupportedTarget(other.to_owned())),
    })
}

/// Path of `libcef` (or its platform equivalent) inside the SDK,
/// relative to `sdk_dir`.
fn libcef_relpath(target: &str) -> PathBuf {
    match host_os(target) {
        Some("macos") => PathBuf::from("Chromium Embedded Framework.framework")
            .join("Chromium Embedded Framework"),
        Some("windows") => PathBuf::from("Release").join("libcef.dll"),
        _ => PathBuf::from("Release").join("libcef.so"),
    }
}

fn host_os(target: &str) -> Option<&'static str> {
    if target.contains("apple-darwin") {
        Some("macos")
    } else if target.contains("windows") {
        Some("windows")
    } else if target.contains("linux") {
        Some("linux")
    } else {
        None
    }
}

/// Best-effort host target triple. Compiled-in via env! so we report
/// the same triple cef-dll-sys's build script saw at compile time.
fn host_target_triple() -> &'static str {
    // env!("TARGET") would fail because TARGET is only set during
    // build scripts. We fall back to the canonical cfg combos here.
    // The tests exercise the explicit `resolve_at` path.
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        "aarch64-apple-darwin"
    } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
        "x86_64-apple-darwin"
    } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        "x86_64-pc-windows-msvc"
    } else if cfg!(all(target_os = "windows", target_arch = "aarch64")) {
        "aarch64-pc-windows-msvc"
    } else if cfg!(all(target_os = "windows", target_arch = "x86")) {
        "i686-pc-windows-msvc"
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        "x86_64-unknown-linux-gnu"
    } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
        "aarch64-unknown-linux-gnu"
    } else if cfg!(all(target_os = "linux", target_arch = "arm")) {
        "arm-unknown-linux-gnueabi"
    } else {
        // Fall through to a value that will fail resolve cleanly so
        // the user gets a typed error rather than a panic.
        "unknown"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_root(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-cef-sdk-test-{}-{}",
            label,
            std::process::id()
        ));
        if dir.exists() {
            fs::remove_dir_all(&dir).unwrap();
        }
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn lay_out_linux_sdk(root: &Path, version: &str) -> PathBuf {
        let sdk = root.join(version).join("cef_linux_x86_64");
        fs::create_dir_all(sdk.join("Release")).unwrap();
        fs::write(sdk.join("Release").join("libcef.so"), b"stub").unwrap();
        sdk
    }

    fn lay_out_macos_sdk(root: &Path, version: &str) -> PathBuf {
        let sdk = root.join(version).join("cef_macos_aarch64");
        let framework = sdk
            .join("Chromium Embedded Framework.framework")
            .join("Libraries");
        fs::create_dir_all(&framework).unwrap();
        // The libcef binary lives at the framework's root, not under
        // Libraries — Libraries is for the helper dylibs.
        fs::write(
            sdk.join("Chromium Embedded Framework.framework")
                .join("Chromium Embedded Framework"),
            b"stub",
        )
        .unwrap();
        sdk
    }

    #[test]
    fn pinned_version_is_trimmed_and_non_empty() {
        let v = pinned_cef_version();
        assert!(!v.is_empty());
        assert_eq!(v, v.trim());
    }

    #[test]
    fn resolves_against_a_synthetic_linux_layout() {
        let root = temp_root("linux-ok");
        let version = "147.1.0+147.0.10";
        let sdk_dir = lay_out_linux_sdk(&root, version);

        let resolved =
            CefSdk::resolve_at(&root, "x86_64-unknown-linux-gnu", version).expect("resolve");
        assert_eq!(resolved.sdk_dir(), sdk_dir);
        assert_eq!(resolved.version(), version);
        assert_eq!(resolved.runtime_library_dir(), sdk_dir.join("Release"));
    }

    #[test]
    fn resolves_against_a_synthetic_macos_layout() {
        let root = temp_root("macos-ok");
        let version = "147.1.0+147.0.10";
        let sdk_dir = lay_out_macos_sdk(&root, version);

        let resolved = CefSdk::resolve_at(&root, "aarch64-apple-darwin", version).expect("resolve");
        assert_eq!(resolved.sdk_dir(), sdk_dir);
        assert_eq!(
            resolved.runtime_library_dir(),
            sdk_dir
                .join("Chromium Embedded Framework.framework")
                .join("Libraries")
        );
    }

    #[test]
    fn missing_root_is_a_typed_error() {
        let bogus = std::env::temp_dir().join("thalyn-cef-sdk-test-not-here");
        let _ = std::fs::remove_dir_all(&bogus);
        let err =
            CefSdk::resolve_at(&bogus, "x86_64-unknown-linux-gnu", "147.1.0+147.0.10").unwrap_err();
        assert!(matches!(err, SdkResolveError::CefPathMissing(_)));
    }

    #[test]
    fn missing_versioned_subdir_is_a_typed_error() {
        let root = temp_root("no-version");
        let err =
            CefSdk::resolve_at(&root, "x86_64-unknown-linux-gnu", "999.0.0+999.0.0").unwrap_err();
        assert!(matches!(err, SdkResolveError::SdkDirMissing(_)));
    }

    #[test]
    fn missing_libcef_is_a_typed_error() {
        let root = temp_root("no-libcef");
        let version = "147.1.0+147.0.10";
        let sdk = root.join(version).join("cef_linux_x86_64");
        std::fs::create_dir_all(&sdk).unwrap();

        let err = CefSdk::resolve_at(&root, "x86_64-unknown-linux-gnu", version).unwrap_err();
        assert!(matches!(err, SdkResolveError::LibCefMissing(_)));
    }

    #[test]
    fn unsupported_target_is_a_typed_error() {
        let root = temp_root("bad-target");
        let err =
            CefSdk::resolve_at(&root, "wasm32-unknown-unknown", "147.1.0+147.0.10").unwrap_err();
        assert!(matches!(err, SdkResolveError::UnsupportedTarget(_)));
    }
}
