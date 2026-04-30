//! macOS CEF helper-subprocess entry point.
//!
//! Each `<App>.app/Contents/Frameworks/Thalyn Helper*.app` bundle's
//! `Contents/MacOS/<Helper Name>` is a copy of this binary. CEF
//! re-execs the helper bundles for renderer / GPU / utility / plugin
//! / alerts subprocesses; the helper binary loads the framework and
//! routes to `cef::execute_process`, which runs the subprocess's
//! work and exits.
//!
//! Per ADR-0029's helper-bundle-integration spike refinement, the
//! helper is a separate `[[bin]]` rather than the parent Thalyn
//! binary symlinked or copied into each helper bundle. Two reasons:
//! the parent is large (~80 MB debug, frontend assets dominate),
//! and codesigning isolation works without a follow-on rotation
//! when each helper has its own executable.
//!
//! The build pipeline (`scripts/stage-cef-helpers.sh`, invoked from
//! Tauri's `beforeBundleCommand`) builds this `[[bin]]` and copies
//! the resulting executable into each of the five helper `.app`
//! bundles staged under `target/cef-helpers/`. Tauri's bundler
//! then copies those staged bundles into the produced `.app` via
//! `bundle.macOS.files`.

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

#[cfg(target_os = "macos")]
fn main() -> ! {
    let exe = std::env::current_exe().expect("current_exe must resolve in the helper binary");
    let loader = cef::library_loader::LibraryLoader::new(&exe, /* helper = */ true);
    if !loader.load() {
        eprintln!(
            "thalyn-cef-helper: cef::library_loader load failed at {} \
             — the helper bundle layout under Contents/Frameworks/ \
             does not match what cef-rs expects",
            exe.display()
        );
        std::process::exit(1);
    }
    // Keep the loader alive for the process lifetime — Drop unmaps
    // the framework, which would invalidate every CEF symbol.
    std::mem::forget(loader);

    let cef_args = cef::args::Args::new();
    let exit_code = cef::execute_process(
        Some(cef_args.as_main_args()),
        None::<&mut cef::App>,
        std::ptr::null_mut(),
    );
    // execute_process returns the helper's intended exit code.
    // Browser-process invocations of this binary are nonsense (the
    // helper bundle's executable is only ever invoked by CEF as a
    // helper subprocess), so we exit unconditionally with whatever
    // execute_process produces.
    std::process::exit(exit_code);
}

#[cfg(not(target_os = "macos"))]
fn main() {
    eprintln!("thalyn-cef-helper is macOS-only");
    std::process::exit(1);
}
