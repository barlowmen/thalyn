#!/usr/bin/env bash
# Block references to retired Anthropic model ids. Anthropic announced
# the 2026-06-15 retirement of ``claude-sonnet-4-20250514`` and
# ``claude-opus-4-20250514`` (the "Claude 4" snapshots); v2 has moved
# to ``claude-sonnet-4-6`` / ``claude-opus-4-7``. Catching the retired
# ids in CI keeps a stale fixture or copy-pasted example from
# silently breaking once the upstream snapshots stop responding.
#
# Usage:
#   scripts/scan-retired-models.sh         # exit 1 on any match
#   scripts/scan-retired-models.sh --list  # print matches, exit 0

set -euo pipefail

PATTERNS=(
  'claude-sonnet-4-20250514'
  'claude-opus-4-20250514'
)

# Files exempt from scanning. The scanner itself must reference the
# retired ids by name; the architecture-review record documents the
# retirement decision (an immutable historical artefact); the
# going-public-checklist tracks the reminder.
EXEMPT_PATHS=(
  ':!scripts/scan-retired-models.sh'
  ':!docs/going-public-checklist.md'
  ':!docs/architecture-reviews/'
)

joined_pattern=""
for pattern in "${PATTERNS[@]}"; do
  if [ -z "$joined_pattern" ]; then
    joined_pattern="$pattern"
  else
    joined_pattern="$joined_pattern|$pattern"
  fi
done

# Walk the tracked tree (so untracked / gitignored noise stays out)
# minus the exempt paths. ``git ls-files`` honours .gitignore; combined
# with the pathspec exclusions it matches the same input the
# leakage-scan hook uses.
matches=$(
  git ls-files -z -- "${EXEMPT_PATHS[@]}" \
    | xargs -0 grep -nE -- "$joined_pattern" 2>/dev/null \
    || true
)

if [ "${1:-}" = "--list" ]; then
  echo "$matches"
  exit 0
fi

if [ -n "$matches" ]; then
  echo "scan-retired-models: forbidden retired model id:" >&2
  echo "$matches" >&2
  echo "" >&2
  echo "Use the supported snapshots (e.g. claude-sonnet-4-6, claude-opus-4-7)." >&2
  exit 1
fi
