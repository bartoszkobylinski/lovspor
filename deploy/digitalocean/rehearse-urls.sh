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
#   sudo bash rehearse-urls.sh              dry-run against the corpus commit the live release holds
#   sudo bash rehearse-urls.sh --ref <sha>  dry-run against one corpus commit
#   sudo bash rehearse-urls.sh --keep       leave the envelope for inspection
#
# Nothing is started, reloaded or written: no unit, no admin endpoint, no
# /etc/caddy, no /run/caddy, and not /var/www/lovspor-releases either — the
# envelope is built under a root of this script's own and the dry-run's last
# assertion is that every tree it read is byte-identical to how it found it.
set -euo pipefail

# Out of the operator's working directory before anything runs lovspor: a root
# shell starts in /root (0700), where the build user's FastMCP settings stat
# ./.env and get EACCES, not "absent" — the build dies (#300).
cd /

APP=/opt/lovspor/app
CORPUS=/opt/lovspor/.cache/lovverk
LOVSPOR="$APP/.venv/bin/lovspor"
BUILD_USER=lovspor
ENVIRONMENT=/etc/default/caddy-lovspor

PREVIOUS_CADDYFILE=/etc/caddy/Caddyfile
PROPOSED_CADDYFILE="$APP/deploy/digitalocean/Caddyfile"
# The boundary the dry-run checks imports and symlinks inside of, named rather
# than left to the command: the envelope below stands three levels under it,
# and a boundary at REH_ROOT let an import of the old tree's map pass unchecked
# (#308).
DEPLOYMENT_ROOT=/var/www
REH_ROOT=/var/www/lovspor-urls-rehearsal
# The live release the OLD configuration serves, under the same deployment root
# rather than at a second spelling of it: the corpus commit this dry-run has to
# be built from is read off its name (#331, below).
CURRENT_SYMLINK="$DEPLOYMENT_ROOT/lovspor-current"
KEEP=0

log() { printf '%s rehearse-urls: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: it builds an envelope under /var/www and reads /etc/caddy"
[ -x "$LOVSPOR" ] || die "$LOVSPOR not found; is the app deployed?"
[ -d "$CORPUS/.git" ] || die "$CORPUS is not a git clone; run lovspor-fetch-corpus first"
[ -f "$PREVIOUS_CADDYFILE" ] || die "$PREVIOUS_CADDYFILE is missing; nothing to compare against"
[ -f "$PROPOSED_CADDYFILE" ] || die "$PROPOSED_CADDYFILE is missing; is the checkout complete?"

REF=""
while [ "$#" -gt 0 ]; do
	case "$1" in
		--ref) [ -n "${2:-}" ] || die "--ref needs a commit"; REF="$2"; shift 2 ;;
		--keep) KEEP=1; shift ;;
		*) die "usage: $0 [--ref <commit>] [--keep]" ;;
	esac
done

# The corpus commit the comparison stands on. `Answer.same_response`
# (src/lovspor/release/answers.py) compares the served file's SHA-256, so a
# route-by-route comparison only means anything while BOTH sides hold ONE corpus
# commit — and the old side is not free to choose: it is the flat release behind
# CURRENT_SYMLINK. HEAD was the old default, which made this dry-run unpassable
# on any box whose corpus had moved since its live release: on the droplet, 525
# commits and 1 h 40 m of work to a refusal on a document changed in between
# (#331). The byte comparison is the right contract; the unpinned ref was not.
#
# A pre-envelope release directory is named <YYYYMMDDTHHMMSSZ>-<sha12>, where
# the second half IS the corpus commit it was built from:
# `release_id="$(date -u +%Y%m%dT%H%M%SZ)-${sha:0:12}"` in publish-release.sh
# before the envelope (7e650b4), over `git rev-parse --verify <ref>^{commit}` in
# the corpus clone. That pattern is pinned today as FLAT_RELEASE in
# src/lovspor/release/migrate.py — what `migrate --retire` selects those
# directories by — so it is matched here exactly: 12 lowercase hex, no fewer and
# no more. A name is not enough on its own; the tree has to still be there.
live_release_name() {
	local target name
	{ [ -L "$1" ] && [ -d "$1" ]; } || return 0
	target="$(readlink -f -- "$1")" || return 0
	name="${target##*/}"
	[[ "$name" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$ ]] || return 0
	printf '%s\n' "$name"
}

# Announced before the build rather than after it: the refusal this replaces
# reached the operator 1 h 40 m in. `${live##*-}` is how publish-release.sh read
# the corpus commit back out of the same name. No live release is a refusal, not
# a fall back to HEAD: the comparison would have nothing to compare against.
choose_ref() {
	local live
	if [ -n "$REF" ]; then
		log "corpus ref $REF, named by --ref"
		return 0
	fi
	live="$(live_release_name "$CURRENT_SYMLINK")"
	[ -n "$live" ] || die "$CURRENT_SYMLINK is not a symlink to a pre-envelope release directory (<YYYYMMDDTHHMMSSZ>-<sha12>), so the corpus commit the old configuration serves cannot be read; name it with --ref <corpus commit>"
	REF="${live##*-}"
	log "corpus ref $REF, from the live release $live behind $CURRENT_SYMLINK"
}

choose_ref

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
# comes from the same file the caddy.service drop-in names — read, never
# written, and never guessed: a dry-run against a different host name would be
# a dry-run of a configuration this box does not have.
#
# READ it, do not source it. It is a systemd EnvironmentFile, not shell: the
# value is unquoted and may list several names comma-separated, and a shell
# sourcing it runs the second name as a command and exits 127 (#260, #298 —
# raised while the droplet still carried the retired personal-domain alias).
# The last assignment wins and surrounding quotes go, as systemd reads it.
host_names() {
	sed -n 's/^LOVSPOR_DOMAIN=//p' "$1" | tail -n 1 \
		| sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

[ -f "$ENVIRONMENT" ] || die "$ENVIRONMENT is missing; LOVSPOR_DOMAIN is unknown"
LOVSPOR_DOMAIN="$(host_names "$ENVIRONMENT")"
[ -n "$LOVSPOR_DOMAIN" ] || die "$ENVIRONMENT sets no LOVSPOR_DOMAIN"
export LOVSPOR_DOMAIN

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
	--deployment-root "$DEPLOYMENT_ROOT" \
	--previous-caddyfile "$PREVIOUS_CADDYFILE" \
	--caddyfile-source "$PROPOSED_CADDYFILE"

log "URL dry-run passed; with rehearse-migration.sh, the cutover is authorised on this box"
