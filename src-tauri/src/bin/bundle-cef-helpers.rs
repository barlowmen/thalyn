//! Stage the macOS CEF helper-bundle layout.
//!
//! Produces:
//!
//! ```text
//! <output>/
//!   Chromium Embedded Framework.framework/   ← copied from <cef-sdk>
//!   Thalyn Helper.app/                       ← created from scratch
//!     Contents/Info.plist
//!     Contents/MacOS/Thalyn Helper           ← copy of <helper-binary>
//!   Thalyn Helper (GPU).app/
//!     ...
//!   Thalyn Helper (Renderer).app/
//!     ...
//!   Thalyn Helper (Plugin).app/
//!     ...
//!   Thalyn Helper (Alerts).app/
//!     ...
//! ```
//!
//! `tauri build` then copies these into the produced
//! `Thalyn.app/Contents/Frameworks/` via `bundle.macOS.files`
//! (declared as directory-to-directory entries — tauri-bundler's
//! `copy_custom_files_to_bundle` recursively copies directories).
//! `bundle.macOS.frameworks` would be the conventional knob for the
//! framework, but tauri-build validates those paths at cargo-build
//! time, before `beforeBundleCommand` has staged anything — so we
//! route the framework through `files` alongside the helpers, where
//! validation is deferred to bundle time.
//!
//! Inputs (all required):
//!
//!   --cef-sdk <path>       Platform-specific CEF SDK directory
//!                          (the one containing
//!                          `Chromium Embedded Framework.framework/`).
//!   --helper-binary <path> The compiled `thalyn-cef-helper`
//!                          executable.
//!   --output <path>        Staging directory (typically
//!                          `<target>/cef-helpers/`). Contents are
//!                          replaced on each run.
//!
//! Optional:
//!
//!   --version <semver>          Default `1.0.0`. Stamped into each
//!                               helper's Info.plist.
//!   --identifier-prefix <str>   Default `app.thalyn`. Helper
//!                               identifiers become
//!                               `<prefix>.helper`,
//!                               `<prefix>.helper.gpu`, etc.
//!
//! Per ADR-0029's helper-bundle-integration spike refinement
//! (`docs/spikes/2026-04-30-cef-helper-bundle-integration.md`).

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

#[cfg(target_os = "macos")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::path::PathBuf;

    let mut args = std::env::args().skip(1);
    let mut cef_sdk: Option<PathBuf> = None;
    let mut helper_binary: Option<PathBuf> = None;
    let mut output: Option<PathBuf> = None;
    let mut version = "1.0.0".to_owned();
    let mut identifier_prefix = "app.thalyn".to_owned();

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--cef-sdk" => cef_sdk = args.next().map(PathBuf::from),
            "--helper-binary" => helper_binary = args.next().map(PathBuf::from),
            "--output" => output = args.next().map(PathBuf::from),
            "--version" => {
                if let Some(v) = args.next() {
                    version = v;
                }
            }
            "--identifier-prefix" => {
                if let Some(v) = args.next() {
                    identifier_prefix = v;
                }
            }
            "-h" | "--help" => {
                print_usage();
                return Ok(());
            }
            other => return Err(format!("unknown argument: {other}").into()),
        }
    }

    let cef_sdk = cef_sdk.ok_or("--cef-sdk is required")?;
    let helper_binary = helper_binary.ok_or("--helper-binary is required")?;
    let output = output.ok_or("--output is required")?;

    if !cef_sdk.is_dir() {
        return Err(format!("--cef-sdk path is not a directory: {}", cef_sdk.display()).into());
    }
    if !helper_binary.is_file() {
        return Err(format!("--helper-binary not found: {}", helper_binary.display()).into());
    }

    std::fs::create_dir_all(&output)?;

    // 1. Copy Chromium Embedded Framework.framework into the
    //    staging dir. Tauri's bundler picks it up via the
    //    `bundle.macOS.files` directory entry — see this binary's
    //    module-level docs for why we route through `files` instead
    //    of `frameworks`.
    stage_framework(&cef_sdk, &output)?;

    // 2. Create the five helper `.app` bundles. Same `files`
    //    mechanism — tauri-bundler's `copy_custom_files_to_bundle`
    //    recursively copies the directory tree.
    for suffix in HELPER_SUFFIXES {
        stage_helper_bundle(
            &output,
            suffix,
            &helper_binary,
            &identifier_prefix,
            &version,
        )?;
    }

    Ok(())
}

#[cfg(target_os = "macos")]
fn print_usage() {
    eprintln!("usage: bundle-cef-helpers [options]");
    eprintln!();
    eprintln!("required:");
    eprintln!("  --cef-sdk <path>       CEF SDK directory");
    eprintln!("  --helper-binary <path> Compiled thalyn-cef-helper");
    eprintln!("  --output <path>        Staging directory");
    eprintln!();
    eprintln!("optional:");
    eprintln!("  --version <semver>         Default 1.0.0");
    eprintln!("  --identifier-prefix <str>  Default app.thalyn");
}

