"""Tests for scripts/ci/classify_lane_failure.py (issue #272).

A job that dies with its runner is recorded as `failure` with the step it was
running still `in_progress` — the step never completed and never failed,
because the machine stopped answering. Reporting that as "BLOCKED before the
tests ran" reads as a verdict about the pull request; on PR #269 that
misreading cost about three hours across two dead runs.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "classify_lane_failure.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("classify_lane_failure", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules[module]; an
    # unregistered module makes that lookup return None at class-creation time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


classify_lane_failure = _load()


def _job(name: str, conclusion: str, steps: list[tuple[str, str, str | None]]) -> dict[str, Any]:
    return {
        "name": name,
        "conclusion": conclusion,
        "steps": [
            {"name": step, "status": status, "conclusion": step_conclusion}
            for step, status, step_conclusion in steps
        ],
    }


def test_a_step_still_running_on_a_failed_job_is_infrastructure() -> None:
    jobs = [
        _job(
            "codex-author",
            "failure",
            [
                ("Set up job", "completed", "success"),
                ("Codex — independent PR test author", "in_progress", None),
                ("Scope guard", "queued", None),
            ],
        )
    ]

    verdict = classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"])

    assert verdict.is_infrastructure
    assert verdict.job == "codex-author"
    assert verdict.step == "Codex — independent PR test author"


def test_a_job_that_failed_on_its_own_step_is_not_infrastructure() -> None:
    """Every step completed, one of them with `failure`: the job reached its own
    verdict, and its in-job escalation already spoke."""
    jobs = [
        _job(
            "codex-tests",
            "failure",
            [
                ("Set up job", "completed", "success"),
                ("Run tests on Codex additions", "completed", "failure"),
                ("Commit and push test additions", "completed", "skipped"),
            ],
        )
    ]

    verdict = classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"])

    assert verdict.kind == "in_job"
    assert verdict.is_infrastructure is False
    assert verdict.step == ""


def test_a_successful_lane_is_never_classified() -> None:
    jobs = [_job("codex-author", "success", [("Set up job", "in_progress", None)])]

    assert classify_lane_failure.classify(jobs, ["codex-author"]).kind == "unknown"


def test_lane_order_decides_which_failure_is_reported() -> None:
    """The author lane is asked about first: it is the one that dies with the
    box, so when both lanes are red its death is the cause and the verifier's
    failure is the consequence."""
    jobs = [
        _job("codex-tests", "failure", [("Run tests", "completed", "failure")]),
        _job("codex-author", "failure", [("Codex", "in_progress", None)]),
    ]

    verdict = classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"])

    assert verdict.job == "codex-author"
    assert verdict.is_infrastructure


def test_lane_order_does_not_skip_an_ordinary_failure_for_later_infrastructure() -> None:
    """The first failed lane is authoritative even when a later lane has the
    infrastructure signature. Otherwise the result would depend on which kind
    of failure happened, rather than the caller's explicit lane order."""
    jobs = [
        _job("codex-tests", "failure", [("Run tests", "in_progress", None)]),
        _job("codex-author", "failure", [("Codex", "completed", "failure")]),
    ]

    verdict = classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"])

    assert verdict.kind == "in_job"
    assert verdict.job == "codex-author"
    assert verdict.step == ""


def test_a_lane_missing_from_the_payload_is_not_a_verdict() -> None:
    """A fork PR skips the agent lane entirely; absence is not a death."""
    jobs = [_job("fast-ci", "failure", [("Unit tests", "completed", "failure")])]

    assert classify_lane_failure.classify(jobs, ["codex-author", "codex-tests"]).kind == "unknown"


@pytest.mark.parametrize("wrap", [True, False])
def test_the_payload_may_arrive_wrapped_or_bare(tmp_path: Path, wrap: bool) -> None:
    """`gh api .../jobs` returns {"total_count": n, "jobs": [...]}, but a caller
    that already unwrapped it with --jq hands over the bare list."""
    job = _job("codex-author", "failure", [("Codex", "in_progress", None)])
    payload: Any = {"total_count": 1, "jobs": [job]} if wrap else [job]
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = classify_lane_failure._load(str(path))

    assert classify_lane_failure.classify(loaded, ["codex-author"]).is_infrastructure


