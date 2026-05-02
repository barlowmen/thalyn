#!/bin/sh
# Build the Thalyn brain sidecar via PyInstaller (ADR-0018).
#
# Produces a one-folder bundle staged at
# `<target>/brain-sidecar/thalyn-brain/`. Tauri's
# `beforeBundleCommand` copies it from there into
# `<App>.app/Contents/Resources/thalyn-brain/` via the entry in
# `tauri.conf.json`'s `bundle.macOS.files`.
#
# Set `THALYN_SKIP_BRAIN_BUNDLE=1` to reuse the existing staged
# bundle — useful when iterating on Rust changes that don't touch
# the brain. The full PyInstaller run takes ~30s on a warm cache.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BRAIN_DIR="$REPO_ROOT/brain"
SRC_TAURI="$REPO_ROOT/src-tauri"
TARGET_DIR="${CARGO_TARGET_DIR:-$SRC_TAURI/target}"
OUT_DIR="$TARGET_DIR/brain-sidecar"
STAGED="$OUT_DIR/thalyn-brain"

if [ -n "${THALYN_SKIP_BRAIN_BUNDLE:-}" ] && [ -d "$STAGED" ]; then
    echo "[build-brain-sidecar] THALYN_SKIP_BRAIN_BUNDLE set; reusing $STAGED" >&2
    exit 0
fi

echo "[build-brain-sidecar] running PyInstaller" >&2
cd "$BRAIN_DIR"

# Sync the bundle group so PyInstaller is available even on a fresh
# checkout. `--frozen` keeps the lockfile authoritative.
uv sync --group bundle --frozen >/dev/null

# Clean PyInstaller's per-build state — it caches aggressively and
# has been seen to ship stale modules across spec edits.
rm -rf build dist
uv run --group bundle pyinstaller --noconfirm --clean thalyn-brain.spec

if [ ! -x "dist/thalyn-brain/thalyn-brain" ]; then
    echo "[build-brain-sidecar] PyInstaller did not produce dist/thalyn-brain/thalyn-brain" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"
rm -rf "$STAGED"
mv dist/thalyn-brain "$STAGED"

echo "[build-brain-sidecar] staged at $STAGED" >&2
