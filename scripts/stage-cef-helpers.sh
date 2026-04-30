#!/bin/sh
# Stage the macOS CEF helper-bundle layout for `tauri build`.
#
# Invoked from `src-tauri/tauri.conf.json`'s `beforeBundleCommand`
# hook. Builds the `thalyn-cef-helper` and `bundle-cef-helpers`
# `[[bin]]` targets, then runs `bundle-cef-helpers` to populate
# `<target>/cef-helpers/` with the framework + the five helper
# `.app` bundles. Tauri's bundler then copies them into the
# produced `.app` via `bundle.macOS.frameworks` and
# `bundle.macOS.files`.
#
# Per ADR-0029's helper-bundle-integration spike refinement
# (`docs/spikes/2026-04-30-cef-helper-bundle-integration.md`).

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_TAURI="$REPO_ROOT/src-tauri"
TARGET_DIR="${CARGO_TARGET_DIR:-$SRC_TAURI/target}"
PROFILE_DIR="$TARGET_DIR/release"
HELPER_BIN="$PROFILE_DIR/thalyn-cef-helper"
BUNDLE_BIN="$PROFILE_DIR/bundle-cef-helpers"
OUTPUT_DIR="$TARGET_DIR/cef-helpers"

# CEF SDK resolution. The cef-dll-sys build script honours CEF_PATH
# either as the cache root (e.g. `~/.cache/thalyn-cef`, with the
# SDK at `<root>/<build-version>/<os_arch>/`) or as the version dir
# (e.g. `~/.cache/thalyn-cef/147.0.10`, with the SDK at
# `<dir>/<os_arch>/`). Support both layouts.
if [ -z "${CEF_PATH:-}" ]; then
    echo "[stage-cef-helpers] CEF_PATH must be set to the CEF cache root or version dir" >&2
    exit 1
fi

CEF_VERSION_FULL="$(cat "$SRC_TAURI/cef-version.txt")"
# Build metadata (after `+`) is the directory cef-dll-sys writes
# under. download_cef::default_version("147.1.0+147.0.10") returns
# "147.0.10"; we reproduce that here without a Rust call.
case "$CEF_VERSION_FULL" in
    *+*) CEF_BUILD_DIR="${CEF_VERSION_FULL##*+}" ;;
    *)   CEF_BUILD_DIR="$CEF_VERSION_FULL" ;;
esac

case "$(uname -m)" in
    arm64|aarch64) CEF_OS_ARCH="cef_macos_aarch64" ;;
    x86_64)        CEF_OS_ARCH="cef_macos_x86_64" ;;
    *)
        echo "[stage-cef-helpers] unsupported macOS arch: $(uname -m)" >&2
        exit 1
        ;;
esac

if [ -d "$CEF_PATH/$CEF_OS_ARCH" ]; then
    CEF_SDK="$CEF_PATH/$CEF_OS_ARCH"
elif [ -d "$CEF_PATH/$CEF_BUILD_DIR/$CEF_OS_ARCH" ]; then
    CEF_SDK="$CEF_PATH/$CEF_BUILD_DIR/$CEF_OS_ARCH"
else
    echo "[stage-cef-helpers] could not locate CEF SDK." >&2
    echo "  CEF_PATH=$CEF_PATH" >&2
    echo "  expected one of:" >&2
    echo "    $CEF_PATH/$CEF_OS_ARCH" >&2
    echo "    $CEF_PATH/$CEF_BUILD_DIR/$CEF_OS_ARCH" >&2
    exit 1
fi

# Resolve the app version from tauri.conf.json so helper bundles
# carry the same `CFBundleVersion` as the parent app. Fall back to
# 0.0.0 if the lookup fails — the helper Info.plist version is
# cosmetic, the runtime doesn't gate on it.
APP_VERSION="$(grep -E '^\s*"version"\s*:' "$SRC_TAURI/tauri.conf.json" \
    | head -n 1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/' || true)"
APP_VERSION="${APP_VERSION:-0.0.0}"

# Resolve the bundle identifier so helper identifiers nest under it.
APP_IDENTIFIER_PREFIX="$(grep -E '^\s*"identifier"\s*:' "$SRC_TAURI/tauri.conf.json" \
    | head -n 1 | sed -E 's/.*"identifier"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/' || true)"
APP_IDENTIFIER_PREFIX="${APP_IDENTIFIER_PREFIX:-app.thalyn}"

echo "[stage-cef-helpers] building thalyn-cef-helper + bundle-cef-helpers" >&2
cd "$SRC_TAURI"
cargo build --release --features cef \
    --bin thalyn-cef-helper \
    --bin bundle-cef-helpers

if [ ! -f "$HELPER_BIN" ]; then
    echo "[stage-cef-helpers] build did not produce $HELPER_BIN" >&2
    exit 1
fi
if [ ! -f "$BUNDLE_BIN" ]; then
    echo "[stage-cef-helpers] build did not produce $BUNDLE_BIN" >&2
    exit 1
fi

echo "[stage-cef-helpers] CEF SDK: $CEF_SDK" >&2
echo "[stage-cef-helpers] staging into $OUTPUT_DIR" >&2
"$BUNDLE_BIN" \
    --cef-sdk "$CEF_SDK" \
    --helper-binary "$HELPER_BIN" \
    --output "$OUTPUT_DIR" \
    --version "$APP_VERSION" \
    --identifier-prefix "$APP_IDENTIFIER_PREFIX"
