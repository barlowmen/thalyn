#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // Pre-`tauri::Builder` browser-process setup for the in-process
    // CEF engine (ADR-0029). On macOS this maps the bundled
    // `Chromium Embedded Framework.framework`; on every supported
    // platform it short-circuits CEF helper subprocesses so they
    // run their work and exit before reaching Tauri. Without this
    // hook the helper subprocess cases would re-enter Tauri and
    // re-launch the app infinitely.
    #[cfg(feature = "cef")]
    {
        if let Some(exit_code) = thalyn_lib::cef::embed::runtime::run_pre_tauri_setup() {
            std::process::exit(exit_code);
        }
    }
    thalyn_lib::run()
}
