#!/usr/bin/env bash
# rehearse-urls.sh — the staged first-migration rehearsal's URL dry-run
# (ADR-0014 Validation, "First-migration rehearsal (staged)"), before the
# production cutover.
#
# The ADR lists TWO rehearsals and the migration is authorised by both.
# rehearse-migration.sh walks the address transitions, a load Caddy rejects, the
# rollback and the admin socket's four facts on a second Caddy instance; it
# looks at no URL. This one is exactly that: `caddy validate` + `caddy adapt`
# over the old and the new Caddyfiles against a fully built envelope and the
# flat release still behind the lovspor-current symlink, compared route by
# route.
#
# Every assertion is Python, in src/lovspor/release/staged.py, unit-tested
# against committed `caddy adapt` captures. This wrapper only builds an
# envelope to compare against, hands the command the two Caddyfiles, and takes
# the envelope away again. It asserts nothing of its own.
#
#   sudo bash rehearse-urls.sh              dry-run against HEAD of the corpus clone
#   sudo bash rehearse-urls.sh --ref <sha>  dry-run against one corpus commit
#   sudo bash rehearse-urls.sh --keep       leave the envelope for inspection
#
# Nothing is started, reloaded or written: no unit, no admin endpoint, no
# /etc/caddy, no /run/caddy, and not /var/www/lovspor-releases either — the
# envelope is built under a root of this script's own and the dry-run's last
# assertion is that every tree it read is byte-identical to how it found it.
set -euo pipefail

APP=/opt/lovspor/app
CORPUS=/opt/lovspor/.cache/lovverk
LOVSPOR="$APP/.venv/bin/lovspor"
BUILD_USER=lovspor
ENVIRONMENT=/etc/default/caddy-lovspor

PREVIOUS_CADDYFILE=/etc/caddy/Caddyfile
PROPOSED_CADDYFILE="$APP/deploy/digitalocean/Caddyfile"
REH_ROOT=/var/www/lovspor-urls-rehearsal
KEEP=0

log() { printf '%s rehearse-urls: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: it builds an envelope under /var/www and reads /etc/caddy"
[ -x "$LOVSPOR" ] || die "$LOVSPOR not found; is the app deployed?"
[ -d "$CORPUS/.git" ] || die "$CORPUS is not a git clone; run lovspor-fetch-corpus first"
[ -f "$PREVIOUS_CADDYFILE" ] || die "$PREVIOUS_CADDYFILE is missing; nothing to compare against"
[ -f "$PROPOSED_CADDYFILE" ] || die "$PROPOSED_CADDYFILE is missing; is the checkout complete?"

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
		log "--keep: the envelope is left under $REH_ROOT"
		return 0
	fi
	rm -rf "$REH_ROOT"
}
trap teardown EXIT

# LOVSPOR_DOMAIN is what the site block's host name expands from, in BOTH
# Caddyfiles, so `caddy adapt` needs it the way the running Caddy does. It
# comes from the same file the caddy.service drop-in sources — read, never
# written, and never guessed: a dry-run against a different host name would be
# a dry-run of a configuration this box does not have.
[ -f "$ENVIRONMENT" ] || die "$ENVIRONMENT is missing; LOVSPOR_DOMAIN is unknown"
set -a
# shellcheck disable=SC1090
. "$ENVIRONMENT"
set +a
[ -n "${LOVSPOR_DOMAIN:-}" ] || die "$ENVIRONMENT sets no LOVSPOR_DOMAIN"

# An envelope of its own, under a root of its own: this box has no live release
# to hard-link against yet, so it is a full second copy — check `df -h /var/www`
# first. It goes with the run.
build_envelope() {
	install -d -o "$BUILD_USER" -g "$BUILD_USER" -m 755 "$REH_ROOT" "$REH_ROOT/releases"
	sudo -u "$BUILD_USER" "$LOVSPOR" release build \
		--corpus "$CORPUS" --ref "$REF" --live none --releases "$REH_ROOT/releases" </dev/null
}

RELEASE_ID="$(build_envelope)"
log "comparing $PREVIOUS_CADDYFILE with $PROPOSED_CADDYFILE over release $RELEASE_ID"

"$LOVSPOR" release rehearse-urls "$RELEASE_ID" \
	--releases "$REH_ROOT/releases" \
	--previous-caddyfile "$PREVIOUS_CADDYFILE" \
	--caddyfile-source "$PROPOSED_CADDYFILE"

log "URL dry-run passed; with rehearse-migration.sh, the cutover is authorised on this box"
