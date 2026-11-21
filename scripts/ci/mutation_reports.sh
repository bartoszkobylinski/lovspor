#!/usr/bin/env bash
# Commit one night to the `mutation-reports` branch (#984).
#
# The branch is where the nightly mutation lane keeps what must outlive a run
# artifact: the register of mutants left alive, one history line per night and
# each night's report (scripts/ci/mutation_register.py writes them). Unlike
# `mutation-ledger`, which is one commit replaced by force, this branch grows:
# every night is a commit on top of the one before, pushed without force. A
# push that loses a race is not forced through: the night is merged again onto
# the branch as it then stands.
#
# Usage:
#   mutation_reports.sh publish OUT_DIR ARGS...
#
# ARGS are the arguments of `mutation_register.py merge` without --register-in,
# --history-in and --out, which this script supplies. OUT_DIR receives
# issue-section.md and comment.md for the report step. When the night could
# not be pushed the script exits 1, the section is removed and the comment
# says that the branch does not hold the night.
set -euo pipefail

BRANCH="${MUTATION_REPORTS_BRANCH:-mutation-reports}"
LOCAL_REF="refs/mutation-reports/fetched"
ATTEMPTS=3
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${MUTATION_REPORTS_PYTHON:-.venv/bin/python}"

on_remote() {
  git ls-remote --exit-code -q origin "refs/heads/$BRANCH" >/dev/null 2>&1
}

# One fetch, merge, commit and push. A branch that exists and cannot be fetched
# fails here, before anything is built on a history it did not read; one that
# does not exist starts with a commit without a parent, and if the branch was
# only unreachable the push of that commit is refused, since it is not forced.
attempt() {
  local out="$1"
  shift
  local parent="" work tree commit path
  work="$(mktemp -d)"
  : >"$work/register.jsonl"
  : >"$work/history.jsonl"
  if on_remote; then
    git fetch -q --no-tags origin "+refs/heads/$BRANCH:$LOCAL_REF"
    parent="$(git rev-parse "$LOCAL_REF")"
    git show "$LOCAL_REF:register.jsonl" >"$work/register.jsonl" 2>/dev/null || : >"$work/register.jsonl"
    git show "$LOCAL_REF:history.jsonl" >"$work/history.jsonl" 2>/dev/null || : >"$work/history.jsonl"
  fi
  rm -rf "$out"
  "$python_bin" "$here/mutation_register.py" merge "$@" \
    --register-in "$work/register.jsonl" --history-in "$work/history.jsonl" --out "$out"
  # A private index: the job's own index and working tree are not touched.
  export GIT_INDEX_FILE="$work/index"
  if [ -n "$parent" ]; then
    git read-tree "$parent^{tree}"
  else
    git read-tree --empty
  fi
  while IFS= read -r path; do
    git update-index --add --cacheinfo "100644,$(git hash-object -w "$out/branch/$path"),$path"
  done < <(cd "$out/branch" && find . -type f | sed 's|^\./||' | LC_ALL=C sort)
  tree="$(git write-tree)"
  unset GIT_INDEX_FILE
  if [ -n "$parent" ] && [ "$tree" = "$(git rev-parse "$parent^{tree}")" ]; then
    echo "reports: $BRANCH already holds this night; nothing to push"
    rm -rf "$work"
    return 0
  fi
  commit="$(GIT_AUTHOR_NAME=nightly-mutation GIT_AUTHOR_EMAIL=nightly-mutation@invalid \
    GIT_COMMITTER_NAME=nightly-mutation GIT_COMMITTER_EMAIL=nightly-mutation@invalid \
    git commit-tree "$tree" ${parent:+-p "$parent"} -F "$out/commit-message.txt")"
  git push -q origin "$commit:refs/heads/$BRANCH"
  echo "reports: pushed $(git rev-parse --short "$commit") to $BRANCH"
  rm -rf "$work"
}

# The texts for the issue describe the branch. When the night did not reach it
# they would describe a register nobody can read and link to a report that is
# not there: the section for the body is withdrawn and the comment says so.
not_published() {
  local out="$1"
  rm -f "$out/issue-section.md"
  if [ -f "$out/comment.md" ]; then
    {
      echo "- **Not written to the \`$BRANCH\` branch**: all $ATTEMPTS attempts failed (see the run)."
      echo "  The report link above leads nowhere and the register counts above are what"
      echo "  the night would have left, not what the branch holds. The night is in the"
      echo "  run artifact."
    } >>"$out/comment.md"
  fi
}

# Each attempt is a process of its own: called inside an `if`, a function runs
# with `set -e` ignored, and a failed fetch would be followed by a commit.
publish() {
  local try
  for try in $(seq 1 "$ATTEMPTS"); do
    if "${BASH_SOURCE[0]}" attempt "$@"; then
      return 0
    fi
    echo "reports: attempt $try of $ATTEMPTS failed" >&2
    if [ "$try" -lt "$ATTEMPTS" ]; then
      sleep "$((try * ${MUTATION_REPORTS_WAIT:-5}))"
    fi
  done
  not_published "$1"
  return 1
}

case "${1:-}" in
  publish)
    shift
    [ "$#" -ge 1 ] || { echo "usage: mutation_reports.sh publish OUT_DIR ARGS..." >&2; exit 2; }
    publish "$@"
    ;;
  attempt)
    shift
    attempt "$@"
    ;;
  *)
    echo "usage: mutation_reports.sh publish OUT_DIR ARGS..." >&2
    exit 2
    ;;
esac
