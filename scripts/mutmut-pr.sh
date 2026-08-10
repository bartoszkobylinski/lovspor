#!/usr/bin/env bash
# PR-scoped mutation testing (issue #4).
#
# Full-repo `mutmut run` mutates all of src/lovspor/ on every review (4000+
# mutants, does not terminate in reviewable time) and release/packaging PRs
# have no mutation surface at all. This script mutates only the src/lovspor/
# Python files the branch actually changed relative to the base ref, and
# reports an explicit "not applicable" notice when there are none.
#
# Scoping the mutants was not enough. Three things made the score either
# unobtainable or wrong, each found by running it rather than reading it:
#
#   * The configured runner ran the whole unit suite per mutant (~32 s), so a
#     600-mutant PR needed hours and reviews abandoned it part-way. Each file
#     is mutated against its own test module instead (~0.6-5 s).
#   * mutmut reads "tests passed" as `returncode != 1`, so a mutant that
#     breaks the module makes pytest exit 2 (a collection error) and was
#     filed as SURVIVED. The runner collapses every failure onto 1.
#   * The cache carries the measured baseline test time, so one cache shared
#     across modules timed every mutant of a slow module against a fast
#     module's baseline and filed it "suspicious". Fresh cache per file.
#
# Two consequences of the per-module scoping, both deliberate:
#
#   * A mutant that only a test in some other module would have caught is
#     reported as survived. That direction is safe - the score can read worse
#     than reality, never better - but a survivor is worth checking against
#     the wider suite before treating it as a real gap.
#   * A changed file with no matching test module falls back to tests/unit/,
#     the whole unit suite, rather than silently running nothing.
#
# The runner calls .venv/bin/python directly. `uv run` inside a runner
# re-resolves the environment per mutant and has produced a clean sweep that
# was an artifact of the tooling rather than of the tests.
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

# A mutant that breaks a subprocess call's environment isolation makes the
# child inherit this process's PATH and launch the REAL provider CLI:
# measured once, a single such mutant spent 36 s on a live model call billed
# to the subscription. Shadowing `claude` with a stub that exits non-zero
# costs nothing when isolation holds (the tests put their own fake first) and
# turns that mutant into a fast, clean kill. Prepending rather than removing
# a PATH entry keeps git and sh reachable.
guard_dir="$(mktemp -d)"
unkilled_file="$(mktemp)"
# One trap for both, and on interruption as well: a second `trap ... EXIT`
# replaces the first rather than adding to it, which used to leak the stub.
trap 'rm -rf "$guard_dir"; rm -f "$unkilled_file"' EXIT INT TERM
printf '#!/bin/sh\necho "mutation guard: the real provider CLI is blocked" >&2\nexit 127\n' \
  >"$guard_dir/claude"
chmod +x "$guard_dir/claude"

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

# Built here, not inline, so the probes show exactly what a run executes.
runner_for() {
  # Every interpolated path is quoted inside the inner command: a checkout
  # under a directory with a space otherwise splits into two words, the
  # runner fails to start, and mutmut reads that failure as a killed mutant
  # — a false kill on every single mutant of the run.
  printf "sh -c 'PATH=\"%s:\$PATH\" \"%s\" -m pytest -x -q \"%s\" || exit 1'" \
    "$guard_dir" "$python_bin" "$(tests_for "$1")"
}

# Probes: describe what a real run would do for one path, without running it.
if [ -n "$tests_for_path" ]; then
  tests_for "$tests_for_path"
  echo
  exit 0
fi
if [ -n "$runner_for_path" ]; then
  runner_for "$runner_for_path"
  echo
  exit 0
fi
if [ "$check_guard" -eq 1 ]; then
  # Exercised rather than inspected: the stub lives in a temp directory this
  # script removes on exit, and what matters is that it runs and refuses.
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
survived_total=0
suspicious_total=0

while IFS= read -r file; do
  echo
  echo "=== mutating $file against $(tests_for "$file")"
  # Fresh cache per file, not just per run: a cache from another scope skews
  # the score (decisions.md Sprint 8 note), and it also carries the baseline
  # time this file's mutants have to be measured against.
  rm -f "$repo_root/.mutmut-cache"
  run_status=0
  "$python_bin" -m mutmut run \
    --paths-to-mutate="$file" \
    --runner="$(runner_for "$file")" || run_status=$?
  # mutmut encodes its verdicts in the exit code as a bitfield: 2 survived,
  # 4 untested, 8 suspicious. Any other non-zero status means mutmut itself
  # failed, and swallowing that reported a clean "survived: 0, suspicious: 0"
  # for a run that never measured anything.
  case "$run_status" in
    0 | 2 | 4 | 6 | 8 | 10 | 12 | 14) : ;;
    *)
      echo "error: mutmut run failed on $file (exit $run_status)" >&2
      echo "no score for this PR — do not report one" >&2
      exit 3
      ;;
  esac
  # Dumped now, while this file's cache is the live one: mutant ids are
  # per-run indices, so `mutmut show` cannot resolve an earlier file's id
  # once the next file has replaced the cache. Suspicious mutants are dumped
  # alongside survivors because neither was killed, and a summary listing
  # only survivors claims more certainty than the run has.
  for bucket in survived suspicious; do
    if ! ids="$("$python_bin" -m mutmut result-ids "$bucket")"; then
      echo "error: mutmut result-ids $bucket failed for $file" >&2
      echo "no score for this PR — do not report one" >&2
      exit 3
    fi
    [ -z "$ids" ] && continue
    found="$(printf '%s\n' $ids | wc -w | tr -d ' ')"
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
  done
done <<<"$changed"

echo
echo "=== mutants that were not killed, with the change each one made"
cat "$unkilled_file"
echo
echo "survived:   $survived_total"
echo "suspicious: $suspicious_total"
echo
echo "Suspicious mutants were NOT killed. mutmut files them on how long the"
echo "tests took, not on whether they failed, so the verdict moves with machine"
echo "load — never fold them into the killed count. To settle one, in a clean"
echo "checkout: mutmut apply <id>, run the runner printed above, read its exit"
echo "code, then git checkout -- <file>."
echo
echo "Each file was mutated against its own tests, so a mutant another module's"
echo "test would kill reads as survived: the score errs pessimistic, never"
echo "optimistic."

# Recomputed rather than passed through: mutmut's exit code is per-file, so
# the last file's would silently stand in for the whole run.
if [ "$suspicious_total" -gt 0 ]; then
  exit 8
elif [ "$survived_total" -gt 0 ]; then
  exit 2
fi
exit 0
