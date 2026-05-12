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
  '\bautopilot\s+run\b'
)

# Literal substrings that look like forbidden tokens but are legitimate
# repo-local references. Stripped from the haystack before pattern matching
# so the bare-word form of the underlying token still trips. Empty today;
# kept for future additions.
ALLOWED_LITERALS=()

# Files exempt from scanning. These either *define* the forbidden-token
# vocabulary (and so must reference it) or are external documents brought
# into the repo whose wording is not authored by us. Architecture-review
# summaries cite upstream library versions (``v0.1.71`` etc.) which match
# the bare-word ``v0.x.y`` pattern; their job is to record the upstream
# release picture, so they're exempt as a class.
EXEMPT_PATHSPECS=(
  ':!scripts/scan-leakage.sh'
  ':!docs/adr/0015-commit-hygiene-conventional-commits.md'
  ':!docs/adr/README.md'
  ':!docs/architecture-reviews/'
  ':!CONTRIBUTING.md'
)

added_diff() {
  # Lines added in the staged diff, with their unified-diff prefix stripped.
  # Exempt files (the scanner itself, the meta-ADRs that define the
  # forbidden vocabulary) are excluded by pathspec.
  git diff --cached --no-color --unified=0 -- "${EXEMPT_PATHSPECS[@]}" \
    | awk '/^\+\+\+ /{next} /^\+/{sub(/^\+/, ""); print}'
}

commit_msg_text() {
  # Only read a commit-message file when one is explicitly passed in (the
  # commit-msg hook contract). Reading .git/COMMIT_EDITMSG unconditionally
  # would scan the *previous* commit on every manual run, which is the wrong
  # input.
  if [ "$#" -ge 1 ] && [ -f "$1" ]; then
    cat "$1"
  fi
}

haystack="$(added_diff)
$(commit_msg_text "$@")"

# Strip allowed literals so legitimate filename references don't trip the
# bare-word patterns. After this, what remains is narrative text and the
# patterns can be applied without false positives.
filtered="$haystack"
for literal in "${ALLOWED_LITERALS[@]+"${ALLOWED_LITERALS[@]}"}"; do
  filtered="${filtered//${literal}/}"
done

failures=0
for pattern in "${PATTERNS[@]}"; do
  matches="$(printf '%s\n' "$filtered" | grep -inE "$pattern" || true)"
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
