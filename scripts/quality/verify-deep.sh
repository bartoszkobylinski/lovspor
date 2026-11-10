#!/usr/bin/env bash
# The deep gate (issue #323, docs/decisions.md §9d and §9f): the fast gate, then
# the fail-closed security scan, then the unit tests in the push's scope (issue
# #864: the whole suite on a shared-infrastructure change or with
# VERIFY_DEEP_FULL=1, in a machine-wide slot, at nice 10). The
# pre-push hook runs exactly this script. CI runs the fast checks, the scan and
# the whole unit suite on every PR and stays authoritative.
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
# run_suite.py picks the push's test files and runs them without the
# network-marked tests (issue #359): those answer for the operator's
# third-party credential, not for the change being pushed.
check unit-suite uv run python scripts/quality/run_suite.py

finish verify-deep
