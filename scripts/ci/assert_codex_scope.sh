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

echo "scope guard OK ($(printf '%s\n' "$changed" | grep -c . || true) changed file(s), all allowed)"
