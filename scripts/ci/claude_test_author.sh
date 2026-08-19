#!/usr/bin/env bash
# Tertiary test author: runs ONLY when both Codex accounts are rate-limited
# (codex_account_failover.py exit 75). A separate Claude model writes the PR
# tests instead, so the pipeline degrades to a different vendor rather than
# blocking every PR until a limit window resets.
#
# Independence constraint: the implementation author on this repo is Claude
# Fable 5, so the fallback model MUST NOT be a Fable model — the independent
# test engineer cannot be the model that wrote the change. Enforced below,
# not just documented.
#
# Subscription-only auth: CLAUDE_TESTS_OAUTH_TOKEN must be a
# `claude setup-token` credential (sk-ant-oat…). API keys are refused, and
# ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are stripped from the child
# environment — in headless mode a present key silently flips the run onto
# per-token billing.
#
# Usage: scripts/ci/claude_test_author.sh [prompt-file]
set -euo pipefail

prompt_file="${1:-.github/codex/pr-tests.md}"
model="${CLAUDE_TESTS_MODEL:-claude-sonnet-5}"

case "$(printf '%s' "$model" | tr '[:upper:]' '[:lower:]')" in
  *fable*)
    echo "error: CLAUDE_TESTS_MODEL=$model — the fallback test author must not be" >&2
    echo "a Fable model: the implementation author is one" >&2
    exit 2
    ;;
esac

token="${CLAUDE_TESTS_OAUTH_TOKEN:-}"
if [ -z "$token" ]; then
  echo "error: CLAUDE_TESTS_OAUTH_TOKEN is not set — create one with" >&2
  echo "'claude setup-token' and store it as a repository secret" >&2
  exit 2
fi
case "$token" in
  sk-ant-oat*) : ;;
  *)
    echo "error: CLAUDE_TESTS_OAUTH_TOKEN is not a subscription OAuth token" >&2
    echo "(sk-ant-oat…); refusing — an API key would bill per token" >&2
    exit 2
    ;;
esac

if ! command -v claude >/dev/null 2>&1; then
  echo "error: claude CLI is not installed on this runner" >&2
  exit 2
fi

if [ ! -f "$prompt_file" ]; then
  echo "error: prompt file not found: $prompt_file" >&2
  exit 2
fi

echo "claude fallback test author: model $model, prompt $prompt_file" >&2
env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN \
  CLAUDE_CODE_OAUTH_TOKEN="$token" \
  claude -p "$(cat "$prompt_file")" \
  --model "$model" \
  --dangerously-skip-permissions
