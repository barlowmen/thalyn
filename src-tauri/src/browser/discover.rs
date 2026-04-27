//! Discovery — find a Chromium-family binary on the user's machine.
//!
//! The user's existing Chrome / Chromium / Edge / Brave install is
//! the source of truth. v1 does not bundle Chromium and does not
//! download one — bundling adds ~200 MB to the installer (a
//! non-starter for an open-source desktop project) and downloading on
//! first run breaks the offline-first promise. If none is found, the
//! caller surfaces a clear error pointing the user at the install
//! flow they prefer.
//!
//! Order of preference:
//!  1. `THALYN_BROWSER_BIN` env var if set (testing + power-user
//!     override; an explicit path always wins).
//!  2. Chrome (most common, best-supported by CDP).
//!  3. Chromium (matches Chrome closely; common on Linux).
//!  4. Microsoft Edge (Chromium-derived; Windows default; tested CDP).
//!  5. Brave (Chromium-derived; common on macOS / Linux dev machines).
//!
//! All four are CDP-compatible; the family is recorded so higher
//! layers can adjust spawn flags if a particular family needs a
//! workaround (none today).

use std::path::PathBuf;

use thiserror::Error;

const ENV_OVERRIDE: &str = "THALYN_BROWSER_BIN";

#[derive(Debug, Error)]
pub enum DiscoverError {
    #[error("no Chromium-family browser found; install Google Chrome, Chromium, Microsoft Edge, or Brave")]
    NotFound,
    #[error("the browser at {path:?} from {origin} is not executable: {reason}")]
    NotExecutable {
        path: PathBuf,
        origin: &'static str,
        reason: String,
    },
}

/// Which Chromium derivative the binary belongs to. Surfaced for
/// telemetry and (eventually) family-specific spawn quirks; today we
/// treat them identically.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BrowserFamily {
    Chrome,
    Chromium,
    Edge,
    Brave,
    /// Override-supplied via `THALYN_BROWSER_BIN`; we believe the user.
    Override,
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct BrowserBinary {
    pub path: PathBuf,
    pub family: BrowserFamily,
}

/// Locate a Chromium-family binary, preferring the env override.
pub fn find_browser() -> Result<BrowserBinary, DiscoverError> {
    if let Ok(value) = std::env::var(ENV_OVERRIDE) {
        let path = PathBuf::from(value);
        if !path.exists() {
            return Err(DiscoverError::NotExecutable {
                path,
                origin: ENV_OVERRIDE,
                reason: "path does not exist".into(),
            });
        }
        return Ok(BrowserBinary {
            path,
            family: BrowserFamily::Override,
        });
    }

    for (path, family) in candidate_paths() {
        if path.exists() {
            return Ok(BrowserBinary { path, family });
        }
    }
    Err(DiscoverError::NotFound)
}

/// Return the candidate (path, family) pairs in preference order. Pure
/// function so tests can lock in the platform-specific order.
fn candidate_paths() -> Vec<(PathBuf, BrowserFamily)> {
    let mut out = Vec::new();

    #[cfg(target_os = "macos")]
    {
        out.extend(macos_candidates());
    }
    #[cfg(target_os = "linux")]
    {
        out.extend(linux_candidates());
    }
    #[cfg(target_os = "windows")]
    {
        out.extend(windows_candidates());
    }

    out
}

#[cfg(target_os = "macos")]
fn macos_candidates() -> Vec<(PathBuf, BrowserFamily)> {
    let home = std::env::var_os("HOME").map(PathBuf::from);
    let mut roots: Vec<PathBuf> = vec!["/Applications".into()];
    if let Some(h) = home.as_ref() {
        roots.push(h.join("Applications"));
    }
    let entries: &[(&str, &str, BrowserFamily)] = &[
        (
            "Google Chrome.app",
            "Contents/MacOS/Google Chrome",
            BrowserFamily::Chrome,
        ),
        (
            "Chromium.app",
            "Contents/MacOS/Chromium",
            BrowserFamily::Chromium,
        ),
        (
            "Microsoft Edge.app",
            "Contents/MacOS/Microsoft Edge",
            BrowserFamily::Edge,
        ),
        (
            "Brave Browser.app",
            "Contents/MacOS/Brave Browser",
            BrowserFamily::Brave,
        ),
    ];
    let mut out = Vec::new();
    for root in &roots {
        for (bundle, inner, family) in entries {
            out.push((root.join(bundle).join(inner), *family));
        }
    }
    out
}

