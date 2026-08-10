#!/usr/bin/env bash
# PR-scoped mutation testing (issue #4).
#
# Full-repo `mutmut run` mutates all of src/lovspor/ on every review (4000+
# mutants, does not terminate in reviewable time) and release/packaging PRs
# have no mutation surface at all. This script mutates only the src/lovspor/
# Python files the branch actually changed relative to the base ref, and
# reports an explicit "not applicable" notice when there are none.
#
# Usage: scripts/mutmut-pr.sh [--list] [base_ref]
#   --list    print the scoped file list and exit without running mutmut
#   base_ref  diff base (default: origin/main)
set -euo pipefail

list_only=0
base_ref="origin/main"
for arg in "$@"; do
  case "$arg" in
    --list) list_only=1 ;;
    *) base_ref="$arg" ;;
  esac
done

if ! git rev-parse --verify --quiet "$base_ref" >/dev/null; then
  echo "error: base ref '$base_ref' not found — fetch it first" >&2
  exit 2
fi

# ACMR: added/copied/modified/renamed. Deleted files cannot be mutated.
changed="$(git diff --name-only --diff-filter=ACMR "$base_ref"...HEAD -- 'src/lovspor/' | grep '\.py$' || true)"

if [ -z "$changed" ]; then
  echo "mutation not applicable: no src/lovspor logic changed relative to $base_ref"
  echo "(report the line above verbatim as the mutation result — do not run full-repo mutmut, do not report a score)"
  exit 0
fi

count="$(printf '%s\n' "$changed" | wc -l | tr -d ' ')"
paths="$(printf '%s\n' "$changed" | paste -sd, -)"

echo "mutation scope: $count changed file(s) relative to $base_ref"
printf '%s\n' "$changed"

if [ "$list_only" -eq 1 ]; then
  exit 0
fi

# A cache from a different scope or branch silently skews the score
# (decisions.md Sprint 8 note) — every scoped run starts fresh.
rm -f .mutmut-cache

exec uv run mutmut run --paths-to-mutate="$paths"
