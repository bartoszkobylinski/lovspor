#!/usr/bin/env bash
# PR-scoped mutation testing (issue #4).
#
# Full-repo `mutmut run` mutates all of src/lovspor/ on every review (4000+
# mutants, does not terminate in reviewable time) and release/packaging PRs
# have no mutation surface at all. This script mutates only the src/lovspor/
# Python files the branch actually changed relative to the base ref, and
# reports an explicit "not applicable" notice when there are none.
#
# Scoping the mutants was not enough: the configured runner ran the whole
# unit suite for every mutant (~32 s each), so a 560-mutant PR needed hours
# and reviewers abandoned it part-way. Each file is therefore mutated
# against its own test module (~0.6-5 s), which is what makes the score
# obtainable at all. Two consequences, both deliberate:
#
#   * A mutant that only a test in some other module would have caught is
#     reported as survived. That direction is safe - the score can read
#     worse than reality, never better - but a survivor is worth checking
#     against the wider suite before treating it as a real gap.
#   * A changed file with no matching test module falls back to the whole
#     unit suite rather than silently running nothing, which would score
#     every one of its mutants as survived.
#
# The runner calls .venv/bin/python directly. `uv run` inside a runner
# re-resolves the environment per mutant and has produced a clean sweep
# that was an artifact of the tooling rather than of the tests.
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

repo_root="$(git rev-parse --show-toplevel)"
python_bin="$repo_root/.venv/bin/python"
if [ ! -x "$python_bin" ]; then
  echo "error: no interpreter at $python_bin — run ./scripts/bootstrap.sh" >&2
  exit 2
fi

# src/lovspor/llhb/fairness.py -> tests/unit/test_llhb_fairness.py
# src/lovspor/mcp.py           -> tests/unit/test_mcp.py
tests_for() {
  local module="${1#src/lovspor/}"
  local candidate="tests/unit/test_${module%.py}.py"
  candidate="${candidate//\//_}"
  candidate="tests/unit/${candidate#tests_unit_}"
  if [ -f "$repo_root/$candidate" ]; then
    printf '%s' "$candidate"
  else
    printf 'tests/unit/'
  fi
}

# ACMR: added/copied/modified/renamed. Deleted files cannot be mutated.
changed="$(git diff --name-only --diff-filter=ACMR "$base_ref"...HEAD -- 'src/lovspor/' | grep '\.py$' || true)"

if [ -z "$changed" ]; then
  echo "mutation not applicable: no src/lovspor logic changed relative to $base_ref"
  echo "(report the line above verbatim as the mutation result — do not run full-repo mutmut, do not report a score)"
  exit 0
fi

count="$(printf '%s\n' "$changed" | wc -l | tr -d ' ')"

echo "mutation scope: $count changed file(s) relative to $base_ref"
while IFS= read -r file; do
  printf '  %s -> %s\n' "$file" "$(tests_for "$file")"
done <<<"$changed"

if [ "$list_only" -eq 1 ]; then
  exit 0
fi

cd "$repo_root"
status=0
survivors_file="$(mktemp)"
trap 'rm -f "$survivors_file"' EXIT

while IFS= read -r file; do
  tests="$(tests_for "$file")"
  echo
  echo "=== mutating $file against $tests"
  # Fresh cache per file, not just per run. A cache from a different
  # scope or branch skews the score (decisions.md Sprint 8 note), and
  # the cache also carries the measured baseline test time: reusing one
  # across modules times every mutant of a slow module against a fast
  # module's baseline, which files them as "suspicious" — neither killed
  # nor survived, and useless as a score.
  rm -f "$repo_root/.mutmut-cache"
  # `|| exit 1`: mutmut reads "tests passed" as `returncode != 1`, so a
  # mutant that breaks the module outright makes pytest exit 2 (collection
  # error) and gets filed as SURVIVED. Collapsing every failure onto 1
  # scores those as killed, which is what a suite that refuses to run
  # actually means.
  "$python_bin" -m mutmut run \
    --paths-to-mutate="$file" \
    --runner="sh -c '$python_bin -m pytest -x -q $tests || exit 1'" || status=$?
  # Dumped now, while this file's cache is the live one: mutant ids are
  # per-run indices, so `mutmut show` cannot resolve an earlier file's id
  # once the next file has replaced the cache.
  printf '\n--- survivors in %s\n' "$file" >>"$survivors_file"
  for id in $("$python_bin" -m mutmut result-ids survived 2>/dev/null || true); do
    printf '  mutant %s\n' "$id" >>"$survivors_file"
    "$python_bin" -m mutmut show "$id" 2>/dev/null |
      grep -E '^[-+]' | grep -vE '^(---|\+\+\+)' | head -6 | sed 's/^/    /' >>"$survivors_file"
  done
done <<<"$changed"

echo
echo "=== survived mutants, with the change each one made"
cat "$survivors_file"
echo
echo "Read the per-file tallies above for the score. Each file was mutated"
echo "against its own tests, so a mutant another module's test would kill"
echo "reads as survived: the score errs pessimistic, never optimistic."
exit "$status"