def test_the_cli_prints_the_three_output_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow appends this straight to $GITHUB_OUTPUT, so the shape of
    these three lines is the contract with the reporting step."""
    path = tmp_path / "jobs.json"
    payload = {"jobs": [_job("codex-author", "failure", [("Codex", "in_progress", None)])]}
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert classify_lane_failure.main(["--jobs", str(path), "--lane", "codex-author"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=infrastructure",
        "job=codex-author",
        "step=Codex",
    ]


def test_the_cli_reads_stdin_and_uses_the_documented_default_lanes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {"jobs": [_job("codex-tests", "failure", [("Run tests", "completed", "failure")])]}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert classify_lane_failure.main([]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=in_job",
        "job=codex-tests",
        "step=",
    ]


# Issue #270, verbatim from the codex-tests checkout of run 34457835291 (job
# 102808288828, PR #269, 2026-09-10 08:55 UTC). The push token had expired: git
# was handed a credential, GitHub refused it, git fell back to asking for a
# username and GIT_TERMINAL_PROMPT=0 made that fatal. The comment on #270
# confirms it — renewing LOVSPOR_CI_PUSH_TOKEN was the only change before the
# next rerun cleared the checkout.
_EXPIRED_TOKEN_LOG = (
    "2026-09-10T08:55:12.0000000Z [command]/usr/bin/git -c protocol.version=2 fetch"
    " --no-tags --prune --no-recurse-submodules --unshallow origin"
    " +refs/heads/*:refs/remotes/origin/* +refs/tags/*:refs/tags/*\n"
    "2026-09-10T08:55:13.0000000Z ##[error]fatal: could not read Username for"
    " 'https://github.com': terminal prompts disabled\n"
    "2026-09-10T08:55:13.0000000Z The process '/usr/bin/git' failed with exit code 128\n"
)

_CHECKOUT = "Run actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


def _checkout_death() -> list[dict[str, Any]]:
    return [
        _job(
            "codex-tests",
            "failure",
            [
                ("Set up job", "completed", "success"),
                (_CHECKOUT, "completed", "failure"),
                ("The author lane did not finish", "completed", "skipped"),
            ],
        )
    ]


def test_an_expired_token_at_checkout_is_a_credential_failure() -> None:
    """#270: every PR sat at `codex-tests BLOCKED before the tests ran` for
    eight hours while the cause was an expired secret. A credential is an
    operator action; nothing in the diff and no rerun can fix it."""
    verdict = classify_lane_failure.classify(
        _checkout_death(), ["codex-author", "codex-tests"], _EXPIRED_TOKEN_LOG
    )

    assert verdict.kind == "credential"
    assert verdict.job == "codex-tests"
    assert verdict.step == _CHECKOUT
    assert verdict.signature == (
        "could not read Username for 'https://github.com': terminal prompts disabled"
    )
    assert verdict.is_infrastructure is False


def test_gits_other_wording_of_a_rejected_token_is_a_credential_failure() -> None:
    """Not observed in #270: git's own wording for the same refusal when the
    username-prompt fallback does not happen (a credential helper answered)."""
    line = "fatal: Authentication failed for 'https://github.com/bartoszkobylinski/lovspor/'"
    log = f"2026-09-10T08:55:13.0000000Z ##[error]{line}\n"

    verdict = classify_lane_failure.classify(_checkout_death(), ["codex-tests"], log)

    assert verdict.kind == "credential"
    assert verdict.signature in line


def test_a_signature_outside_an_error_annotation_is_not_evidence() -> None:
    """A failing test that prints its own fixture — this file's, for one — must
    not turn a code failure into a credential outage. Only the runner's own
    `##[error]` annotation, right after the timestamp, counts."""
    log = (
        '2026-09-10T08:55:13.0000000Z E    log = "##[error]fatal: could not read'
        " Username for 'https://github.com': terminal prompts disabled\"\n"
        "2026-09-10T08:55:13.0000000Z could not read Username for"
        " 'https://github.com': terminal prompts disabled\n"
    )

    verdict = classify_lane_failure.classify(_checkout_death(), ["codex-tests"], log)

    assert verdict.kind == "in_job"
    assert verdict.signature == ""


@pytest.mark.parametrize("annotation", ["warning", "notice", "debug"])
def test_only_an_error_annotation_is_credential_evidence(annotation: str) -> None:
    """The contract requires the runner's error annotation. Other workflow
    annotations can quote the same text without proving that authentication
    ended the lane."""
    log = (
        f"2026-09-10T08:55:13.0000000Z ##[{annotation}]fatal: could not read Username for"
        " 'https://github.com': terminal prompts disabled\n"
    )

    verdict = classify_lane_failure.classify(_checkout_death(), ["codex-tests"], log)

    assert verdict == classify_lane_failure.Verdict("in_job", job="codex-tests")


def test_a_death_with_the_runner_stays_infrastructure_whatever_the_log_says() -> None:
    """The log only refines a job that reached its own failure. A step frozen
    mid-flight is the machine, and a stale auth line earlier in the log does
    not change which failure ended the job."""
    jobs = [_job("codex-author", "failure", [("Codex", "in_progress", None)])]

    verdict = classify_lane_failure.classify(jobs, ["codex-author"], _EXPIRED_TOKEN_LOG)

    assert verdict.kind == "infrastructure"
    assert verdict.signature == ""


def test_an_ordinary_failure_with_a_clean_log_keeps_its_verdict() -> None:
    log = "2026-09-10T08:55:13.0000000Z ##[error]Process completed with exit code 1.\n"

    verdict = classify_lane_failure.classify(_checkout_death(), ["codex-tests"], log)

    assert verdict == classify_lane_failure.Verdict("in_job", job="codex-tests")


def test_the_cli_reports_the_credential_and_its_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fourth line exists only when a log was handed over, so a caller
    without `--log` (mutation-remediation.yml) reads the same three lines as
    before. It carries the matched signature constant, never the raw log line:
    the workflow interpolates these outputs, and a log is attacker-reachable."""
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": _checkout_death()}), encoding="utf-8")
    log = tmp_path / "lane.log"
    log.write_text(_EXPIRED_TOKEN_LOG, encoding="utf-8")

    assert classify_lane_failure.main(["--jobs", str(jobs), "--log", str(log)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=credential",
        "job=codex-tests",
        f"step={_CHECKOUT}",
        "signature=could not read Username for 'https://github.com': terminal prompts disabled",
    ]


def test_an_empty_log_is_no_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The workflow writes an empty log when the download fails; that must
    downgrade to the old verdict, not fail the classifier."""
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": _checkout_death()}), encoding="utf-8")
    log = tmp_path / "lane.log"
    log.write_text("", encoding="utf-8")

    assert classify_lane_failure.main(["--jobs", str(jobs), "--log", str(log)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=in_job",
        "job=codex-tests",
        "step=",
        "signature=",
    ]


def test_the_cli_replaces_malformed_utf8_in_a_downloaded_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Job logs are external bytes. One malformed byte elsewhere in the log
    must not hide a valid credential refusal or silence the fallback job."""
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": _checkout_death()}), encoding="utf-8")
    log = tmp_path / "lane.log"
    log.write_bytes(b"invalid: \xff\n" + _EXPIRED_TOKEN_LOG.encode())

    assert classify_lane_failure.main(["--jobs", str(jobs), "--log", str(log)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=credential",
        "job=codex-tests",
        f"step={_CHECKOUT}",
        "signature=could not read Username for 'https://github.com': terminal prompts disabled",
    ]


# Issue #448, verbatim from Mutation Remediation run 36670610064 (PR #469), the
# job log as the jobs API serves it: GitHub's timestamp, then codex's own. The
# standalone codex-cli 0.159.0 on the Mac runner ships without
# `codex-code-mode-host`, so every tool call the agent made failed to spawn.
_CODE_MODE_HOST_LOG = (
    "2026-09-30T04:50:08.6453880Z warning: Code Mode is unavailable because failed to"
    " spawn code-mode host /Users/ci-lovspor/.local/bin/codex-code-mode-host: host"
    " executable was not found. Code mode will fail closed; enable"
    " `features.code_mode_host` and install `codex-code-mode-host`.\n"
    "2026-09-30T04:50:18.8947600Z 2026-09-30T04:50:18.893013Z ERROR"
    " codex_core::tools::router: error=failed to spawn code-mode host"
    " /Users/ci-lovspor/.local/bin/codex-code-mode-host: No such file or directory"
    " (os error 2)\n"
)
_TOOL_STOP = "Stop when the agent could not start its execution tool"


def _tool_death() -> list[dict[str, Any]]:
    return [
        _job(
            "remediate",
            "failure",
            [
                ("Codex — mutation remediation (tests only)", "completed", "success"),
                (_TOOL_STOP, "completed", "failure"),
                ("Scope guard", "completed", "skipped"),
            ],
        )
    ]


def test_a_missing_code_mode_host_is_a_runner_tool_failure() -> None:
    """#448: the runner's Codex install, not the diff and not a survivor verdict."""
    verdict = classify_lane_failure.classify(_tool_death(), ["remediate"], _CODE_MODE_HOST_LOG)

    assert verdict.kind == "runner_tool"
    assert verdict.job == "remediate"
    assert verdict.step == _TOOL_STOP
    assert verdict.signature == "failed to spawn code-mode host"
    assert verdict.is_infrastructure is True


def test_the_startup_warning_alone_is_not_evidence() -> None:
    """Code Mode being unavailable is only a warning until a tool call fails;
    PR #447's author round printed it and still finished."""
    log = _CODE_MODE_HOST_LOG.splitlines(keepends=True)[0]

    verdict = classify_lane_failure.classify(_tool_death(), ["remediate"], log)

    assert verdict == classify_lane_failure.Verdict("in_job", job="remediate")


def test_a_quoted_code_mode_host_error_is_not_evidence() -> None:
    """A failing test printing this file's fixture must stay a code failure:
    only codex's own tracing line, straight after GitHub's timestamp, counts."""
    log = (
        '2026-09-30T04:50:18.1Z E    log = "2026-09-30T04:50:18.893013Z ERROR'
        " codex_core::tools::router: error=failed to spawn code-mode host"
        ' /Users/x/.local/bin/codex-code-mode-host: gone"\n'
    )

    verdict = classify_lane_failure.classify(_tool_death(), ["remediate"], log)

    assert verdict.kind == "in_job"


def test_a_credential_refusal_still_wins_over_a_runner_tool_line() -> None:
    log = _EXPIRED_TOKEN_LOG + _CODE_MODE_HOST_LOG

    verdict = classify_lane_failure.classify(_tool_death(), ["remediate"], log)

    assert verdict.kind == "credential"


def test_the_cli_names_the_runner_tool_with_the_constant_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The path in the log is not echoed: outputs are interpolated into the
    workflow, and a log is attacker-reachable."""
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": _tool_death()}), encoding="utf-8")
    log = tmp_path / "lane.log"
    log.write_text(_CODE_MODE_HOST_LOG, encoding="utf-8")
    args = ["--jobs", str(jobs), "--lane", "remediate", "--log", str(log)]

    assert classify_lane_failure.main(args) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=runner_tool",
        "job=remediate",
        f"step={_TOOL_STOP}",
        "signature=failed to spawn code-mode host",
    ]


# Issue #493, the shape of runs 36784234807 and 36823210811: the Mac runner
# could not download a pinned action from codeload.github.com, so the job's one
# and only step is GitHub's own `Set up job`, concluded `failure`. No step of
# the workflow ran — no checkout, no Codex round.
def _setup_death() -> list[dict[str, Any]]:
    return [_job("codex-author", "failure", [("Set up job", "completed", "failure")])]


def test_a_job_that_failed_in_set_up_job_is_a_runner_setup_failure() -> None:
    verdict = classify_lane_failure.classify(_setup_death(), ["codex-author", "codex-tests"])

    assert verdict.kind == "runner_setup"
    assert verdict.job == "codex-author"
    assert verdict.step == "Set up job"
    assert verdict.is_infrastructure is True


def test_a_failed_job_with_no_steps_at_all_is_a_runner_setup_failure() -> None:
    """A job the runner never set up has an empty steps list: the machine as
    well, never a verdict about the diff."""
    jobs = [_job("codex-author", "failure", [])]

    verdict = classify_lane_failure.classify(jobs, ["codex-author"])

    assert verdict.kind == "runner_setup"
    assert verdict.step == "(no step started)"


def test_set_up_job_only_counts_when_it_is_the_step_that_failed() -> None:
    """A successful `Set up job` followed by the job's own failing step is the
    ordinary in-job case."""
    jobs = [
        _job(
            "codex-author",
            "failure",
            [("Set up job", "completed", "success"), ("Scope guard", "completed", "failure")],
        )
    ]

    assert classify_lane_failure.classify(jobs, ["codex-author"]).kind == "in_job"


def test_a_set_up_job_frozen_mid_flight_stays_infrastructure() -> None:
    jobs = [_job("codex-author", "failure", [("Set up job", "in_progress", None)])]

    assert classify_lane_failure.classify(jobs, ["codex-author"]).kind == "infrastructure"


def test_a_set_up_job_failure_is_not_refined_by_the_log() -> None:
    """No workflow step ran, so nothing in the log can be the job's own
    credential refusal or tool failure."""
    log = _EXPIRED_TOKEN_LOG + _CODE_MODE_HOST_LOG

    verdict = classify_lane_failure.classify(_setup_death(), ["codex-author"], log)

    assert verdict == classify_lane_failure.Verdict(
        "runner_setup", job="codex-author", step="Set up job"
    )


def test_the_cli_reports_a_runner_setup_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": _setup_death()}), encoding="utf-8")
    log = tmp_path / "lane.log"
    log.write_text("", encoding="utf-8")

    assert classify_lane_failure.main(["--jobs", str(jobs), "--log", str(log)]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=runner_setup",
        "job=codex-author",
        "step=Set up job",
        "signature=",
    ]


def test_the_cli_names_a_remediate_lane_that_died_in_set_up_job(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue #499: `remediate-verify` calls the classifier with `--lane remediate`
    and the lane's log, which a job that never ran a step barely has."""
    jobs = tmp_path / "jobs.json"
    death = [_job("remediate", "failure", [("Set up job", "completed", "failure")])]
    jobs.write_text(json.dumps({"jobs": death}), encoding="utf-8")
    log = tmp_path / "lane.log"
    log.write_text("", encoding="utf-8")

    argv = ["--jobs", str(jobs), "--lane", "remediate", "--log", str(log)]
    assert classify_lane_failure.main(argv) == 0

    assert capsys.readouterr().out.splitlines() == [
        "kind=runner_setup",
        "job=remediate",
        "step=Set up job",
        "signature=",
    ]


# Issue #489, the author lane's own stop: `codex exec` exited 0 but its
# transcript held no executed command, and the lane's stop step annotated why.
# The job log as the jobs API serves it: GitHub's timestamp, then the annotation.
_AUTHOR_STOP = "Fail a round that executed no command"
_NO_COMMAND_LOG = (
    "2026-09-30T06:17:57.0829650Z I couldn't inspect or modify the repository.\n"
    "2026-09-30T06:17:58.0000000Z ##[error]The independent test author executed no"
    " command (#489): the agent executed no command. No test reviewed this PR.\n"
)


def _author_noop() -> list[dict[str, Any]]:
    return [
        _job(
            "codex-author",
            "failure",
            [
                ("Codex — independent PR test author", "completed", "success"),
                (_AUTHOR_STOP, "completed", "failure"),
                ("Scope guard", "completed", "skipped"),
            ],
        )
    ]


def test_an_author_round_that_ran_no_command_is_a_lane_failure() -> None:
    lanes = ["codex-author", "codex-tests"]

    verdict = classify_lane_failure.classify(_author_noop(), lanes, _NO_COMMAND_LOG)

    assert verdict.kind == "agent_did_not_run"
    assert verdict.job == "codex-author"
    assert verdict.step == _AUTHOR_STOP
    assert verdict.signature == classify_lane_failure.AGENT_NOT_RUN_SIGNATURE
    assert verdict.is_infrastructure is False


def test_a_missing_tool_host_names_the_runner_before_the_no_command_stop() -> None:
    """The host line says why no command ran: the runner's install, #448."""
    log = _CODE_MODE_HOST_LOG + _NO_COMMAND_LOG

    verdict = classify_lane_failure.classify(_author_noop(), ["codex-author"], log)

    assert verdict.kind == "runner_tool"
    assert verdict.is_infrastructure is True


def test_the_no_command_phrase_outside_an_annotation_is_not_evidence() -> None:
    """A test printing this phrase is still a code failure."""
    log = (
        "2026-09-30T06:17:58.0Z E   assert 'The independent test author executed no"
        " command (#489)' in body\n"
    )

    verdict = classify_lane_failure.classify(_author_noop(), ["codex-author"], log)

    assert verdict.kind == "in_job"


def test_a_credential_refusal_still_wins_over_the_no_command_stop() -> None:
    log = _EXPIRED_TOKEN_LOG + _NO_COMMAND_LOG

    verdict = classify_lane_failure.classify(_author_noop(), ["codex-author"], log)

    assert verdict.kind == "credential"
