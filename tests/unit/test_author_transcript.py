"""Issue #489: a test-author round that ran no command is not a round that found nothing.

On PR #469 (head 99e00f7, run 36677467828) `codex-author` reported success and
`codex-tests` went green, yet the independent test author never executed a
command: the runner's `codex-code-mode-host` was missing (#448) and `codex
exec` still exited 0. The author lane now reads its own transcript and fails
closed when the round ran nothing, or left nothing to read.

The fixtures are the remediation lane's real transcripts (#472): the same Codex
CLI, the same runner, the same failure, and the same `exec` marker.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "scripts" / "ci" / "author_transcript.py"
_FIXTURES = Path(__file__).parent / "fixtures" / "remediation"
_NO_COMMAND = _FIXTURES / "codex-no-command-run-36670610064.log"
_RAN = _FIXTURES / "codex-ran-commands-run-36531263464.log"


def _check(log: Path) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, str(_HELPER), "--log", str(log)],
        check=False,
        capture_output=True,
        text=True,
    )
    (line,) = done.stdout.splitlines()
    key, _, reason = line.partition("=")
    assert key == "not_run"
    return done.returncode, reason


def test_a_round_that_ran_commands_passes_with_an_empty_reason() -> None:
    assert _check(_RAN) == (0, "")


def test_the_pr_469_round_fails_and_names_the_missing_tool_host() -> None:
    status, reason = _check(_NO_COMMAND)

    assert status == 1
    assert "/Users/ci-lovspor/.local/bin/codex-code-mode-host is missing" in reason
    assert "#448" in reason


def test_no_command_without_a_named_cause_still_fails(tmp_path: Path) -> None:
    log = tmp_path / "codex-author.log"
    log.write_text("codex\nI could not inspect the repository.\ntokens used\n", encoding="utf-8")

    assert _check(log) == (1, "the agent executed no command")


def test_a_missing_transcript_fails_closed(tmp_path: Path) -> None:
    """The remediation lane claims nothing without a transcript; this lane's
    green check is itself the claim, so no evidence must mean no success."""
    assert _check(tmp_path / "absent.log") == (1, "the round left no transcript")


def test_an_empty_or_blank_transcript_fails_closed(tmp_path: Path) -> None:
    for content in ("", "\n  \n"):
        log = tmp_path / "codex-author.log"
        log.write_text(content, encoding="utf-8")

        assert _check(log) == (1, "the round left no transcript")


def test_the_prompt_quoting_exec_is_not_a_command(tmp_path: Path) -> None:
    log = tmp_path / "codex-author.log"
    log.write_text("user\nexec the tests, then exec again\ncodex\ndone\n", encoding="utf-8")

    assert _check(log) == (1, "the agent executed no command")


def test_the_github_log_view_with_timestamps_reads_the_same(tmp_path: Path) -> None:
    stamped = tmp_path / "stamped.log"
    lines = _RAN.read_text(encoding="utf-8").splitlines()
    stamped.write_text("".join(f"2026-09-30T06:17:44.1Z {line}\n" for line in lines))

    assert _check(stamped) == (0, "")
