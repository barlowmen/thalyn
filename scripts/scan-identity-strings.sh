#!/usr/bin/env bash
# Block user-facing strings that name the brain as "the brain" / "the
# assistant" / "the agent" / "the AI". The brain is named **Thalyn**
# (per 01-requirements.md F1.2 + memory:project_brain_name) — UI copy
# uses that name. Code comments are exempt; this scan only catches
# strings the user can actually see.
#
# Heuristic for "comment": the line's first non-whitespace character is
# `*` (block-comment continuation), `//` (line comment), or `/*` (block
# open). For our TSX/TS codebase that maps cleanly onto JSDoc + line
# comments; a pathological multi-line comment with arbitrary content on
# a non-`*` line would slip through, but we don't have that pattern.
#
# Usage:
#   scripts/scan-identity-strings.sh         # exit 1 on any match
#   scripts/scan-identity-strings.sh --list  # print matches, exit 0

set -euo pipefail

PATTERNS=(
  '\bthe brain\b'
  '\bthe assistant\b'
  '\bthe agent\b'
  '\bthe AI\b'
)

# Restrict to renderer source files. Storybook stories that test
# strings from the Thalyn-renamed components stay in scope so the
# stories' rendered text matches the components.
ROOTS=(
  src/components
  src/lib
  src/pages
  src/app
)

declare -a EXISTING_ROOTS=()
for root in "${ROOTS[@]}"; do
  if [ -d "$root" ]; then
    EXISTING_ROOTS+=("$root")
  fi
done

if [ ${#EXISTING_ROOTS[@]} -eq 0 ]; then
  exit 0
fi

joined_pattern=""
for pattern in "${PATTERNS[@]}"; do
  if [ -z "$joined_pattern" ]; then
    joined_pattern="$pattern"
  else
    joined_pattern="$joined_pattern|$pattern"
  fi
done

# Find every match, then drop comment lines. The Awk-based filter
# isolates the line content from the `path:lineno:line` shape and
# checks whether the first non-whitespace character starts a comment.
matches=$(grep -rEn \
  --include='*.tsx' \
  --include='*.ts' \
  --include='*.jsx' \
  --include='*.js' \
  -- "$joined_pattern" \
  "${EXISTING_ROOTS[@]}" \
  | awk -F':' '{
      content = ""
      for (i = 3; i <= NF; i++) {
        if (i > 3) content = content ":"
        content = content $i
      }
      sub(/^[ \t]+/, "", content)
      if (content !~ /^\*/ && content !~ /^\/\*/ && content !~ /^\/\//) {
        print
      }
    }' \
  || true)

if [ "${1:-}" = "--list" ]; then
  echo "$matches"
  exit 0
fi

if [ -n "$matches" ]; then
  echo "scan-identity-strings: forbidden user-facing identity tokens:" >&2
  echo "$matches" >&2
  echo "" >&2
  echo "Use 'Thalyn' in user-visible copy. Comments may keep 'the brain'." >&2
  exit 1
fi
