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
#   --exit-code-for S T X   print the exit code for those bucket counts
#   --score-for K S T X SK U  print the summary for those bucket counts
#   base_ref           diff base (default: origin/main)
set -euo pipefail

list_only=0
tests_for_path=""
runner_for_path=""
check_guard=0
exit_code_probe=""
score_probe=""
base_ref="origin/main"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --list) list_only=1 ;;
    --tests-for) shift; tests_for_path="${1:-}" ;;
    --runner-for) shift; runner_for_path="${1:-}" ;;
    --check-guard) check_guard=1 ;;
    --exit-code-for) shift; exit_code_probe="${1:-} ${2:-} ${3:-}"; shift 2 ;;
    --score-for)
      shift
      score_probe="${1:-} ${2:-} ${3:-} ${4:-} ${5:-} ${6:-}"
      shift 5
      ;;
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

# POSIX single-quoting: close the quote, escape the apostrophe, reopen. Any
# byte survives this, which double quotes cannot promise — and a checkout
# path that breaks the runner is not a harmless failure. The runner then
# fails to start and mutmut reads that as a killed mutant, on every mutant
# of the run, so a broken path reads as a perfect score.
shell_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

test_command() {
  printf 'PATH=%s:$PATH %s -m pytest -x -q %s' \
    "$(shell_quote "$guard_dir")" \
    "$(shell_quote "$python_bin")" \
    "$(shell_quote "$(tests_for "$1")")"
}

# Built here, not inline, so the probes show exactly what a run executes.
# Quoted twice on purpose: once for the shell that runs the command, once
# more because mutmut splits the runner string with shlex before exec.
runner_for() {
  printf 'sh -c %s' "$(shell_quote "$(test_command "$1") || exit 1")"
}

# mutmut bit-ORs its statuses into the exit code (2 survived, 4 timed out,
# 8 suspicious) precisely so one run can report several at once. Returning
# only the first non-empty bucket hid the others from anything reading the
# code: a run with survivors AND timeouts came back 4, and a caller checking
# for 2 concluded nothing survived.
# AGENTS.md asks for X/Y killed, and the denominator has to be every mutant
# the run produced — not the ones that got through. A report without the
# killed bucket left the score to be assembled by hand from mutmut's
# per-file progress lines, which is exactly where a number goes wrong.
score_report() {
  local killed="$1" survived="$2" timed_out="$3" suspicious="$4" skipped="$5" untested="$6"
  local total=$((killed + survived + timed_out + suspicious + skipped + untested))
  echo "killed:     $killed / $total"
  echo "survived:   $survived"
  echo "timed out:  $timed_out"
  echo "suspicious: $suspicious"
  if [ "$skipped" -gt 0 ]; then echo "skipped:    $skipped"; fi
  if [ "$untested" -gt 0 ]; then
    echo "untested:   $untested  — the run did not measure every mutant, so the"
    echo "            score above is not over the whole surface."
  fi
}

exit_code_for() {
  local code=0
  if [ "$1" -gt 0 ]; then code=$((code | 2)); fi
  if [ "$2" -gt 0 ]; then code=$((code | 4)); fi
  if [ "$3" -gt 0 ]; then code=$((code | 8)); fi
  printf '%s' "$code"
}

# Probes: describe what a real run would do for one path, without running it.
if [ -n "$exit_code_probe" ]; then
  exit_code_for $exit_code_probe
  echo
  exit 0
fi
if [ -n "$score_probe" ]; then
  score_report $score_probe
  exit 0
fi
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
  # Expansion rather than `| head -1`, for the same SIGPIPE reason.
  echo "runner: $(runner_for "${changed%%$'\n'*}")"
  exit 0
fi

cd "$repo_root"
killed_total=0
survived_total=0
timeout_total=0
suspicious_total=0
skipped_total=0
untested_total=0

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
  # 4 timed out, 8 suspicious (mutmut/__init__.py, compute_exit_code). 1 is a
  # fatal error, and swallowing it reported a clean "survived: 0" for a run
  # that never measured anything.
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
  for bucket in killed survived timeout suspicious skipped untested; do
    if ! ids="$("$python_bin" -m mutmut result-ids "$bucket")"; then
      echo "error: mutmut result-ids $bucket failed for $file" >&2
      echo "no score for this PR — do not report one" >&2
      exit 3
    fi
    [ -z "$ids" ] && continue
    found="$(printf '%s\n' $ids | wc -w | tr -d ' ')"
    case "$bucket" in
      killed) killed_total=$((killed_total + found)) ;;
      survived) survived_total=$((survived_total + found)) ;;
      timeout) timeout_total=$((timeout_total + found)) ;;
      suspicious) suspicious_total=$((suspicious_total + found)) ;;
      skipped) skipped_total=$((skipped_total + found)) ;;
      *) untested_total=$((untested_total + found)) ;;
    esac
    # Killed mutants need no diff; the report is about what got through.
    [ "$bucket" = killed ] && continue
    printf '\n--- %s in %s\n' "$bucket" "$file" >>"$unkilled_file"
    for id in $ids; do
      printf '  mutant %s\n' "$id" >>"$unkilled_file"
      # One awk, not `grep | grep | head`: `head` closes the pipe as soon as
      # it has its lines, the producer takes SIGPIPE, and under `pipefail`
      # that killed the whole script with 141 — losing the score over a
      # survivor whose diff happened to be long. awk reads to EOF instead.
      # A failed `show` costs the diff, not the run: the id and the counts
      # are already known, so it is reported and the loop continues.
      if ! "$python_bin" -m mutmut show "$id" 2>/dev/null |
        awk '/^[-+]/ && !/^(---|\+\+\+)/ { if (++n <= 6) print "    " $0 }' \
          >>"$unkilled_file"; then
        printf '    (diff unavailable)\n' >>"$unkilled_file"
      fi
    done
  done
done <<<"$changed"

echo
echo "=== mutants that were not killed, with the change each one made"
cat "$unkilled_file"
echo
score_report "$killed_total" "$survived_total" "$timeout_total" "$suspicious_total" \
  "$skipped_total" "$untested_total"
echo
echo "None of the three were killed. A timed-out mutant hung the tests rather"
echo "than failing them, so it is a gap in the suite, not a pass."
echo
echo "Suspicious mutants were NOT killed either. mutmut files them on how long the"
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
exit "$(exit_code_for "$survived_total" "$timeout_total" "$suspicious_total")"
