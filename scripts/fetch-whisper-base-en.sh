#!/usr/bin/env bash
# Fetch the base.en Whisper GGML model and stage it under
# `target/whisper-models/` for the bundler to copy into
# `<App>.app/Contents/Resources/whisper/`.
#
# This is the immediate-first-use preload per ADR-0025: a 148 MB
# model that ships inside the installer so push-to-talk works on
# day one without an internet round-trip. The larger small.en
# (487 MB) lazy-downloads at runtime — that path is filed on
# `docs/going-public-checklist.md`.
#
# Idempotent: a SHA-256 match on an existing staged file
# short-circuits the download. The pinned digest matches
# `whisper.cpp/models/download-ggml-model.sh` as of 2026-05.
#
# Skippable: if `THALYN_SKIP_WHISPER_PRELOAD=1`, the staging step
# is a no-op. The bundler will then fall through to the runtime
# fallback path on the user's machine (which currently surfaces
# as "voice STT falls back to noop until the lazy-download path
# runs"). Use this when iterating on Rust-only changes that don't
# need a working voice surface.

set -euo pipefail

if [ "${THALYN_SKIP_WHISPER_PRELOAD:-}" = "1" ]; then
  printf 'fetch-whisper-base-en: THALYN_SKIP_WHISPER_PRELOAD=1, skipping\n' >&2
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE_DIR="$REPO_ROOT/target/whisper-models"
STAGE_FILE="$STAGE_DIR/ggml-base.en.bin"

URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
EXPECTED_SHA="a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"

verify_sha() {
  local file="$1"
  local expected="$2"
  local actual
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  else
    printf 'fetch-whisper-base-en: no shasum / sha256sum binary on PATH\n' >&2
    return 2
  fi
  if [ "$actual" != "$expected" ]; then
    printf 'fetch-whisper-base-en: SHA-256 mismatch on %s\n  expected %s\n  got      %s\n' \
      "$file" "$expected" "$actual" >&2
    return 1
  fi
}

mkdir -p "$STAGE_DIR"

if [ -f "$STAGE_FILE" ] && verify_sha "$STAGE_FILE" "$EXPECTED_SHA" 2>/dev/null; then
  printf 'fetch-whisper-base-en: %s already staged with matching SHA, skipping fetch\n' \
    "$STAGE_FILE" >&2
  exit 0
fi

TMP="$STAGE_FILE.partial"
trap 'rm -f "$TMP"' EXIT

if command -v curl >/dev/null 2>&1; then
  curl -fSL --retry 3 --retry-delay 2 -o "$TMP" "$URL"
elif command -v wget >/dev/null 2>&1; then
  wget --tries=3 --waitretry=2 -O "$TMP" "$URL"
else
  printf 'fetch-whisper-base-en: no curl / wget on PATH\n' >&2
  exit 2
fi

verify_sha "$TMP" "$EXPECTED_SHA"
mv "$TMP" "$STAGE_FILE"
trap - EXIT

printf 'fetch-whisper-base-en: staged %s\n' "$STAGE_FILE" >&2
