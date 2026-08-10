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
#     unit suite (tests/unit/, what the runner used before this change)
#     rather than silently running nothing, which would score every one of
#     its mutants as survived.
#
# The runner calls .venv/bin/python directly. `uv run` inside a runner
# re-resolves the environment per mutant and has produced a clean sweep
# that was an artifact of the tooling rather than of the tests.
#
# Usage: scripts/mutmut-pr.sh [--list] [base_ref]
#   --list             print the scoped file list and runner, run nothing
#   --tests-for PATH   print the test module PATH would be mutated against
#   --runner-for PATH  print the exact runner command PATH would be mutated with
#   --check-guard      run the PATH stub and confirm it refuses to execute
#   base_ref           diff base (default: origin/main)
set -euo pipefail

list_only=0
tests_for_path=""
runner_for_path=""
check_guard=0
base_ref="origin/main"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --list) list_only=1 ;;
    --tests-for) shift; tests_for_path="${1:-}" ;;
    --runner-for) shift; runner_for_path="${1:-}" ;;
    --check-guard) check_guard=1 ;;
    *) base_ref="$1" ;;
  esac
  shift
done

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

# A mutant that breaks the orchestrator's env isolation makes the child
# inherit this process's PATH and launch the REAL provider CLI: measured
# once, a single such mutant spent 36 s on a live model call billed to the
# subscription. Shadowing `claude` with a stub that exits non-zero costs
# nothing when isolation holds (the tests put their own fake first via
# hermetic_env) and turns that mutant into a fast, clean kill. Prepending
# rather than removing a PATH entry keeps git and sh reachable.
guard_dir="$(mktemp -d)"
unkilled_file="$(mktemp)"
resolve_tree="$guard_dir/resolve-at-head"
# One trap for both: a second `trap ... EXIT` replaces the first rather than
# adding to it, which used to leave the stub directory behind on every run.
# INT and TERM are trapped too, so an interrupted run cleans up as well.
cleanup() {
  [ -d "$resolve_tree" ] && git -C "$repo_root" worktree remove --force "$resolve_tree" \
    >/dev/null 2>&1
  git -C "$repo_root" worktree prune >/dev/null 2>&1 || true
  rm -rf "$guard_dir"
  rm -f "$unkilled_file"
}
trap cleanup EXIT INT TERM
printf '#!/bin/sh\necho "mutation guard: the real provider CLI is blocked" >&2\nexit 127\n' \
  >"$guard_dir/claude"
chmod +x "$guard_dir/claude"

# The runner is built here, not inline, so the probes can show exactly
# what a real run would execute.
test_command() {
  printf 'PATH=%s:$PATH %s -m pytest -x -q %s' "$guard_dir" "$python_bin" "$(tests_for "$1")"
}

runner_for() {
  # `|| exit 1`: mutmut reads "tests passed" as `returncode != 1`, so a
  # mutant that breaks the module outright makes pytest exit 2 (collection
  # error) and gets filed as SURVIVED. Collapsing every failure onto 1
  # scores those as killed, which is what a suite that refuses to run
  # actually means.
  printf "sh -c '%s || exit 1'" "$(test_command "$1")"
}

# mutmut files a mutant as "suspicious" on how long the tests took, not on
# whether they failed, so the verdict moves with machine load and leaves the
# score unsettled. Re-running each one and reading its exit code turns a
# timing observation back into an outcome.
#
# The re-run happens in a throwaway worktree at HEAD, never in the tree you
# are working in. Resolving means applying a mutant to a source file, and
# doing that in place put the reviewer's work one interrupted script away
# from a mutated or reverted file. Here nothing outside the worktree is
# written, so an interrupt, a concurrent edit or a dirty checkout cost
# nothing. PYTHONPATH points imports at the copy; it precedes the editable
# install on sys.path, which is what makes the copy the code under test.
resolve_suspicious() {
  local file="$1" ids="$2" id verdict
  if [ ! -d "$resolve_tree" ] && ! setup_resolve_tree; then
    printf '  (cannot resolve: no throwaway worktree — left as suspicious)\n' >>"$unkilled_file"
    return
  fi
  cp "$repo_root/.mutmut-cache" "$resolve_tree/.mutmut-cache"
  for id in $ids; do
    if ! (cd "$resolve_tree" && "$python_bin" -m mutmut apply "$id") >/dev/null 2>&1; then
      printf '  mutant %s: could not be applied, still unresolved\n' "$id" >>"$unkilled_file"
      continue
    fi
    if (cd "$resolve_tree" && sh -c "PYTHONPATH=$resolve_tree/src $(test_command "$file")") \
      >/dev/null 2>&1; then verdict=SURVIVED; else verdict=killed; fi
    (cd "$resolve_tree" && git checkout -- "$file")
    printf '  mutant %s: re-run says %s\n' "$id" "$verdict" >>"$unkilled_file"
    if [ "$verdict" = SURVIVED ]; then
      resolved_survived=$((resolved_survived + 1))
    else
      resolved_killed=$((resolved_killed + 1))
    fi
  done
}

