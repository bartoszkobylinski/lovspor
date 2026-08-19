#!/usr/bin/env bash
# PR-scoped mutation testing on Mutmut 3.x.
#
# Mutmut 3 builds one shadow tree, records which tests exercise each function,
# and then runs only those tests for each mutant. The PR scope is the changed
# functions, not every function in a changed module.
set -euo pipefail

list_only=0
patterns_for_path=""
check_guard=0
exit_code_probe=""
score_probe=""
base_ref="origin/main"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --list) list_only=1 ;;
    --patterns-for) shift; patterns_for_path="${1:-}" ;;
    --check-guard) check_guard=1 ;;
    --exit-code-for)
      shift
      exit_code_probe="${1:-} ${2:-} ${3:-} ${4:-0}"
      if [ "$#" -ge 4 ]; then shift 3; else shift 2; fi
      ;;
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
mutmut_bin="$repo_root/.venv/bin/mutmut"
if [ ! -x "$mutmut_bin" ]; then
  echo "error: no mutmut at $mutmut_bin — run 'uv sync' first" >&2
  exit 2
fi

pattern_for() {
  local module="${1#src/}"
  module="${module%.py}"
  printf '%s.*' "${module//\//.}"
}

score_report() {
  local killed="$1" survived="$2" timed_out="$3" suspicious="$4" skipped="$5" untested="$6"
  local total=$((killed + survived + timed_out + suspicious + skipped + untested))
  echo "killed:     $killed / $total"
  echo "survived:   $survived"
  echo "timed out:  $timed_out"
  echo "suspicious: $suspicious"
  if [ "$skipped" -gt 0 ]; then echo "skipped:    $skipped"; fi
  if [ "$untested" -gt 0 ]; then
    echo "untested:   $untested  — the score is not over the whole surface"
  fi
}

exit_code_for() {
  local code=0
  if [ "$1" -gt 0 ]; then code=$((code | 2)); fi
  if [ "$2" -gt 0 ]; then code=$((code | 4)); fi
  if [ "$3" -gt 0 ]; then code=$((code | 8)); fi
  if [ "${4:-0}" -gt 0 ]; then code=$((code | 16)); fi
  printf '%s' "$code"
}

guard_dir="$(mktemp -d)"
trap 'rm -rf "$guard_dir"' EXIT INT TERM
printf '#!/bin/sh\necho "mutation guard: the real provider CLI is blocked" >&2\nexit 127\n' \
  >"$guard_dir/claude"
chmod +x "$guard_dir/claude"

if [ -n "$exit_code_probe" ]; then
  exit_code_for $exit_code_probe
  echo
  exit 0
fi
if [ -n "$score_probe" ]; then
  score_report $score_probe
  exit 0
fi
if [ -n "$patterns_for_path" ]; then
  pattern_for "$patterns_for_path"
  echo
  exit 0
fi
if [ "$check_guard" -eq 1 ]; then
  guard_status=0
  "$guard_dir/claude" >/dev/null 2>&1 || guard_status=$?
  if [ "$guard_status" -eq 0 ]; then
    echo "guard: FAILS OPEN"
    exit 1
  fi
  echo "guard: blocks the real provider CLI (stub exited $guard_status)"
  exit 0
fi

if ! git rev-parse --verify --quiet "$base_ref" >/dev/null; then
  echo "error: base ref '$base_ref' not found — fetch it first" >&2
  exit 2
fi

changed="$(git diff --name-only --diff-filter=ACMR "$base_ref"...HEAD -- 'src/lovspor/' | grep '\.py$' || true)"
if [ -z "$changed" ]; then
  echo "mutation not applicable: no src/lovspor logic changed relative to $base_ref"
  exit 0
fi

count="$(printf '%s\n' "$changed" | wc -l | tr -d ' ')"
echo "mutation scope: $count changed file(s) relative to $base_ref"

patterns=()
while IFS= read -r pattern; do
  [ -n "$pattern" ] && patterns+=("$pattern")
