#!/usr/bin/env bash
# rehearse-migration.sh — walk the first migration on a SECOND Caddy instance
# (ADR-0014 Validation (g)), before the production cutover.
#
# The sequence, every assertion and both negative fixtures are Python, in
# src/lovspor/release/rehearsal.py, where they are unit-tested against
# FakeCaddy. This wrapper only builds the second instance's world — two
# Caddyfiles derived from the live ones, two fixture files, the unit, an
# envelope to cut over to — starts it, calls `lovspor release rehearse`, and
# takes the world down again. It asserts nothing of its own.
#
#   sudo bash rehearse-migration.sh              rehearse against HEAD of the corpus clone
#   sudo bash rehearse-migration.sh --ref <sha>  rehearse against one corpus commit
#   sudo bash rehearse-migration.sh --keep       leave the instance up for inspection
#
# Exit 0 is what authorises `publish-release.sh --migrate` on this box. Nothing
# here touches /etc/caddy/Caddyfile, /run/caddy, caddy.service or
# /var/www/lovspor-releases: the rehearsal command refuses the production unit
# and the production Caddyfile by name, and every other path below is its own.
set -euo pipefail

APP=/opt/lovspor/app
CORPUS=/opt/lovspor/.cache/lovverk
LOVSPOR="$APP/.venv/bin/lovspor"
BUILD_USER=lovspor
RELEASE_GROUP=lovspor-release

UNIT=caddy-rehearsal
REH_ETC=/etc/caddy/rehearsal
REH_RUN=/run/$UNIT
REH_ROOT=/var/www/lovspor-rehearsal
REH_DROP_IN=/etc/systemd/system/$UNIT.service.d/lovspor.conf
REH_PORT=8443
REH_TCP=localhost:2029
REH_ADMIN="unix/$REH_RUN/admin.sock"
KEEP=0

log() { printf '%s rehearse-migration: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: it installs a unit and creates /run and /etc paths"
[ -x "$LOVSPOR" ] || die "$LOVSPOR not found; is the app deployed?"
[ -d "$CORPUS/.git" ] || die "$CORPUS is not a git clone; run lovspor-fetch-corpus first"
[ -f /etc/caddy/Caddyfile ] || die "/etc/caddy/Caddyfile is missing; nothing to rehearse against"

REF=HEAD
while [ "$#" -gt 0 ]; do
	case "$1" in
		--ref) [ -n "${2:-}" ] || die "--ref needs a commit"; REF="$2"; shift 2 ;;
		--keep) KEEP=1; shift ;;
		*) die "usage: $0 [--ref <commit>] [--keep]" ;;
	esac
done

teardown() {
	if [ "$KEEP" -eq 1 ]; then
		log "--keep: $UNIT left running on $REH_TCP / $REH_ADMIN"
		return 0
	fi
	log "taking the rehearsal instance down"
	systemctl stop "$UNIT" >/dev/null 2>&1 || true
	rm -rf "$REH_ETC" "$REH_ROOT" "/etc/systemd/system/$UNIT.service.d" \
		"/etc/systemd/system/$UNIT.service"
	systemctl daemon-reload
	rm -rf "$REH_RUN"
}
trap teardown EXIT

# The second instance's two Caddyfiles, derived from the live pair so the
# rehearsal walks THIS box's configuration and not a sample of it. Four
# substitutions, and only four: the site host becomes a loopback port with an
# explicit http:// scheme (so Caddy asks for no certificate), the admin socket
# and the release fragment move under the rehearsal's own paths, and the access
# log goes to its own file. The roots are left alone — reading the pre-envelope
# trees on a loopback port changes nothing about them.
#
# The previous Caddyfile gains a global options block the production one does
# not have, naming localhost:2029. It has to: localhost:2019 belongs to the
# Caddy that is serving the site. The property every address assertion rests on
# survives — `caddy reload` derives the address from the file it supplies, and
# that address is not the socket.
prepare_configuration() {
	install -d -m 755 "$REH_ETC"
	printf '{\n\tadmin %s\n}\n' "$REH_TCP" >"$REH_ETC/Caddyfile"
	rehearsal_form /etc/caddy/Caddyfile >>"$REH_ETC/Caddyfile"
	rehearsal_form "$APP/deploy/digitalocean/Caddyfile" >"$REH_ETC/Caddyfile.new"
	grep -q "$REH_ADMIN|0660" "$REH_ETC/Caddyfile.new" \
		|| die "the new Caddyfile does not bind admin to $REH_ADMIN|0660 after rewriting"
}

