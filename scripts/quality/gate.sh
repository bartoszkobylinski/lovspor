# shellcheck shell=bash
# Shared by the quality gates in this directory (issue #323). Sourced, never
# run: a gate script lists its checks with `check`, then calls `finish`.
#
# Kept to bash 3.2, which is /bin/bash on macOS: no associative arrays and no
# expansion of a possibly empty array under `set -u`.

gate_failures=""
gate_ran=0
gate_failed=0

# The last non-blank line of a check's output, capped, with colour codes
# stripped: gitleaks colours its log even into a pipe.
gate_last_line() {
  local esc
  esc="$(printf '\033')"
  sed -e "s/${esc}\\[[0-9;]*m//g" "$1" | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c 1-200 || true
}

# check NAME COMMAND... - run COMMAND, stream its output, record the verdict.
check() {
  local name="$1" log status line
  shift
  log="$(mktemp)"
  echo "==> ${name}: $*"
  set +e
  # A git hook exports GIT_DIR (and, in a worktree, GIT_WORK_TREE / GIT_INDEX_FILE)
  # to everything it spawns. Every check here runs from the repo root and asks
  # git nothing about the hook's repository, while the unit suite spawns git in
  # temp directories — with the hook's GIT_DIR inherited those commands hit the
  # real repository: a trashed worktree index, core.bare flipped to true, 712
  # errors (issues #369, #370). The gate strips every GIT_* variable, whoever
  # invoked it — the same contract as the unit suite's git_env fixture, so a
  # variable git starts exporting tomorrow needs no new line here.
  ( unset $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); "$@" ) 2>&1 | tee "$log"
  status="${PIPESTATUS[0]}"
  set -e
  gate_ran=$((gate_ran + 1))
  if [ "$status" -ne 0 ]; then
    line="$(gate_last_line "$log")"
    gate_failed=$((gate_failed + 1))
    gate_failures="${gate_failures}FAIL ${name}: ${line:-no output} (exit ${status})"$'\n'
  fi
  rm -f "$log"
}

# finish GATE - one FAIL line per failed check, last, and a non-zero exit if any.
finish() {
  if [ "$gate_failed" -gt 0 ]; then
    printf '\n%s: %d of %d checks failed\n%s' "$1" "$gate_failed" "$gate_ran" "$gate_failures"
    exit 1
  fi
  printf '\n%s: all checks passed\n' "$1"
}
