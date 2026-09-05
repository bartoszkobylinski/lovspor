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
  { printf '%s\n\n' "$MARKER"; cat "$BODY_FILE"; } > "$BODY_FILE.sticky"
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

# `-f` (--raw-field) sends the body as a string and lets gh do the JSON
# encoding: no external jq, and no `-F` type coercion turning a body that
# happens to read as a number or `true` into a non-string.
gh api --method PATCH "repos/$REPO/issues/comments/$existing_id" \
  -f body="$combined" >/dev/null
