#!/usr/bin/env bash
# Post a pipeline escalation into ONE comment per PR per workflow, appending
# each round, instead of creating a new comment every time.
#
# Why: GitHub emails the PR author when a comment is CREATED, not when one is
# edited. Eight `gh pr comment` call sites x every red round meant PR #230
# alone sent ten notification mails carrying nine distinct escalations. The
# escalations themselves are load-bearing — each names the run and the
# artifact a human needs — so they are appended here, never overwritten.
# The evidence trail is preserved; only the mail-per-round is not.
#
# One marker per workflow, not per PR: pr-pipeline.yml and
# mutation-remediation.yml can be in flight at the same time, and a shared
# comment would let a read-modify-write from one silently drop the other's
# round. Separate markers make the two streams independent.
#
# With STICKY_HEAD_SHA set (issue #477), each round opens with the head it was
# written for, a round for a head the PR has already left says it is stale,
# and one status line under the marker names the head of the newest round.
# On PR #469 a reader took the first round (head a85fe67, 12 survivors) for the
# current state; the round for the real head (99e00f7, 5 survivors, run
# 36679035597) sat at the bottom with nothing saying the top was superseded.
#
# Usage: pr_sticky_comment.sh <marker-key> <pr-number> <body-file>
set -euo pipefail

MARKER_KEY="${1:?usage: pr_sticky_comment.sh <marker-key> <pr-number> <body-file>}"
PR="${2:?usage: pr_sticky_comment.sh <marker-key> <pr-number> <body-file>}"
BODY_FILE="${3:?usage: pr_sticky_comment.sh <marker-key> <pr-number> <body-file>}"
REPO="${GH_REPO:-${GITHUB_REPOSITORY:?GH_REPO or GITHUB_REPOSITORY must be set}}"

[ -r "$BODY_FILE" ] || { echo "body file not readable: $BODY_FILE" >&2; exit 1; }

MARKER="<!-- lovspor-sticky:${MARKER_KEY} -->"
SEPARATOR=$'\n\n---\n\n'
# GitHub rejects a comment body over 65536 characters with a 422. Stay clear of
# the edge so an append never fails the job it exists to report.
MAX_BODY=60000
HEAD_SHA="${STICKY_HEAD_SHA:-}"
STATUS_PREFIX="_Rounds run oldest first"
status=""

if [ -n "$HEAD_SHA" ]; then
  # Never fatal: an escalation that cannot read the PR head still posts.
  pr_head="$(gh api "repos/$REPO/pulls/$PR" --jq '.head.sha' 2>/dev/null)" || pr_head=""
  round_head="**Head \`${HEAD_SHA:0:7}\`**"
  if [ -z "$pr_head" ]; then
    where="PR head unknown: it could not be read"
  elif [ "$pr_head" = "$HEAD_SHA" ]; then
    where="the PR head"
  else
    round_head="$round_head — STALE: the PR head is now \`${pr_head:0:7}\`; that head's own run reports for it."
    where="not the PR head \`${pr_head:0:7}\`"
  fi
  status="${STATUS_PREFIX}: the newest, at the bottom, was written for head \`${HEAD_SHA:0:7}\`, ${where}._"
  { printf '%s\n\n' "$round_head"; cat "$BODY_FILE"; } > "$BODY_FILE.round"
  BODY_FILE="$BODY_FILE.round"
fi

# The status line sits right under the marker, one copy only: the old one is
# dropped and the new one put back in its place.
with_status() {
  local body="$1" head="${MARKER}"$'\n\n'
  if [ -z "$status" ]; then printf '%s' "$body"; return; fi
  body="${body#"$head"}"
  if [[ "$body" == "$STATUS_PREFIX"* ]]; then body="${body#*$'\n\n'}"; fi
  printf '%s' "${head}${status}"$'\n\n'"${body}"
}

existing_id="$(
  gh api "repos/$REPO/issues/$PR/comments" --paginate \
    --jq "[.[] | select(.body | contains(\"$MARKER\")) | .id] | first // empty"
)"
# --paginate applies the filter once per page, so a marker duplicated across
# comments (two created by a race) yields several ids. Keep the first rather
# than splicing them all into the PATCH URL. Trimmed in the shell, not through
# `head`, which closes the pipe early and would SIGPIPE gh under `pipefail`.
existing_id="${existing_id%%$'\n'*}"

if [ -z "$existing_id" ]; then
  if [ -n "$status" ]; then
    with_status "$MARKER"$'\n\n'"$(cat "$BODY_FILE")" > "$BODY_FILE.sticky"
  else
    { printf '%s\n\n' "$MARKER"; cat "$BODY_FILE"; } > "$BODY_FILE.sticky"
  fi
  gh pr comment "$PR" --body-file "$BODY_FILE.sticky"
  exit 0
fi

previous="$(gh api "repos/$REPO/issues/comments/$existing_id" --jq '.body')"
combined="${previous}${SEPARATOR}$(cat "$BODY_FILE")"

# Trim from the OLDEST round forward: the newest escalation is the one a human
# is being paged about, so it is the last thing that may ever be dropped.
# Whole rounds are dropped at a separator boundary rather than a byte count —
# a byte cut can land mid-UTF-8-sequence, and gh would then fail to encode the
# body — failing the escalation instead of shortening it.
if [ "${#combined}" -gt "$MAX_BODY" ]; then
  trimmed=""
  while [ "${#combined}" -gt "$MAX_BODY" ]; do
    rest="${combined#*"$SEPARATOR"}"
    [ "$rest" = "$combined" ] && break
    combined="$rest"
    trimmed="yes"
  done
  if [ -n "$trimmed" ]; then
    notice="_Older rounds trimmed at GitHub's comment size limit; every run is still listed in this PR's checks history._"
    combined="${MARKER}"$'\n\n'"${notice}${SEPARATOR}${combined}"
  fi
fi
if [ -n "$status" ]; then combined="$(with_status "$combined")"; fi

# `-f` (--raw-field) sends the body as a string and lets gh do the JSON
# encoding: no external jq, and no `-F` type coercion turning a body that
# happens to read as a number or `true` into a non-string.
gh api --method PATCH "repos/$REPO/issues/comments/$existing_id" \
  -f body="$combined" >/dev/null
