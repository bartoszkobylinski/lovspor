#!/usr/bin/env bash
# Fails when a tracked file carries an unresolved merge-conflict marker.
# docs/architecture.md carried an unresolved merge block on main for a week
# (issue #196); nothing read it and nothing checked it. One script for the
# fast-ci step and the fast gate, so the two cannot drift apart (issue #558).
#
# Usage, from the repository root: scripts/quality/check_conflict_markers.sh
set -euo pipefail

if git grep -nE '^(<{7}|>{7}) ' -- .; then
  echo '::error::unresolved merge-conflict markers are committed'
  exit 1
fi
