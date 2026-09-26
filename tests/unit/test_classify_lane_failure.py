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
