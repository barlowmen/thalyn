#!/usr/bin/env bash
# Block additions that contain internal-workflow language.
#
# Runs in three contexts:
#   1. As a pre-commit framework hook (scans the staged diff).
#   2. As a commit-msg hook (scans the commit message file passed as $1).
#   3. As a manual invocation (scans whatever is staged + COMMIT_EDITMSG if present).
#
# Exit 0 = clean. Exit 1 = forbidden token found.

set -euo pipefail

PATTERNS=(
  '\bphase\s*[0-9]+\b'
  '\bv0\.[0-9]+\.[0-9]+\b'
  '\bcommit\s+[0-9]+\s+of\s+[0-9]+\b'
  '\bprompt[- ]?plan\b'
  '\bas\s+instructed\b'
  '\bper\s+the\s+prompt\b'
  '\biteration\s+[0-9]+\b'
  '\bworking\s+name\b'
  '\bspock\b'
  'Co-Authored-By:\s*Claude'
  '\bclaude\s+code\b'
  '\bautopilot\s+run\b'
)

added_diff() {
  # Lines added in the staged diff, with their unified-diff prefix stripped.
  # The scanner itself is excluded — it defines the forbidden tokens by
  # construction and would otherwise trip on its own diff.
  git diff --cached --no-color --unified=0 -- \
      ':!scripts/scan-leakage.sh' \
    | awk '/^\+\+\+ /{next} /^\+/{sub(/^\+/, ""); print}'
}

commit_msg_text() {
  # 1) Explicit commit-msg path passed by git's commit-msg hook.
  if [ "$#" -ge 1 ] && [ -f "$1" ]; then
    cat "$1"
    return
  fi
  # 2) Default location used during a normal commit.
  if [ -f .git/COMMIT_EDITMSG ]; then
    cat .git/COMMIT_EDITMSG
  fi
}

haystack="$(added_diff)
$(commit_msg_text "$@")"

failures=0
for pattern in "${PATTERNS[@]}"; do
  matches="$(printf '%s\n' "$haystack" | grep -inE "$pattern" || true)"
  if [ -n "$matches" ]; then
    printf 'Forbidden pattern matched: /%s/\n' "$pattern" >&2
    printf '%s\n' "$matches" | head -5 >&2
    failures=$((failures + 1))
  fi
done

if [ "$failures" -gt 0 ]; then
  printf '\nLeakage scan blocked %d pattern(s). Refine wording or update scripts/scan-leakage.sh if a hit is a false positive.\n' \
    "$failures" >&2
  exit 1
fi
