"""Issue #472: a remediation round that never ran is not a round that changed nothing.

On PR #469 the agent could not execute a single command (the runner's
`codex-code-mode-host` was missing, #448), `codex exec` still exited 0, the
patch was empty, and the sticky comment said "12 survivor(s) remediation called
non-killable" — a classification no agent made.

The fixtures are real, never regenerated:
- `codex-no-command-run-36670610064.log` — the Codex step's output of Mutation
  Remediation run 36670610064 (PR #469, head a85fe67), GitHub's timestamps cut,
  as `tee` would have written it;
- `codex-ran-commands-run-36531263464.log` — the first 64 lines of run
  36531263464 on mikrus-codex, where the agent read the artifact through `exec`;
- `mutation-result-a85fe67.json` — artifact `mutation-result-a85fe67…`
  (ID 11077319140), the one that round was handed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "scripts" / "ci" / "remediation_transcript.py"
_FIXTURES = Path(__file__).parent / "fixtures" / "remediation"
_NO_COMMAND = _FIXTURES / "codex-no-command-run-36670610064.log"
_RAN = _FIXTURES / "codex-ran-commands-run-36531263464.log"
_RESULT = _FIXTURES / "mutation-result-a85fe67.json"


def _not_run(log: Path, result: Path = _RESULT) -> str:
    done = subprocess.run(
        [sys.executable, str(_HELPER), "--log", str(log), "--result", str(result)],
        check=True,
        capture_output=True,
        text=True,
    )
    (line,) = done.stdout.splitlines()
    key, _, message = line.partition("=")
    assert key == "not_run"
    return message


def test_a_round_that_ran_no_command_is_reported_as_not_run() -> None:
    message = _not_run(_NO_COMMAND)

    assert message.startswith("Mutation remediation did not run")
    assert "no survivor was classified" in message
    assert "Remediation: FAILED (agent_did_not_run)" in message


def test_it_never_claims_a_non_killable_verdict() -> None:
    message = _not_run(_NO_COMMAND)

    assert "called non-killable" not in message
    assert "made no safe test-only change" not in message


def test_the_missing_code_mode_host_is_named_with_its_path() -> None:
    message = _not_run(_NO_COMMAND)

    assert "/Users/ci-lovspor/.local/bin/codex-code-mode-host" in message
    assert "#448" in message


def test_the_unclassified_counts_come_from_the_artifact() -> None:
    message = _not_run(_NO_COMMAND)

    assert "Gate: FAIL (surviving_mutants)" in message
    assert "Unclassified: 12 survived" in message


def test_a_round_that_ran_commands_says_nothing() -> None:
    assert _not_run(_RAN) == ""


def test_the_github_log_view_with_timestamps_reads_the_same(tmp_path: Path) -> None:
    stamped = tmp_path / "stamped.log"
    lines = _NO_COMMAND.read_text(encoding="utf-8").splitlines()
    stamped.write_text("".join(f"2026-09-30T04:50:08.1Z {line}\n" for line in lines))

    assert _not_run(stamped) == _not_run(_NO_COMMAND)


def test_no_command_without_a_named_cause_still_says_so(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("codex\nI could not start.\ntokens used\n", encoding="utf-8")

    message = _not_run(log)

    assert message.startswith("Mutation remediation did not run (the agent executed no command)")


def test_the_prompt_quoting_exec_is_not_a_command(tmp_path: Path) -> None:
    log = tmp_path / "agent.log"
    log.write_text("user\nexec the tests, then exec again\ncodex\ndone\n", encoding="utf-8")

    assert _not_run(log).startswith("Mutation remediation did not run")


def test_a_missing_transcript_claims_nothing(tmp_path: Path) -> None:
    assert _not_run(tmp_path / "absent.log") == ""


def test_an_unreadable_artifact_still_reports_the_round_as_not_run(tmp_path: Path) -> None:
    broken = tmp_path / "mutation-result.json"
    broken.write_text("{truncated", encoding="utf-8")

    message = _not_run(_NO_COMMAND, broken)

    assert message.startswith("Mutation remediation did not run")
    assert "counts unavailable" in message


def test_the_message_is_one_output_line() -> None:
    assert "\n" not in _not_run(_NO_COMMAND)
