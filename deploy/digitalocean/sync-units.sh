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
# Units that match can still reach no one: from 2026-10-01 to 10-23 450 failure
# alerts went to an empty LOVSPOR_ALERT_WEBHOOK and every one was a logged
# no-op (#671). So both modes end by asking `lovspor ops alert-target` about
# /etc/lovspor/alert.env (root-only: run as root), which prints one
# `alert target:` line.
#
# Exit 0 every unit matches and the alert target is set; 1 an installed unit
# differs or is missing (each one printed as DIFF); 2 a bad argument; 3 every
# unit matches but the alert target is not configured, refused by the alert
# reader, or unknown (file unreadable, lovspor not installed). Units are installed and verified either way: 3 is the deploy's to
# report, not a reason to skip the restart (README: `|| [ $? -eq 3 ]`).
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${LOVSPOR_UNIT_DIR:-/etc/systemd/system}"
ALERT_ENV="${LOVSPOR_ALERT_ENV:-/etc/lovspor/alert.env}"
LOVSPOR_BIN="${LOVSPOR_BIN:-/opt/lovspor/app/.venv/bin/lovspor}"
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

# The verdict is the alert reader's own, not a shell copy of it: two copies
# diverged in review (an authority-less URL, an upper-case scheme), and
# urlsplit also drops tabs and leading control characters, refuses malformed
# IPv6 brackets and NFKC-unsafe hosts. `lovspor ops alert-target` reads the file
# only (never the caller's environment), prints one `alert target:` line,
# never the URL, and exits 3 when an alert would reach no one. Root running the
# lovspor user's venv must not write bytecode into it (#667).
verify_alert_target() {
	if [ ! -x "$LOVSPOR_BIN" ]; then
		echo "alert target: unknown ($LOVSPOR_BIN not installed)"
		echo "cannot tell whether failure alerts reach anyone: run uv sync first" >&2
		return 3
	fi
	PYTHONDONTWRITEBYTECODE=1 "$LOVSPOR_BIN" ops alert-target --env-file "$ALERT_ENV"
}

# Drift (1) outranks a missing alert target (3); both lines are printed.
verify_all() {
	local units=0 target=0
	verify_units || units=$?
	verify_alert_target || target=$?
	[ "$units" -eq 0 ] || return "$units"
	return "$target"
}

case "${1:-}" in
	"") install_units; verify_all ;;
	--check) verify_all ;;
	*) echo "usage: sync-units.sh [--check]" >&2; exit 2 ;;
esac
