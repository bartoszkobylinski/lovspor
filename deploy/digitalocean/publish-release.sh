#!/usr/bin/env bash
# publish-release.sh — drive `lovspor release` (ADR-0014 Decision 6, refining ADR-0013 Decision 8).
#
# The logic lives in the lovspor package (src/lovspor/release/), where every
# step, every crash case and every invariant is unit-tested. This wrapper only
# prepares the environment, takes the lock, and calls the commands in the one
# order the ADR fixes:
#
#   live     (root)     the reconciled live release: Caddy's running config (R),
#                       the adapted config on disk (D) and the marker (M) agree —
#                       an unreconciled host, or one whose admin socket cannot
#                       be reached, stops here
#   build    (lovspor)  probe, then the seven steps under .build-<random>/,
#                       renamed to <release_content_id>/ once finalized; prints
#                       the id (the live one when the release_key is unchanged)
#   commit   (root)     stage the release's fragment, validate, commit it,
#                       reload Caddy, then write the marker; a no-op for the id
#                       that is already live
#   prune    (root)     only when reconciled; never R, D, M.active or M.previous
#
# Nothing here ever writes into a tree Caddy is serving, and no symlink exists.
#
#   publish-release.sh                       build HEAD of the corpus clone and switch
#   publish-release.sh --ref <sha>           build a specific corpus commit
#   publish-release.sh --rollback            the previous release, same transaction
#   publish-release.sh --reconcile [--complete|--abandon]
#   publish-release.sh --prune
#
# Exit 0 on a switch or on "already live"; non-zero leaves the live release
# exactly as it was (a reload failure puts the previous fragment back).
set -euo pipefail

# The Caddyfile's site block is `{$LOVSPOR_DOMAIN} {`. Caddy the SERVICE gets
# that variable from its systemd drop-in; `caddy validate`/`caddy adapt` run by
# the release commands do not, the placeholder expands to nothing, the site
# block parses as a global options block, and validation fails. Read the same
# file Caddy does — but READ it, do not source it: it is a systemd
# EnvironmentFile whose value is `lovspor.no, lovspor.bartoszkobylinski.com`
# unquoted; a shell would run the second word as a command (seen 2026-09-08).
if [ -z "${LOVSPOR_DOMAIN:-}" ] && [ -r /etc/default/caddy-lovspor ]; then
	LOVSPOR_DOMAIN="$(sed -n 's/^LOVSPOR_DOMAIN=//p' /etc/default/caddy-lovspor | tail -n 1 | sed 's/^"\(.*\)"$/\1/')"
fi
: "${LOVSPOR_DOMAIN:?LOVSPOR_DOMAIN is unset and /etc/default/caddy-lovspor did not provide it}"
export LOVSPOR_DOMAIN

# The environment the commands read (each also has a matching option).
export LOVSPOR_RELEASES_ROOT="${LOVSPOR_RELEASES_ROOT:-/var/www/lovspor-releases}"
export LOVSPOR_CADDYFILE="${LOVSPOR_CADDYFILE:-/etc/caddy/Caddyfile}"
export LOVSPOR_RELEASE_FRAGMENT="${LOVSPOR_RELEASE_FRAGMENT:-/etc/caddy/lovspor-release.caddy}"
# Caddy's own spelling of its admin address. The Unix socket is v1's binding;
# TCP (`localhost:2019`) is allowed only for the migration window.
export LOVSPOR_CADDY_ADMIN="${LOVSPOR_CADDY_ADMIN:-unix//run/caddy/admin.sock}"

APP=/opt/lovspor/app
CORPUS=/opt/lovspor/.cache/lovverk
LOVSPOR="$APP/.venv/bin/lovspor"
BUILD_USER=lovspor
LOCK=/run/lock/lovspor-publish.lock

log() { printf '%s publish-release: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# One publish at a time on this box. systemd's Conflicts= keeps the unit off
# the corpus fetch; the lock keeps a manual run off the unit.
exec 9>"$LOCK"
flock -n 9 || die "another publish holds $LOCK"

# Root, the reconcile identity: it can open the admin socket, write the
# fragment and reload Caddy. The build user cannot, by design.
control() { "$LOVSPOR" release "$@"; }

build_as_build_user() {
	# The corpus clone is readable only by the build user, and the build must
	# not run as root. The probe credential arrives from systemd's
	# LoadCredential= as a root-only file; it is passed to the build user as
	# stdin, never as an argument, an environment variable or a file it owns.
	local ref="$1" live="$2" token="${CREDENTIALS_DIRECTORY:-}/site-probe"
	local -a args=(release build --corpus "$CORPUS" --ref "$ref" --live "$live" --releases "$LOVSPOR_RELEASES_ROOT")
	if [ -n "${CREDENTIALS_DIRECTORY:-}" ] && [ -r "$token" ]; then
		sudo -u "$BUILD_USER" "$LOVSPOR" "${args[@]}" --probe-token-file /dev/stdin <"$token"
	else
		sudo -u "$BUILD_USER" "$LOVSPOR" "${args[@]}" </dev/null
	fi
}

publish() {
	local ref="${1:-HEAD}" live release_id
	[ -x "$LOVSPOR" ] || die "$LOVSPOR not found; is the app deployed?"
	[ -d "$CORPUS/.git" ] || die "$CORPUS is not a git clone; run lovspor-fetch-corpus first"
	install -d -o "$BUILD_USER" -g "$BUILD_USER" -m 755 "$LOVSPOR_RELEASES_ROOT"

	live="$(control live)"
	log "live release: $live"
	release_id="$(build_as_build_user "$ref" "$live")"
	log "finalized release: $release_id"
	control commit "$release_id"
	control prune
	log "live: $(control live)"
}

case "${1:-}" in
	--rollback) control rollback && control prune ;;
	--reconcile) shift; control reconcile "$@" ;;
	--prune) control prune ;;
	--ref) [ -n "${2:-}" ] || die "--ref needs a commit"; publish "$2" ;;
	"") publish ;;
	*) die "usage: $0 [--ref <commit> | --rollback | --reconcile [--complete|--abandon] | --prune]" ;;
esac
