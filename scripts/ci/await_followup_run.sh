#!/usr/bin/env bash
# After a lane pushed, wait for the pr-pipeline run on the pushed SHA (#798).
#
# Usage: await_followup_run.sh <pr-number> <pushed-sha> <before-sha>
#
# A codex-tests push skips `mutation` and `ready` by design: the push fires a
# fresh `synchronize` run that reaches its own verdict. On PR #791 (run
# 42059499913) the branch had the pushed SHA, the PR kept the old head for 20
# minutes and no run started. Every check was green or skipped, and nothing
# said that no verdict would ever arrive.
#
# This asks GitHub, a bounded number of times, whether a pr-pipeline run
# exists for exactly the pushed SHA. When the follow-up run does start, the
# workflow's concurrency group cancels the run this script is in, which is the
# same success. A PR head that moved past both SHAs has a newer push and is
# left to that push's run. When none of this happens within the bound, the PR
# is labelled `needs-human:pipeline` and told what to do, and the script fails
# so the stranded head shows a red check. It never closes, reopens or pushes:
# re-triggering the run is the owner's call.
#
# Exit: 0 a run exists or the head moved on; 1 escalated; 2 bad arguments.
set -uo pipefail

usage="usage: await_followup_run.sh <pr-number> <pushed-sha> <before-sha>"
pr="${1:-}"
pushed="${2:-}"
before="${3:-}"
attempts="${FOLLOWUP_ATTEMPTS-20}"
interval="${FOLLOWUP_INTERVAL_SECONDS-60}"
repo="${GH_REPO:-${GITHUB_REPOSITORY:-}}"

refuse() { echo "await_followup_run: $1" >&2; exit 2; }
[[ "$pr" =~ ^[1-9][0-9]*$ ]] || refuse "$usage"
[[ "$pushed" =~ ^[0-9a-f]{40}$ ]] || refuse "pushed SHA must be 40 hex characters"
[[ "$before" =~ ^[0-9a-f]{40}$ ]] || refuse "before SHA must be 40 hex characters"
[[ "$attempts" =~ ^[1-9][0-9]*$ ]] && (( attempts <= 60 )) \
  || refuse "FOLLOWUP_ATTEMPTS must be 1 to 60, got '$attempts'"
[[ "$interval" =~ ^[0-9]+$ ]] && (( interval <= 300 )) \
  || refuse "FOLLOWUP_INTERVAL_SECONDS must be 0 to 300, got '$interval'"
[ -n "$repo" ] || refuse "GH_REPO or GITHUB_REPOSITORY must be set"

head=""
for (( attempt = 1; attempt <= attempts; attempt++ )); do
  runs=$(gh api "repos/$repo/actions/workflows/pr-pipeline.yml/runs?head_sha=$pushed&per_page=1" \
    --jq '.total_count' 2>/dev/null) || runs=""
  if [[ "$runs" =~ ^[0-9]+$ ]] && (( runs > 0 )); then
    echo "follow-up run exists for $pushed (attempt $attempt of $attempts)"
    exit 0
  fi
  head=$(gh pr view "$pr" --json headRefOid --jq '.headRefOid' 2>/dev/null) || head=""
  if [[ "$head" =~ ^[0-9a-f]{40}$ ]] && [ "$head" != "$pushed" ] && [ "$head" != "$before" ]; then
    echo "PR head moved to $head, past $pushed: that push's run decides"
    exit 0
  fi
  echo "no pr-pipeline run for $pushed yet; PR head ${head:-unknown} (attempt $attempt of $attempts)"
  (( attempt < attempts )) && sleep "$interval"
done

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
{
  echo "**BLOCKED — no follow-up run (#798).** The test author pushed \`$pushed\`, so"
  echo "this run skipped \`mutation\` and \`ready\` for the run that push should start."
  echo "No pr-pipeline run exists for \`$pushed\` after $attempts checks, $interval s apart."
  echo "The PR head is \`${head:-unknown}\`; the head this run tested was \`$before\`."
  echo ""
  echo "No verdict will arrive on its own. Start the run on the pushed head:"
  echo ""
  echo '```bash'
  echo "gh pr close $pr && gh pr reopen $pr"
  echo '```'
} > "$body"
"$(dirname "$0")/pr_sticky_comment.sh" pipeline "$pr" "$body"
gh pr edit "$pr" --add-label needs-human:pipeline
echo "escalated: no pr-pipeline run for $pushed" >&2
exit 1
