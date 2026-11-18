#!/usr/bin/env bash
# Sign and push the lovverk publication seal (docs/publication-seal.md).
#
# Runs in the `seal` job of .github/workflows/sync.yml, on a fresh runner that
# never ran lovspor engine code: git, ssh-keygen and coreutils only. The
# unsigned tag object comes from the sync job's Compose step as an artifact;
# nothing in it is trusted. It is re-derived here from origin with git alone
# and refused unless every byte matches, then signed, written with
# `git mktag`, verified against the pinned signers and pushed.
#
# Environment:
#   SEAL_PAYLOAD      the unsigned tag object composed by the sync job
#   SEAL_HEAD         the head the sync job reports it published
#   SEAL_TAG          the tag name, sync/seal-<GITHUB_RUN_ID>-<attempt>
#   SEAL_SIGNERS      allowed_signers file from this lovspor commit
#   SEAL_REMOTE       the lovverk remote (deploy key configured by the job)
#   SEAL_SIGNING_KEY  the private key; absent = not sealing (see below)
#   SEAL_REQUIRED     "true" makes an absent key fail the job
#   GITHUB_RUN_ID     the run the tag name must belong to
#
# Exit 0: sealed, or no key and not required (warning). Non-zero: refused.
set -euo pipefail

notes_ref=refs/notes/temporal-attestations
tagger_line='^tagger lovspor-sync\[bot\] <lovspor-sync@noreply\.github\.com> [0-9]+ \+0000$'

fail() {
  echo "::error::publication seal refused: $*"
  exit 1
}

key="${SEAL_SIGNING_KEY:-}"
unset SEAL_SIGNING_KEY
if [ -z "$key" ]; then
  if [ "${SEAL_REQUIRED:-}" = "true" ]; then
    fail "LOVVERK_SEAL_REQUIRED is true but LOVVERK_SEAL_SIGNING_KEY is not set"
  fi
  echo "::warning::LOVVERK_SEAL_SIGNING_KEY is not set; this head stays unsealed."
  exit 0
fi

: "${SEAL_PAYLOAD:?}" "${SEAL_HEAD:?}" "${SEAL_TAG:?}" "${SEAL_SIGNERS:?}" "${SEAL_REMOTE:?}"
: "${GITHUB_RUN_ID:?}"
signers="$(cd "$(dirname "$SEAL_SIGNERS")" && pwd)/$(basename "$SEAL_SIGNERS")"
grep -Eq '^[[:space:]]*[^#[:space:]]' "$signers" || fail "$SEAL_SIGNERS holds no key"
case "$SEAL_TAG" in
  "sync/seal-$GITHUB_RUN_ID-"[0-9]*) ;;
  *) fail "tag $SEAL_TAG does not belong to run $GITHUB_RUN_ID" ;;
esac

umask 077
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
keyfile="$scratch/seal-key"
printf '%s\n' "$key" > "$keyfile"
unset key

# 1. A fresh, minimal clone: only main and the notes ref, blobs on demand.
repo="$scratch/lovverk.git"
git clone -q --bare --filter=blob:none --single-branch --branch main "$SEAL_REMOTE" "$repo"
git -C "$repo" fetch -q origin "+$notes_ref:$notes_ref"
origin_head="$(git -C "$repo" rev-parse --verify 'refs/heads/main^{commit}')"

# 2. Re-derive the unsigned tag from origin and require it byte for byte.
[ "$origin_head" = "$SEAL_HEAD" ] || fail "origin main is $origin_head, not $SEAL_HEAD"
[ "$(sed -n '1p' "$SEAL_PAYLOAD")" = "object $SEAL_HEAD" ] || fail "payload object is not $SEAL_HEAD"
tagger="$(sed -n '4p' "$SEAL_PAYLOAD")"
printf '%s\n' "$tagger" | grep -Eq "$tagger_line" || fail "payload tagger line is malformed"
blob="$(git -C "$repo" notes --ref="$notes_ref" list "$SEAL_HEAD")" || fail "no note on $SEAL_HEAD"
expected="$scratch/expected"
{
  printf 'object %s\ntype commit\ntag %s\n%s\n\n' "$SEAL_HEAD" "$SEAL_TAG" "$tagger"
  printf 'lovspor-publication-seal v1\ncorpus_commit %s\n' "$SEAL_HEAD"
  printf 'attestation_ref %s\nattestation_blob %s\n\n' "$notes_ref" "$blob"
  git -C "$repo" cat-file blob "$blob"
} > "$expected"
cmp -s "$expected" "$SEAL_PAYLOAD" || fail "payload differs from the tag re-derived from origin"

# 3. Sign the exact bytes with a clean environment, then build the tag object.
cp "$expected" "$scratch/payload"
env -i PATH=/usr/bin:/bin /usr/bin/ssh-keygen -Y sign -q -n git -f "$keyfile" "$scratch/payload"
rm -f "$keyfile"
cat "$scratch/payload" "$scratch/payload.sig" > "$scratch/tag"
oid="$(git -C "$repo" mktag < "$scratch/tag")"
git -C "$repo" update-ref "refs/tags/$SEAL_TAG" "$oid" ""

# 4. Verify with git itself against the pinned signers, then publish.
git -C "$repo" -c gpg.ssh.program=/usr/bin/ssh-keygen \
  -c gpg.ssh.allowedSignersFile="$signers" verify-tag "$SEAL_TAG"
git -C "$repo" push -q origin "refs/tags/$SEAL_TAG"
echo "sealed $SEAL_HEAD as $SEAL_TAG"
