#!/bin/sh
# Tauri's `beforeBundleCommand` runs once between `cargo build` and
# `cargo-tauri`'s bundling step. We need three artifacts staged before
# bundling so they can be copied into `<App>.app` via
# `tauri.conf.json`'s `bundle.macOS.files`:
#
#   1. The CEF helper-bundle layout (the framework + the five
#      `Thalyn Helper*.app` bundles) — staged by
#      `stage-cef-helpers.sh`. Required for the in-process Chromium
#      to spawn its renderer / GPU / utility / plugin / alerts
#      subprocesses (ADR-0029).
#   2. The PyInstaller'd brain sidecar (one-folder bundle) — staged
#      by `build-brain-sidecar.sh`. Required so a Finder-installed
#      Thalyn can spawn the brain without a `uv`-managed venv on
#      the user's PATH (ADR-0018).
#   3. The base.en Whisper model (148 MB) — staged by
#      `fetch-whisper-base-en.sh`. The immediate-first-use preload
#      so voice input works without an internet round-trip; the
#      larger small.en (487 MB) lazy-downloads at runtime
#      (ADR-0025).
#
# Run sequentially: the CEF stage is fast (cargo cached), the
# PyInstaller stage is the slow one (~30s warm), the Whisper fetch
# is one-shot (~30s on first run, instant on cache hit). Failure
# of any step kills the bundle.

set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"

"$DIR/stage-cef-helpers.sh"
"$DIR/build-brain-sidecar.sh"
"$DIR/fetch-whisper-base-en.sh"
