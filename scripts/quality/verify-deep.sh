#!/usr/bin/env bash
# The deep gate (issue #323, docs/decisions.md §9d): the fast gate, then the
# full unit suite. The pre-push hook runs exactly this script. CI still runs the
# suite on every PR and stays authoritative.
#
# The suite does not start when the fast gate fails: that failure has to be
# repaired and re-verified anyway, so minutes of tests first buy nothing.
#
# Usage: scripts/quality/verify-deep.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/quality/gate.sh
. "$here/gate.sh"
cd "$here/../.."

if ! "$here/verify-fast.sh"; then
  echo "verify-deep: the fast gate failed; unit suite not run"
  exit 1
fi
check unit-suite uv run pytest tests/unit/ -q

finish verify-deep
