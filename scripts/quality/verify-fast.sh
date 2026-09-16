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
# commit (hook), before every push (verify-deep.sh) and by hand alike.
check gitleaks gitleaks git --pre-commit --redact --staged --verbose
check ruff-check uv run ruff check
check ruff-format uv run ruff format --check
check mypy uv run mypy src/

finish verify-fast
