#!/usr/bin/env bash
# The fast gate (issue #323, docs/decisions.md §9d): the one command for the
# commit loop. The pre-commit hook runs exactly this script, so a hook and a
# manual run cannot disagree. It needs no hooks installed and runs from any
# directory.
#
# Every check runs even after one fails. The checks' own output streams first;
# then each failure is one line, `FAIL <check>: <last output line> (exit N)`,
# and the exit status is non-zero if any check failed.
#
# Usage: scripts/quality/verify-fast.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/quality/gate.sh
. "$here/gate.sh"
cd "$here/../.."

# The fast gate. A new check is one more line here; it then runs at every
# commit (hook), before every push (verify-deep.sh) and by hand alike. Every
# fast-ci step runs here by the same command, or is listed with its reason in
# tests/unit/test_quality_fast_gate.py (issue #558).
check gitleaks gitleaks git --pre-commit --redact --staged --verbose
check conflict-markers scripts/quality/check_conflict_markers.sh
check tests-tree scripts/quality/check_tests_tree.sh
check ruff-check uv run ruff check
check ruff-format uv run ruff format --check
check mypy uv run mypy src/
# A stale or refused mutation-equivalents entry costs ~0.1 s here and a full CI
# cycle in fast-ci: PR #554 moved two registered lines into a new helper.
check equivalents uv run python scripts/ci/mutation_to_json.py --check-equivalents
check ratchets uv run python scripts/quality/check_ratchets.py
check boundaries uv run python scripts/quality/check_boundaries.py
# One named module, never the unit suite: the release contracts (#323 D1/D2)
# are the cheapest form of two failure classes already shipped once.
check release-contracts uv run pytest tests/unit/test_release_contracts.py -q -p no:cacheprovider

finish verify-fast
