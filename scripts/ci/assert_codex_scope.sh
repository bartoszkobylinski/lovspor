#!/usr/bin/env bash
# Mechanical scope guard for Codex CI changes. The prompt is NOT a security
# boundary; this script is. Any file outside the allowlist => exit 1, job FAILS,
# nothing is committed or pushed.
set -euo pipefail

BASE_SHA="${1:?usage: assert_codex_scope.sh <base-sha>}"

# Codex may touch test files only. Patterns are shell `case` globs (fnmatch,
# `*` crosses `/`), so tests/* covers the whole tests/ tree.
ALLOWED_PATTERNS=(
  "tests/*"
)

# Committed range + staged + unstaged + untracked: the guard holds no matter
# whether Codex committed its edits or left them in the working tree.
changed="$(
  {
    git diff --name-only "$BASE_SHA"..HEAD
    git diff --name-only
    git diff --name-only --cached
    git ls-files --others --exclude-standard
  } | sort -u
)"

violations=()
while IFS= read -r f; do
  [ -z "$f" ] && continue
  ok=false
  for pat in "${ALLOWED_PATTERNS[@]}"; do
    # shellcheck disable=SC2254
    case "$f" in
      $pat) ok=true; break ;;
    esac
  done
  "$ok" || violations+=("$f")
done <<< "$changed"

if [ "${#violations[@]}" -gt 0 ]; then
  echo "SCOPE VIOLATION — Codex touched files outside the test allowlist:" >&2
  printf '  %s\n' "${violations[@]}" >&2
  exit 1
fi

# The allowlist is about WHICH files changed; it says nothing about HOW. A
# deleted test still matches tests/*, so removal of a test the agent did not
# write passes the check above untouched (issue #162). Rename detection is off
# on purpose: moving a human test out from under the name its failures are
# reported by is the same loss as deleting it.
deleted="$(git diff --no-renames --diff-filter=D --name-only "$BASE_SHA" -- 'tests/*')"
if [ -n "$deleted" ]; then
  echo "SCOPE VIOLATION — Codex removed test files that existed at $BASE_SHA:" >&2
  printf '  %s\n' $deleted >&2
  exit 1
fi

# Rewriting an existing assertion stays ALLOWED. On PR #161 the agent tightened
# one, and failing that would have painted a good round as an implementation
# problem — the confusion this guard exists to prevent. But a weakened assertion
# and a correct new test both leave this job green, so the rewrite is reported
# where a human reads it rather than left to be noticed.
rewritten="$(git diff --no-renames --numstat "$BASE_SHA" -- 'tests/*' | awk '$2 ~ /^[0-9]+$/ && $2 > 0 {print $2 "\t" $3}')"
if [ -n "$rewritten" ]; then
  {
    echo "REWRITTEN TESTS — lines were removed from test files that existed at $BASE_SHA."
    echo "Read the diff: this guard cannot tell a tightened assertion from a gutted one."
    printf '  -%s line(s)  %s\n' $rewritten
  } | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
fi

echo "scope guard OK ($(printf '%s\n' "$changed" | grep -c . || true) changed file(s), all allowed)"
