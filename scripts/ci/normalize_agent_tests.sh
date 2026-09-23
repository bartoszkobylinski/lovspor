#!/usr/bin/env bash
# Normalize an agent-authored test draft before the pipeline's own lint gate
# sees it (issue #66) — without letting a lint ruff cannot fix end the round.
#
# Order: format, ruff's SAFE fixes, then every violation still standing gets
# an explicit `# noqa: <rule>` on its line. That is the edit the owner made by
# hand to recover the rounds of issues #232 (SIM105) and #256 (RUF001): the
# old step hard-failed on the residue, the tests never ran, the correct draft
# was discarded, and the failure was reported as a pipeline defect. Unsafe
# fixes are never applied — they can change what a test asserts.
#
# Nothing is suppressed silently: each suppression is a warning annotation on
# its file and line, listed again in the step summary, and it sits in the
# pushed diff as a `# noqa` the owner reviews with the rest of the agent's
# commit. A draft ruff cannot read at all (a syntax error) still fails here,
# into the existing pipeline-failure escalation that preserves the work.
#
# Usage: normalize_agent_tests.sh [path ...]      (default: tests/)
# RUFF overrides the ruff command (default: `uv run ruff`).
set -euo pipefail

read -ra RUFF_CMD <<< "${RUFF:-uv run ruff}"
paths=("$@")
[ "${#paths[@]}" -eq 0 ] && paths=(tests/)

"${RUFF_CMD[@]}" format "${paths[@]}"
if "${RUFF_CMD[@]}" check --fix "${paths[@]}"; then
  exit 0
fi

# Re-format before reading the residue: a safe fix can delete lines (an unused
# import), and the annotations below must name the lines of the file that is
# pushed, not of the draft as it was. Concise lines read
# `path:line:col: RULE message`; ruff's own trailer does not match the filter.
"${RUFF_CMD[@]}" format "${paths[@]}"
residual="$("${RUFF_CMD[@]}" check --output-format concise "${paths[@]}" \
  | grep -E '^[^:]+:[0-9]+:[0-9]+: ' || true)"

"${RUFF_CMD[@]}" check --add-noqa "${paths[@]}"
"${RUFF_CMD[@]}" format "${paths[@]}"
# Whatever a noqa could not reach fails the step, as before.
"${RUFF_CMD[@]}" check "${paths[@]}"

[ -n "$residual" ] || exit 0

while IFS= read -r finding; do
  file="${finding%%:*}"
  rest="${finding#*:}"
  line="${rest%%:*}"
  rest="${rest#*:}"
  col="${rest%%:*}"
  message="${rest#*: }"
  echo "::warning file=${file},line=${line},col=${col},title=lint left for the owner::${message} — suppressed with # noqa in the agent's commit"
done <<< "$residual"

{
  echo "### Lint the agent's draft left for the owner"
  echo
  echo "ruff could not fix these safely. Each carries an explicit \`# noqa\` in the"
  echo "pushed commit; review it there like any other line of the agent's work."
  echo
  echo '```'
  echo "$residual"
  echo '```'
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
