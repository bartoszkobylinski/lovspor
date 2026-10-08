#!/usr/bin/env bash
# Fails when a tracked file under tests/ is neither Python nor a fixture.
# Two codex-mutation run reports were committed into tests/ and sat on main
# (tests/mutation-remediation-report.md, 2,405 lines, PR #565; and
# tests/mutation-remediation-source-text-report.md, PR #579): a per-run
# artifact that goes stale with the next PR (issue #596). An allowlist, not a
# list of report names: the next leak will not be called what the last was.
# One script for the fast-ci step and the fast gate (issue #558).
#
# Usage, from the repository root: scripts/quality/check_tests_tree.sh
set -euo pipefail

violations=()
while IFS= read -r -d '' path; do
  # `case` globs: `*` crosses `/`, so tests/*.py is every Python file in the tree.
  case "$path" in
    tests/*.py | tests/fixtures/* | tests/*/fixtures/*) ;;
    *) violations+=("$path") ;;
  esac
done < <(git ls-files -z -- tests)

if [ "${#violations[@]}" -gt 0 ]; then
  printf '  %s\n' "${violations[@]}"
  echo '::error::tracked files under tests/ that are neither Python nor a fixture; a run report belongs in a workflow artifact, a fixture under a fixtures/ directory'
  exit 1
fi