rehearsal_form() {
	sed -e "s#unix//run/caddy/admin.sock#$REH_ADMIN#" \
		-e "s#/etc/caddy/lovspor-release.caddy#$REH_ETC/lovspor-release.caddy#" \
		-e "s#^.*LOVSPOR_DOMAIN.*{\$#http://localhost:$REH_PORT {#" \
		-e "s#/var/log/caddy/lovspor.log#/var/log/caddy/lovspor-rehearsal.log#" \
		"$1"
}

# The two fixtures the rehearsal asserts MUST fail. `rejected` validates and
# fails at load, by binding a port this Caddy cannot have: :443 is the serving
# instance's, and this unit carries no CAP_NET_BIND_SERVICE either.
# `unsuffixed` is the same file without the creation-mode suffix, so a restart
# recreates the socket with the umask's mode instead of 0660.
prepare_fixtures() {
	cp "$REH_ETC/Caddyfile.new" "$REH_ETC/Caddyfile.rejected"
	printf '\n:443 {\n\trespond "the rehearsal expects this block to fail at load"\n}\n' \
		>>"$REH_ETC/Caddyfile.rejected"
	sed "s#|0660##" "$REH_ETC/Caddyfile.new" >"$REH_ETC/Caddyfile.unsuffixed"
}

start_instance() {
	install -m644 "$APP/deploy/digitalocean/$UNIT.service" /etc/systemd/system/
	systemctl daemon-reload
	systemctl start "$UNIT"
	systemctl is-active --quiet "$UNIT" || die "$UNIT did not start; journalctl -u $UNIT"
	log "$UNIT is up on http://localhost:$REH_PORT, admin $REH_TCP"
}

# A full envelope of its own: the rehearsal cuts over to a real one, and this
# box has no live release to hard-link against yet, so it is a full second copy
# — check `df -h /var/www` first. The tree goes with the instance at teardown.
build_envelope() {
	install -d -o "$BUILD_USER" -g "$BUILD_USER" -m 755 "$REH_ROOT" "$REH_ROOT/releases"
	sudo -u "$BUILD_USER" "$LOVSPOR" release build \
		--corpus "$CORPUS" --ref "$REF" --live none --releases "$REH_ROOT/releases" </dev/null
}

prepare_configuration
prepare_fixtures
start_instance
RELEASE_ID="$(build_envelope)"
log "rehearsing with release $RELEASE_ID"

"$LOVSPOR" release rehearse "$RELEASE_ID" \
	--unit "$UNIT" \
	--caddyfile "$REH_ETC/Caddyfile" \
	--caddyfile-source "$REH_ETC/Caddyfile.new" \
	--rejected-source "$REH_ETC/Caddyfile.rejected" \
	--unsuffixed-source "$REH_ETC/Caddyfile.unsuffixed" \
	--fragment "$REH_ETC/lovspor-release.caddy" \
	--releases "$REH_ROOT/releases" \
	--admin "$REH_ADMIN" \
	--tcp-admin "$REH_TCP" \
	--drop-in "$REH_DROP_IN" \
	--runtime-dir "$REH_RUN" \
	--release-group "$RELEASE_GROUP" \
	--unprivileged-user "$BUILD_USER"

log "rehearsal passed: the production cutover is authorised on this box"
