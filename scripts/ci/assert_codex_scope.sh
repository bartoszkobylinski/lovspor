#!/usr/bin/env bash
# Mechanical scope guard for Codex CI changes. The prompt is NOT a security
# boundary; this script is. Any file outside the allowlist => exit 1, job FAILS,
# nothing is committed or pushed.
set -euo pipefail

BASE_SHA="${1:?usage: assert_codex_scope.sh <base-sha> [tests|remediation]}"
LANE="${2:-tests}"

# Patterns are shell `case` globs (fnmatch, `*` crosses `/`), so tests/*.py
# covers every Python file in the tests/ tree. Code and fixtures only, never
# `tests/*`: that pattern let codex-mutation commit its 2,405-line run report
# as tests/mutation-remediation-report.md (issue #596). A report belongs in
# the lane's gitignored report directory and its workflow artifact.
case "$LANE" in
  tests)
    ALLOWED_PATTERNS=("tests/*.py" "tests/fixtures/*" "tests/*/fixtures/*")
    SCOPE="the test allowlist (tests/**/*.py and fixtures)"
    ;;
  remediation)
    # Killing tests are unit tests: mutants are judged by tests/unit/ alone,
    # and mutation-equivalents/ is owner-reviewed, outside the agent's scope.
    ALLOWED_PATTERNS=("tests/unit/*.py")
    SCOPE="the remediation allowlist: the remediation lane may commit tests/unit/*.py only"
    ;;
  *)
    echo "assert_codex_scope.sh: unknown lane '$LANE' (expected tests or remediation)" >&2
    exit 2
    ;;
esac

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

# Preservation contracts: snapshots, goldens and the gate modules that pin
# them. Each freezes a served surface on purpose (#958;
# owner decision: goldens are never auto-regenerated or silently updated), so
# no lane may add, modify or delete one, although tests/*.py and fixtures are
# otherwise allowed. A change to any of them is an owner-reviewed PR. Every
# entry is also a .github/CODEOWNERS line; a unit test keeps the two lists in
# step and fails when an exact path no longer exists, so a rename cannot drop
# a contract out of the guard unnoticed. New coverage for a pinned surface
# goes in a new sibling test module.
PROTECTED_PATTERNS=(
  "tests/fixtures/add_only/*"
  "tests/fixtures/mcp-existing-calls.json"
  "tests/fixtures/synthetic-flat-law.xml"
  "tests/fixtures/forarbeider/corpus/*"
  "tests/unit/fixtures/site/pre-envelope/*"
  "tests/unit/test_mcp_existing_calls.py"
  "tests/unit/test_llhb_mcp_surface.py"
  "tests/unit/test_rendering_golden.py"
  "tests/unit/test_publish_pages_golden.py"
  "tests/unit/test_forarbeider_corpus_golden.py"
  "tests/unit/test_site_routes.py"
  "tests/unit/test_agentic_ci_scripts.py"
)

protected=()
while IFS= read -r f; do
  [ -z "$f" ] && continue
  for pat in "${PROTECTED_PATTERNS[@]}"; do
    # shellcheck disable=SC2254
    case "$f" in
      $pat) protected+=("$f"); break ;;
    esac
  done
done <<< "$changed"

if [ "${#protected[@]}" -gt 0 ]; then
  echo "SCOPE VIOLATION — Codex touched protected preservation contracts (snapshots, goldens, gate modules); refusing to commit:" >&2
  printf '  %s\n' "${protected[@]}" >&2
  echo "Put new coverage in a new sibling test module; changing a pinned contract is an owner-reviewed PR." >&2
  exit 1
fi

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
  echo "SCOPE VIOLATION — Codex touched files outside $SCOPE; refusing to commit:" >&2
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
  # One path per line, quoted. Unquoted expansion splits on IFS, so a filename
  # containing a space was reported as several nonexistent paths — and an
  # operator who cannot copy the name out of this message cannot act on it.
  while IFS= read -r path; do printf '  %s\n' "$path" >&2; done <<< "$deleted"
  exit 1
fi

# Rewriting an existing assertion stays ALLOWED. On PR #161 the agent tightened
# one, and failing that would have painted a good round as an implementation
# problem — the confusion this guard exists to prevent. But a weakened assertion
# and a correct new test both leave this job green, so the rewrite is reported
# where a human reads it rather than left to be noticed.
# Formatted inside awk, on tab-separated fields: the path must never pass
# through shell word splitting. Pairing a count with a path by splitting would
# not merely mangle a spaced path, it would print a plausible line count
# against a filename that does not exist.
rewritten="$(git diff --no-renames --numstat "$BASE_SHA" -- 'tests/*' \
  | awk -F'\t' '$2 ~ /^[0-9]+$/ && $2 > 0 {print "  -" $2 " line(s)  " $3}')"
if [ -n "$rewritten" ]; then
  {
    echo "REWRITTEN TESTS — lines were removed from test files that existed at $BASE_SHA."
    echo "Read the diff: this guard cannot tell a tightened assertion from a gutted one."
    echo "$rewritten"
  } | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
fi

echo "scope guard OK ($(printf '%s\n' "$changed" | grep -c . || true) changed file(s), all allowed)"