setup_resolve_tree() {
  git -C "$repo_root" worktree add --detach -q "$resolve_tree" HEAD >/dev/null 2>&1
}

# Probes: describe what a real run would do for one path, without running it.
if [ -n "${tests_for_path:-}" ]; then
  tests_for "$tests_for_path"
  echo
  exit 0
fi
if [ -n "${runner_for_path:-}" ]; then
  runner_for "$runner_for_path"
  echo
  exit 0
fi
if [ "$check_guard" -eq 1 ]; then
  # Exercised here rather than inspected from outside: the stub lives in a
  # temp directory this script removes on exit, and what matters is that it
  # runs and refuses, not that a path exists.
  guard_status=0
  "$guard_dir/claude" >/dev/null 2>&1 || guard_status=$?
  if [ "$guard_status" -eq 0 ]; then
    echo "guard: FAILS OPEN — the stub exited 0, a real CLI call would go through"
    exit 1
  fi
  echo "guard: blocks the real provider CLI (stub exited $guard_status)"
  exit 0
fi

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

echo "mutation scope: $count changed file(s) relative to $base_ref"
while IFS= read -r file; do
  printf '  %s -> %s\n' "$file" "$(tests_for "$file")"
done <<<"$changed"

if [ "$list_only" -eq 1 ]; then
  echo "runner: $(runner_for "$(printf '%s\n' "$changed" | head -1)")"
  exit 0
fi

cd "$repo_root"

# Resolution measures HEAD in a throwaway worktree, so it is independent of
# whatever is currently uncommitted here — and cannot damage it.
resolved_killed=0
resolved_survived=0
survived_total=0
suspicious_total=0

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
  "$python_bin" -m mutmut run \
    --paths-to-mutate="$file" \
    --runner="$(runner_for "$file")" || true
  # Dumped now, while this file's cache is the live one: mutant ids are
  # per-run indices, so `mutmut show` cannot resolve an earlier file's id
  # once the next file has replaced the cache. Suspicious mutants are
  # dumped alongside survived ones: neither was killed, and a summary
  # that lists only survivors reports more certainty than the run has.
  for bucket in survived suspicious; do
    ids="$("$python_bin" -m mutmut result-ids "$bucket" 2>/dev/null || true)"
    [ -z "$ids" ] && continue
    found=$(printf '%s\n' $ids | wc -w | tr -d ' ')
    if [ "$bucket" = survived ]; then
      survived_total=$((survived_total + found))
    else
      suspicious_total=$((suspicious_total + found))
    fi
    printf '\n--- %s in %s\n' "$bucket" "$file" >>"$unkilled_file"
    for id in $ids; do
      printf '  mutant %s\n' "$id" >>"$unkilled_file"
      "$python_bin" -m mutmut show "$id" 2>/dev/null |
        grep -E '^[-+]' | grep -vE '^(---|\+\+\+)' | head -6 | sed 's/^/    /' >>"$unkilled_file"
    done
    if [ "$bucket" = suspicious ]; then
      printf '  resolving by re-run (suspicious is a timing verdict, not an outcome):\n' \
        >>"$unkilled_file"
      resolve_suspicious "$file" "$ids"
    fi
  done
done <<<"$changed"

echo
echo "=== mutants that were not killed, with the change each one made"
echo "(suspicious ones were not killed either — do not count them as passes)"
cat "$unkilled_file"
unresolved=$((suspicious_total - resolved_killed - resolved_survived))
echo
echo "=== corrected totals, after re-running every suspicious mutant"
echo "survived (from the tallies above):        $survived_total"
echo "suspicious, re-run says killed:           $resolved_killed"
echo "suspicious, re-run says SURVIVED:         $resolved_survived"
echo "suspicious, still unresolved:             $unresolved"
echo
echo "The per-file tallies above are mutmut's raw verdicts; the line above is"
echo "what they mean after resolution. Each file was mutated against its own"
echo "tests, so a mutant another module's test would kill reads as survived:"
echo "the score errs pessimistic, never optimistic."

# mutmut's own exit code still carries the pre-resolution suspicious bit, so
# it is recomputed here rather than passed through.
if [ "$unresolved" -gt 0 ]; then
  exit 8
elif [ $((survived_total + resolved_survived)) -gt 0 ]; then
  exit 2
fi
exit 0
