#!/usr/bin/env bash
# The deep gate (issue #323, docs/decisions.md §9d and §9f): the fast gate, then
# the fail-closed security scan, then the full unit suite. The pre-push hook runs
# exactly this script. CI still runs all three on every PR and stays
# authoritative.
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
# The scan costs ~1.8 s over src/, so it runs before the suite: a narrowed scan
# is repaired and re-verified anyway, and minutes of tests first buy nothing.
check security-scan uv run python scripts/quality/check_security_scan.py
# Not the network-marked tests: they answer for the operator's third-party
# credential, not for the change being pushed — a revoked local key turned
# every push red for an unrelated diff (issue #359). CI has no such key and
# skips them by their own guard; the local gate now matches it.
check unit-suite uv run pytest tests/unit/ -q -m "not network"

finish verify-deep