done < <("$python_bin" "$repo_root/scripts/ci/mutation_scope.py" --base "$base_ref" --explain)

if [ "${#patterns[@]}" -eq 0 ]; then
  echo "mutation not applicable: $count changed file(s), but no mutatable function changed relative to $base_ref"
  exit 0
fi
echo "scoped to ${#patterns[@]} mutation target(s)"
if [ "$list_only" -eq 1 ]; then
  exit 0
fi

cd "$repo_root"
rm -rf mutants
mkdir -p mutants/data
ln -sfn "../.venv" "mutants/.venv"

file_budget="${MUTMUT_PR_FILE_BUDGET_SECONDS:-1200}"
total_budget=$((file_budget * count))
max_children="${MUTMUT_PR_MAX_CHILDREN:-4}"
timeout_bin="$(command -v timeout || true)"
run_status=0
if [ -n "$timeout_bin" ]; then
  PATH="$guard_dir:$PATH" "$timeout_bin" --signal=TERM --kill-after=30 "$total_budget" \
    "$mutmut_bin" run "${patterns[@]}" --max-children "$max_children" \
    2>&1 | tee mutation-run.log || run_status=${PIPESTATUS[0]}
else
  echo "warning: no timeout(1) on PATH — the ${total_budget}s budget is unenforced" >&2
  PATH="$guard_dir:$PATH" "$mutmut_bin" run "${patterns[@]}" --max-children "$max_children" \
    2>&1 | tee mutation-run.log || run_status=${PIPESTATUS[0]}
fi

budget_exceeded=0
if [ "$run_status" -eq 124 ] || [ "$run_status" -eq 137 ]; then
  echo "mutation budget exceeded: after ${total_budget}s — unmeasured mutants are untested"
  budget_exceeded=1
elif [ "$run_status" -ne 0 ]; then
  echo "error: mutmut run failed (exit $run_status)" >&2
  echo "no score for this PR — do not report one" >&2
  exit 3
fi

tally="$(tr '\r' '\n' < mutation-run.log | grep -oE '[0-9]+/[0-9]+  🎉 [0-9]+ 🫥 [0-9]+  ⏰ [0-9]+  🤔 [0-9]+  🙁 [0-9]+  🔇 [0-9]+  🧙 [0-9]+' | tail -1 || true)"
if [ -z "$tally" ]; then
  echo "error: mutmut produced no progress line — the run did not measure any mutant" >&2
  echo "no score for this PR — do not report one" >&2
  exit 3
fi

killed="$(printf '%s' "$tally" | sed -E 's/.*🎉 ([0-9]+).*/\1/')"
no_tests="$(printf '%s' "$tally" | sed -E 's/.*🫥 ([0-9]+).*/\1/')"
timed_out="$(printf '%s' "$tally" | sed -E 's/.*⏰ ([0-9]+).*/\1/')"
suspicious="$(printf '%s' "$tally" | sed -E 's/.*🤔 ([0-9]+).*/\1/')"
survived="$(printf '%s' "$tally" | sed -E 's/.*🙁 ([0-9]+).*/\1/')"
skipped="$(printf '%s' "$tally" | sed -E 's/.*🔇 ([0-9]+).*/\1/')"
done_count="$(printf '%s' "$tally" | sed -E 's#^([0-9]+)/.*#\1#')"

echo
echo "=== surviving mutants"
"$mutmut_bin" results | sed -n 's/^    \(.*\): survived$/🙁 \1/p' || true
echo
echo "killed:     $killed / $done_count"
echo "survived:   $survived"
echo "timed out:  $timed_out"
echo "suspicious: $suspicious"
if [ "$no_tests" -gt 0 ]; then echo "no tests:   $no_tests"; fi
if [ "$skipped" -gt 0 ]; then echo "skipped:    $skipped"; fi
echo
echo "Tests were selected per function from Mutmut's stats pass."

exit "$(exit_code_for "$survived" "$timed_out" "$suspicious" "$budget_exceeded")"
