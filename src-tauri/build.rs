fn main() {
    tauri_build::build();

    // whisper-cpp-plus-sys 0.1.4's build script compiles whisper.cpp's
    // ggml-cpu.c with OpenMP enabled but does not emit a
    // `cargo:rustc-link-lib=gomp` directive, so the link step leaves
    // GOMP_barrier / omp_get_thread_num / friends undefined on Linux.
    // Apple's clang ships its own OpenMP runtime under libomp and the
    // system linker resolves it through compiler-rt, so macOS is fine.
    // Wire the explicit gomp link on Linux when voice-whisper is on so
    // CI's `cargo test --lib` link step succeeds.
    if std::env::var_os("CARGO_FEATURE_VOICE_WHISPER").is_some() {
        let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
        if target_os == "linux" {
            println!("cargo:rustc-link-lib=dylib=gomp");
        }
    }
}
