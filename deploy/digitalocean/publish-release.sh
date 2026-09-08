#!/usr/bin/env bash
# publish-release.sh — build the ADR-0013 site, validate it, switch to it atomically.
#
# The binding invariant (ADR-0013 Decision 8): a request must never observe a
# mixed publication snapshot. So nothing here ever writes into the tree Caddy is
# serving. A release is built off-path, validated in place, and becomes live by
# one symlink rename. Rollback is the same rename in the other direction.
#
#   /var/www/lovspor-releases/<release-id>/   complete, validated release trees
#   /var/www/lovspor-current -> <release-id>   the one Caddy serves
#
# Runs as root (it reloads Caddy and owns the symlink); the build itself runs as
# the lovspor user, which is the only identity that can read the corpus clone.
#
#   publish-release.sh              build HEAD of the corpus clone and switch
#   publish-release.sh --ref <sha>  build a specific corpus commit
#   publish-release.sh --rollback   switch back to the previous release
#
# Exit 0 on a switch or on "already live"; non-zero leaves the live release
# exactly as it was.
set -euo pipefail

# The Caddyfile's site block is `{$LOVSPOR_DOMAIN} {`. Caddy the SERVICE gets
# that variable from its systemd drop-in; a shell running `caddy validate` does
# not, the placeholder expands to nothing, the site block parses as a global
# options block, and validation fails with "unrecognized global option: encode".
# Seen on the first enablement, 2026-09-08. Source the same file Caddy does.
if [ -r /etc/default/caddy-lovspor ]; then
	set -a
	# shellcheck disable=SC1091
	. /etc/default/caddy-lovspor
	set +a
fi
: "${LOVSPOR_DOMAIN:?LOVSPOR_DOMAIN is unset and /etc/default/caddy-lovspor did not provide it}"

RELEASES=/var/www/lovspor-releases
CURRENT=/var/www/lovspor-current
APP=/opt/lovspor/app
CORPUS=/opt/lovspor/.cache/lovverk
LOVSPOR="$APP/.venv/bin/lovspor"
BUILD_USER=lovspor
CADDYFILE=/etc/caddy/Caddyfile
# Releases retained beside the live one, for rollback. Hard links (see rsync
# below) make each retained release cost roughly the inter-release delta.
KEEP_PREVIOUS=1

log() { printf '%s publish-release: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

live_release() {
	# The directory the symlink names, or empty when nothing is live yet.
	[ -L "$CURRENT" ] && readlink -f "$CURRENT" || true
}

switch_to() {
	# One rename: the symlink is replaced, never edited in place, so every
	# request resolves either the old tree or the new one and nothing between.
	local target="$1"
	ln -sfn "$target" "$CURRENT.next"
	mv -T "$CURRENT.next" "$CURRENT"
}

reload_caddy_or_revert() {
	# The redirect/410 snippet is imported from the live release, so Caddy must
	# reload to serve the new map. Pages are already switched by now; if the
	# reload fails we put the old release back rather than serve new pages under
	# an old map, which would be the mixed snapshot this script exists to prevent.
	local previous="$1"
	if ! systemctl reload caddy; then
		log "caddy reload failed; reverting to $previous"
		[ -n "$previous" ] && switch_to "$previous" && systemctl reload caddy || true
		die "release not switched: caddy would not reload"
	fi
}

prune() {
	# Keep the live release and KEEP_PREVIOUS newest others. Only after a
	# successful switch, so a failed release never costs the rollback target.
	local live keep n=0
	live="$(live_release)"
	keep=$KEEP_PREVIOUS
	for dir in $(ls -1d "$RELEASES"/*/ 2>/dev/null | sort -r); do
		dir="${dir%/}"
		[ "$dir" = "$live" ] && continue
		if [ "$n" -lt "$keep" ]; then n=$((n + 1)); continue; fi
		log "pruning $dir"
		rm -rf "$dir"
	done
}

rollback() {
	local live previous
	live="$(live_release)"
	[ -n "$live" ] || die "nothing is live; nothing to roll back from"
	previous="$(ls -1d "$RELEASES"/*/ 2>/dev/null | sort -r | sed 's:/$::' | grep -vx "$live" | head -1 || true)"
	[ -n "$previous" ] || die "no previous release retained; cannot roll back"
	log "rolling back $live -> $previous"
	LOVSPOR_SITE_ROOT="$previous" caddy validate --config "$CADDYFILE" >/dev/null \
		|| die "previous release's redirect map does not validate under the current Caddyfile"
	switch_to "$previous"
	reload_caddy_or_revert "$live"
	log "live: $(live_release)"
}

publish() {
	local ref="${1:-HEAD}" sha release_id build new previous
	[ -x "$LOVSPOR" ] || die "$LOVSPOR not found; is the app deployed?"
	[ -d "$CORPUS/.git" ] || die "$CORPUS is not a git clone; run lovspor-fetch-corpus first"
	install -d -o "$BUILD_USER" -g "$BUILD_USER" -m 755 "$RELEASES"

	sha="$(sudo -u "$BUILD_USER" git -C "$CORPUS" rev-parse --verify "${ref}^{commit}")" \
		|| die "not a corpus commit: $ref"
	release_id="$(date -u +%Y%m%dT%H%M%SZ)-${sha:0:12}"
	previous="$(live_release)"

	if [ -n "$previous" ] && [ "${previous##*-}" = "${sha:0:12}" ]; then
		log "corpus $sha is already live as $previous; nothing to publish"
		return 0
	fi

	# Build off-path, on the same filesystem as the releases so unchanged files
	# can become hard links rather than copies.
	build="$RELEASES/.build-$release_id"
	new="$RELEASES/$release_id"
	trap 'rm -rf "$build"' EXIT
	log "building corpus $sha into $build"
	sudo -u "$BUILD_USER" "$LOVSPOR" publish-site --corpus "$CORPUS" --out "$build" --ref "$sha"

	if [ -n "$previous" ]; then
		# --checksum compares content, and -t is deliberately absent: a file whose
		# bytes did not change becomes a hard link to the previous release's inode
		# and so KEEPS ITS OLD MTIME. That is what keeps Caddy's ETag/Last-Modified
		# truthful per page instead of telling every crawler the whole site changed.
		log "populating $new against $previous (unchanged files hard-linked)"
		sudo -u "$BUILD_USER" rsync -rlpgoD --checksum --link-dest="$previous" "$build/" "$new/"
		rm -rf "$build"
	else
		mv "$build" "$new"
	fi
	trap - EXIT

	# Validate the tree that is about to be served, and the Caddy config that
	# would import its redirect map — both before anything public changes.
	log "validating $new"
	if ! sudo -u "$BUILD_USER" "$LOVSPOR" publish-check "$new"; then
		rm -rf "$new"
		die "release $release_id refused by publish-check; live release untouched"
	fi
	if ! LOVSPOR_SITE_ROOT="$new" caddy validate --config "$CADDYFILE" >/dev/null; then
		rm -rf "$new"
		die "release $release_id: Caddy refuses its redirect map; live release untouched"
	fi

	log "switching $CURRENT -> $new"
	switch_to "$new"
	reload_caddy_or_revert "$previous"
	prune
	log "live: $(live_release)"
}

case "${1:-}" in
	--rollback) rollback ;;
	--ref) [ -n "${2:-}" ] || die "--ref needs a commit"; publish "$2" ;;
	"") publish ;;
	*) die "usage: $0 [--ref <commit> | --rollback]" ;;
esac
