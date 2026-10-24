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
# no-op (#671). So both modes end by reading the alert target from
# /etc/lovspor/alert.env (root-only: run as root) and printing one
# `alert target:` line.
#
# Exit 0 every unit matches and the alert target is set; 1 an installed unit
# differs or is missing (each one printed as DIFF); 2 a bad argument; 3 every
# unit matches but the alert target is not configured, or the file cannot be
# read. Units are installed and verified either way: 3 is the deploy's to
# report, not a reason to skip the restart (README: `|| [ $? -eq 3 ]`).
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${LOVSPOR_UNIT_DIR:-/etc/systemd/system}"
ALERT_ENV="${LOVSPOR_ALERT_ENV:-/etc/lovspor/alert.env}"
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

# LOVSPOR_ALERT_WEBHOOK as `lovspor ops alert` reads the file (systemd's
# EnvironmentFile rules, as release.caddy.environment_file_value): the last
# assignment wins, an indented one counts, surrounding quotes go, and a value
# that is blank once trimmed is unset.
alert_webhook() {
	sed -n 's/^[[:space:]]*LOVSPOR_ALERT_WEBHOOK[[:space:]]*=//p' "$1" | tail -n 1 \
		| sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
			-e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" \
			-e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

# Never prints the URL: for most targets it is a bearer secret. A scheme with no
# authority (`https://`, `http:///x`) is refused, as `lovspor ops alert` refuses
# a URL whose netloc is empty.
verify_alert_target() {
	local webhook
	if [ -e "$ALERT_ENV" ] && [ ! -r "$ALERT_ENV" ]; then
		echo "alert target: unknown (cannot read $ALERT_ENV; run as root)"
		echo "cannot tell whether failure alerts reach anyone: $ALERT_ENV is unreadable; run as root" >&2
		return 3
	fi
	webhook="$( [ -r "$ALERT_ENV" ] && alert_webhook "$ALERT_ENV" || true)"
	case "$webhook" in
		http://[!/?#]* | https://[!/?#]*) echo "alert target: configured" ;;
		*)
			echo "alert target: not configured"
			echo "failure alerts reach no one: set LOVSPOR_ALERT_WEBHOOK in $ALERT_ENV (README § Failure alerts)" >&2
			return 3
			;;
	esac
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
