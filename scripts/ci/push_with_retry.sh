#!/usr/bin/env bash
# Push, and push again only when GitHub answered with a server error (#782).
#
# Usage: push_with_retry.sh <remote> <refspec>
#
# On 2026-11-04 codex-tests passed its tests and then lost the whole round to
# one `remote: Internal Server Error` on the push. A server error says nothing
# about the commit, so the same push is tried again, a bounded number of times.
#
# Everything else is final on the first answer. A rejection that means
# something — non-fast-forward, authorization, a protected branch, a declined
# hook, a missing permission — is checked FIRST and wins even when a server
# error is in the same output. An error this script does not recognise is not
# retried either: retrying is the exception, never the default.
#
# Exit: 0 pushed; 1 refused or not retryable (or a bad bound); 2 server errors
# on every attempt.
set -uo pipefail

remote="${1:?usage: push_with_retry.sh <remote> <refspec>}"
refspec="${2:?usage: push_with_retry.sh <remote> <refspec>}"
attempts="${PUSH_RETRY_ATTEMPTS:-3}"
delay="${PUSH_RETRY_DELAY_SECONDS:-15}"

if ! [[ "$attempts" =~ ^[1-9][0-9]*$ ]] || (( attempts > 10 )); then
  echo "push_with_retry: PUSH_RETRY_ATTEMPTS must be 1 to 10, got '$attempts'" >&2
  exit 1
fi
if ! [[ "$delay" =~ ^[0-9]+$ ]]; then
  echo "push_with_retry: PUSH_RETRY_DELAY_SECONDS must be a whole number, got '$delay'" >&2
  exit 1
fi

policy='non-fast-forward|\(fetch first\)|\[rejected\]|Permission to .* denied|Authentication failed|returned error: 40[0-9]|HTTP 40[0-9]|protected branch|hook declined|GH0[0-9][0-9]|without `workflows` permission|refusing to allow'
server='Internal Server Error|returned error: 5[0-9][0-9]|HTTP 5[0-9][0-9]|50[0-9] (Bad Gateway|Service Unavailable|Gateway Time-?out)'

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

for (( attempt = 1; attempt <= attempts; attempt++ )); do
  if git push "$remote" "$refspec" 2>"$log"; then
    cat "$log" >&2
    exit 0
  fi
  cat "$log" >&2
  if grep -Eq "$policy" "$log" || ! grep -Eq "$server" "$log"; then
    echo "push failed and was not retried: not a GitHub server error (attempt $attempt of $attempts)" >&2
    exit 1
  fi
  if (( attempt < attempts )); then
    echo "GitHub server error on push attempt $attempt of $attempts; retrying in $(( delay * attempt ))s" >&2
    sleep "$(( delay * attempt ))"
  fi
done

echo "push failed: GitHub answered with a server error on all $attempts attempts" >&2
exit 2