/// Helper-bundle name suffixes per the cef-rs `HELPERS` list. The
/// empty suffix is the "base" helper; the others are the
/// CEF-required subprocess specialisations. The five names
/// (`Thalyn Helper`, `Thalyn Helper (GPU)`, …) match what CEF's
/// runtime spawns when it needs each subprocess kind.
#[cfg(target_os = "macos")]
const HELPER_SUFFIXES: &[&str] = &["", " (GPU)", " (Renderer)", " (Plugin)", " (Alerts)"];

#[cfg(target_os = "macos")]
fn stage_framework(
    cef_sdk: &std::path::Path,
    output: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let framework_src = cef_sdk.join("Chromium Embedded Framework.framework");
    let framework_dst = output.join("Chromium Embedded Framework.framework");
    if !framework_src.exists() {
        return Err(format!(
            "Chromium Embedded Framework.framework not found under --cef-sdk: {}",
            framework_src.display()
        )
        .into());
    }
    if framework_dst.exists() {
        std::fs::remove_dir_all(&framework_dst)?;
    }
    cp_recursive(&framework_src, &framework_dst)?;
    eprintln!(
        "[bundle-cef-helpers] staged framework → {}",
        framework_dst.display()
    );
    Ok(())
}

#[cfg(target_os = "macos")]
fn stage_helper_bundle(
    output: &std::path::Path,
    suffix: &str,
    helper_binary: &std::path::Path,
    identifier_prefix: &str,
    version: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::os::unix::fs::PermissionsExt;

    let helper_name = format!("Thalyn Helper{suffix}");
    let app_dir = output.join(format!("{helper_name}.app"));
    if app_dir.exists() {
        std::fs::remove_dir_all(&app_dir)?;
    }
    let macos_dir = app_dir.join("Contents/MacOS");
    std::fs::create_dir_all(&macos_dir)?;

    // Helper executable: copy `thalyn-cef-helper` into the bundle
    // and chmod +x. `cargo build` outputs leave the bit set, but we
    // re-stamp explicitly so a subsequent dev-tree edit that
    // accidentally touches the staged copy doesn't break things.
    let helper_exec = macos_dir.join(&helper_name);
    std::fs::copy(helper_binary, &helper_exec)?;
    let mut perms = std::fs::metadata(&helper_exec)?.permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(&helper_exec, perms)?;

    // Helper Info.plist. The minimum CEF requires + LSUIElement so
    // the helper has no Dock entry. Wider plist keys
    // (NSCameraUsageDescription etc.) belong on the parent app's
    // Info.plist, not on each helper, so we omit them here.
    let identifier_suffix = identifier_suffix_for(suffix);
    let identifier = if identifier_suffix.is_empty() {
        format!("{identifier_prefix}.helper")
    } else {
        format!("{identifier_prefix}.helper.{identifier_suffix}")
    };
    let info_plist = app_dir.join("Contents/Info.plist");
    std::fs::write(
        &info_plist,
        helper_info_plist(&helper_name, &identifier, version),
    )?;

    eprintln!("[bundle-cef-helpers] staged helper → {}", app_dir.display());
    Ok(())
}

#[cfg(target_os = "macos")]
fn cp_recursive(
    src: &std::path::Path,
    dst: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    // `cp -R -p` preserves symlinks (the framework's `Versions/A`
    // structure relies on them) and timestamps. macOS-only, so the
    // `cp` binary is always available.
    let status = std::process::Command::new("cp")
        .arg("-R")
        .arg("-p")
        .arg(src)
        .arg(dst)
        .status()?;
    if !status.success() {
        return Err(format!(
            "cp -R {} {} failed with {status}",
            src.display(),
            dst.display(),
        )
        .into());
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn identifier_suffix_for(helper_suffix: &str) -> String {
    // Strip the parens + space from " (GPU)" → "gpu", " (Renderer)"
    // → "renderer", etc. Empty suffix → empty string (no
    // sub-identifier).
    helper_suffix
        .trim()
        .trim_start_matches('(')
        .trim_end_matches(')')
        .to_lowercase()
}

#[cfg(target_os = "macos")]
fn helper_info_plist(name: &str, identifier: &str, version: &str) -> String {
    // Hand-rolled plist XML. Avoids pulling in the `plist` crate as
    // a direct dep just for this binary; the field set is fixed and
    // small. Field choices match cef-rs's `BundleInfo` /
    // `InfoPlist` shape from `build_util/mac.rs`.
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>English</string>
    <key>CFBundleDisplayName</key>
    <string>{name}</string>
    <key>CFBundleExecutable</key>
    <string>{name}</string>
    <key>CFBundleIdentifier</key>
    <string>{identifier}</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>{name}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>{version}</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>CFBundleVersion</key>
    <string>{version}</string>
    <key>LSEnvironment</key>
    <dict>
        <key>MallocNanoZone</key>
        <string>0</string>
    </dict>
    <key>LSFileQuarantineEnabled</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>LSUIElement</key>
    <string>1</string>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
</dict>
</plist>
"#
    )
}

#[cfg(not(target_os = "macos"))]
fn main() {
    eprintln!("bundle-cef-helpers is macOS-only");
    std::process::exit(1);
}
