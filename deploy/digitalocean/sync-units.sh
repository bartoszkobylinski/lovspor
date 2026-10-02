#!/usr/bin/env bash
# sync-units.sh — install this checkout's systemd units and prove they landed (#484).
#
# The deploy (pull, uv sync, restart) used to leave /etc/systemd/system alone,
# so a unit changed in the repository stayed stale on the box: on 2026-09-30 the
# installed lovspor-fetch-corpus.service lacked the #234 retry and the drift pair
# was not installed at all. This is the one place units are installed — provision.sh
# and every deploy call it — and it ends by comparing every installed unit with the
# repository copy, exiting non-zero on any difference.
#
#   sudo bash sync-units.sh            install, daemon-reload, verify
#   bash sync-units.sh --check         verify only; changes nothing
#
# It never enables, starts or restarts a unit: enabling is provisioning's job,
# restarting the MCP is the deploy's.
#
# Exit 0 every unit matches; 1 an installed unit differs or is missing (each one
# printed as DIFF); 2 a bad argument.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${LOVSPOR_UNIT_DIR:-/etc/systemd/system}"
# Every unit in this directory the box runs. caddy-rehearsal.service is not one:
# rehearse-migration.sh installs it for a rehearsal and removes it after.
UNITS=(
	lovspor-mcp.service
	lovspor-fetch-corpus.service
	lovspor-fetch-corpus.timer
	lovspor-publish.service
	lovspor-site-drift.service
	lovspor-site-drift.timer
	lovspor-alert@.service
)

install_units() {
	local unit
	for unit in "${UNITS[@]}"; do
		install -m 644 "$SRC_DIR/$unit" "$UNIT_DIR/$unit"
	done
	systemctl daemon-reload
}

verify_units() {
	local unit drifted=()
	for unit in "${UNITS[@]}"; do
		if cmp -s "$SRC_DIR/$unit" "$UNIT_DIR/$unit"; then
			echo "OK   $unit"
		else
			echo "DIFF $unit"
			drifted+=("$unit")
		fi
	done
	if [ "${#drifted[@]}" -gt 0 ]; then
		echo "unit drift: ${#drifted[@]} unit(s) in $UNIT_DIR differ from $SRC_DIR: ${drifted[*]}" >&2
		return 1
	fi
}

case "${1:-}" in
	"") install_units; verify_units ;;
	--check) verify_units ;;
	*) echo "usage: sync-units.sh [--check]" >&2; exit 2 ;;
esac