#[cfg(target_os = "linux")]
fn linux_candidates() -> Vec<(PathBuf, BrowserFamily)> {
    let known: &[(&str, BrowserFamily)] = &[
        ("/usr/bin/google-chrome", BrowserFamily::Chrome),
        ("/usr/bin/google-chrome-stable", BrowserFamily::Chrome),
        ("/opt/google/chrome/google-chrome", BrowserFamily::Chrome),
        ("/usr/bin/chromium", BrowserFamily::Chromium),
        ("/usr/bin/chromium-browser", BrowserFamily::Chromium),
        ("/snap/bin/chromium", BrowserFamily::Chromium),
        ("/usr/bin/microsoft-edge", BrowserFamily::Edge),
        ("/usr/bin/microsoft-edge-stable", BrowserFamily::Edge),
        ("/usr/bin/brave-browser", BrowserFamily::Brave),
        ("/usr/bin/brave", BrowserFamily::Brave),
    ];
    known.iter().map(|(p, f)| (PathBuf::from(p), *f)).collect()
}

#[cfg(target_os = "windows")]
fn windows_candidates() -> Vec<(PathBuf, BrowserFamily)> {
    let pf = std::env::var_os("ProgramFiles").map(PathBuf::from);
    let pf86 = std::env::var_os("ProgramFiles(x86)").map(PathBuf::from);
    let local = std::env::var_os("LOCALAPPDATA").map(PathBuf::from);
    let entries: &[(&str, BrowserFamily)] = &[
        (
            "Google\\Chrome\\Application\\chrome.exe",
            BrowserFamily::Chrome,
        ),
        (
            "Microsoft\\Edge\\Application\\msedge.exe",
            BrowserFamily::Edge,
        ),
        (
            "BraveSoftware\\Brave-Browser\\Application\\brave.exe",
            BrowserFamily::Brave,
        ),
        ("Chromium\\Application\\chrome.exe", BrowserFamily::Chromium),
    ];
    let mut out = Vec::new();
    for root in [pf, pf86, local].into_iter().flatten() {
        for (rel, family) in entries {
            out.push((root.join(rel), *family));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// Serialise tests that touch `THALYN_BROWSER_BIN` so they don't
    /// race with each other.
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn override_env_var_wins_when_path_exists() {
        let _g = ENV_LOCK.lock().unwrap();
        // /bin/sh exists on every supported OS in the test matrix.
        std::env::set_var(ENV_OVERRIDE, "/bin/sh");
        let found = find_browser().unwrap();
        std::env::remove_var(ENV_OVERRIDE);
        assert_eq!(found.path, PathBuf::from("/bin/sh"));
        assert_eq!(found.family, BrowserFamily::Override);
    }

    #[test]
    fn override_env_var_with_missing_path_errors_clearly() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::set_var(ENV_OVERRIDE, "/definitely/not/a/real/binary");
        let err = find_browser().unwrap_err();
        std::env::remove_var(ENV_OVERRIDE);
        assert!(matches!(err, DiscoverError::NotExecutable { .. }));
    }

    #[test]
    fn candidate_list_is_non_empty_on_supported_platforms() {
        let candidates = candidate_paths();
        // We don't assert any one path exists — that would fail in
        // headless CI — only that the OS-specific code path produced
        // candidates the discovery loop can consult.
        assert!(
            !candidates.is_empty(),
            "expected at least one candidate path"
        );
    }
}
