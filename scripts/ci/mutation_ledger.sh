#!/usr/bin/env bash
# The nightly mutation ledger on branch `mutation-ledger` (#943).
#
# One orphan commit holding ledger.tar.gz (mutants/**/*.meta, packed by
# mutation_ledger.py) and ledger.json (the range it belongs to). It is a branch,
# not an artifact or a cache entry, because a checkpoint must be pushed DURING
# the run: a runner shutdown skips every later step, `always()` ones included,
# and two nights in a row lost their whole measurement that way.
#
#   mutation_ledger.sh base                  print the ledger's base commit, or nothing
#   mutation_ledger.sh fetch BASE OUT        write the archive to OUT if the ledger is for BASE
#   mutation_ledger.sh save BASE HEAD        pack mutants/ and force-push the ledger
#   mutation_ledger.sh loop BASE HEAD SECS   save every SECS seconds until killed
#   mutation_ledger.sh drop                  delete the ledger branch
#
# Runs from the repo root of a checkout whose `origin` can be pushed to.
set -euo pipefail

BRANCH="${MUTATION_LEDGER_BRANCH:-mutation-ledger}"
LOCAL_REF="refs/mutation-ledger/fetched"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${MUTATION_LEDGER_PYTHON:-.venv/bin/python}"

fetch_ledger() {
  git fetch -q --no-tags origin "+refs/heads/$BRANCH:$LOCAL_REF" 2>/dev/null
}

ledger_base() {
  fetch_ledger || return 0
  git show "$LOCAL_REF:ledger.json" 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("base", ""))' 2>/dev/null || true
}

fetch() {
  local base="$1" out="$2" found
  found="$(ledger_base)"
  if [ -z "$found" ]; then
    echo "ledger: none on $BRANCH; measuring the whole range"
    return 0
  fi
  if [ "$found" != "$base" ]; then
    echo "ledger: $BRANCH is for base $found, not $base; ignoring it"
    return 0
  fi
  git show "$LOCAL_REF:ledger.tar.gz" >"$out"
  echo "ledger: restoring $BRANCH for base $base ($(wc -c <"$out" | tr -d ' ') bytes)"
}

save() {
  local base="$1" head="$2" work archive tree commit
  work="$(mktemp -d)"
  archive="$work/ledger.tar.gz"
  "$python_bin" "$here/mutation_ledger.py" pack "$archive"
  printf '{"base": "%s", "head": "%s", "run_id": "%s", "saved_at": "%s"}\n' \
    "$base" "$head" "${GITHUB_RUN_ID:-local}" "$(date -u +%FT%TZ)" >"$work/ledger.json"
  tree="$(printf '100644 blob %s\tledger.json\n100644 blob %s\tledger.tar.gz\n' \
    "$(git hash-object -w "$work/ledger.json")" "$(git hash-object -w "$archive")" | git mktree)"
  commit="$(GIT_AUTHOR_NAME=nightly-mutation GIT_AUTHOR_EMAIL=nightly-mutation@invalid \
    GIT_COMMITTER_NAME=nightly-mutation GIT_COMMITTER_EMAIL=nightly-mutation@invalid \
    git commit-tree "$tree" -m "mutation ledger: $base...$head")"
  git push -q --force origin "$commit:refs/heads/$BRANCH"
  echo "ledger: saved $(wc -c <"$archive" | tr -d ' ') bytes to $BRANCH at $(date -u +%T)"
  rm -rf "$work"
}

loop() {
  local base="$1" head="$2" every="$3"
  while sleep "$every"; do
    save "$base" "$head" || echo "warning: ledger checkpoint failed; the next one retries" >&2
  done
}

drop() {
  if git ls-remote --exit-code -q origin "refs/heads/$BRANCH" >/dev/null 2>&1; then
    git push -q origin ":refs/heads/$BRANCH"
    echo "ledger: deleted $BRANCH"
  fi
}

case "${1:-}" in
  base) ledger_base ;;
  fetch) fetch "$2" "$3" ;;
  save) save "$2" "$3" ;;
  loop) loop "$2" "$3" "$4" ;;
  drop) drop ;;
  *) echo "usage: mutation_ledger.sh base|fetch BASE OUT|save BASE HEAD|loop BASE HEAD SECS|drop" >&2; exit 2 ;;
esac
