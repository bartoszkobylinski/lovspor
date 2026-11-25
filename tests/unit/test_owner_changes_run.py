"""The sync's owner-change pass against a local bare "lovverk" with pull refs."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.errors import LovsporError
from lovspor.owner_changes import Pin
from lovspor.owner_changes_apply import PinRefusedError
from lovspor.owner_changes_run import (
    RunContext,
    _record_attempt,
    apply_pins,
    attempt_ref,
    check_applied,
    write_report,
)
from tests.unit.publication_seal_support import git, hermetic_git

ROOT = Path(__file__).resolve().parents[2]
DOC = "lokale-forskrifter/5025/doc.md"
CONTEXT = RunContext("a" * 40, "7")


class Lovverk:
    """A bare origin, an owner checkout that opens pull requests, and the sync's clone."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.origin = tmp_path / "lovverk.git"
        seed = tmp_path / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "main")
        _write(seed, {DOC: "line 1\nline 2\n", "lover/x.md": "law\n"})
        _commit(seed, "seed")
        git(tmp_path, "clone", "-q", "--bare", str(seed), str(self.origin))
        self.owner = tmp_path / "owner"
        git(tmp_path, "clone", "-q", str(self.origin), str(self.owner))
        self.clone = tmp_path / "sync"
        git(tmp_path, "clone", "-q", str(self.origin), str(self.clone))

    def pull_request(self, number: int, files: dict[str, str]) -> str:
        git(self.owner, "checkout", "-q", "-B", f"pr{number}", "origin/main")
        _write(self.owner, files)
        tip = _commit(self.owner, f"owner change {number}")
        git(self.owner, "push", "-q", "-f", "origin", f"HEAD:refs/pull/{number}/head")
        return tip

    def pins(self, *lines: str) -> Path:
        path = self.tmp / "pins"
        path.write_text("# pins\n" + "".join(f"{line}\n" for line in lines), encoding="utf-8")
        return path

    def remote_ref(self, ref: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.origin), "rev-parse", "-q", "--verify", ref],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()


def _write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def lovverk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Lovverk:
    hermetic_git(monkeypatch, tmp_path)
    return Lovverk(tmp_path)


def test_empty_pin_file_makes_no_git_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for the existing sync: no pin, no git, no change."""
    hermetic_git(monkeypatch, tmp_path)
    calls: list[object] = []

    def no_subprocess(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("an empty pin file must not run git")

    monkeypatch.setattr("lovspor.owner_changes_apply.subprocess.run", no_subprocess)
    pins = tmp_path / "pins"
    pins.write_text((ROOT / "ops/lovverk-owner-changes").read_text(encoding="utf-8"))
    report = apply_pins(tmp_path / "no-such-clone", pins, CONTEXT)
    assert (report.file_problems, report.applied, report.refused, report.skipped) == (
        [],
        [],
        [],
        [],
    )
    assert calls == []


def test_malformed_pin_file_applies_nothing(lovverk: Lovverk) -> None:
    head = git(lovverk.clone, "rev-parse", "HEAD")
    pins = lovverk.tmp / "pins"
    pins.write_text("not a pin\n", encoding="utf-8")
    report = apply_pins(lovverk.clone, pins, CONTEXT)
    assert report.file_problems and not report.applied
    assert git(lovverk.clone, "rev-parse", "HEAD") == head


def test_pin_is_applied_with_attempt_ref_and_traceable_message(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "line 1\nline 2 repealed\n"})
    report = apply_pins(lovverk.clone, lovverk.pins(f"{tip} 42 1 repealed"), CONTEXT)
    assert [entry["summary"] for entry in report.applied] == [f"lovverk#42 {tip} attempt 1"]
    assert lovverk.remote_ref(f"refs/owner-changes/{tip}/1") == tip
    assert git(lovverk.clone, "rev-parse", "HEAD^2") == tip
    assert git(lovverk.clone, "log", "-1", "--format=%s") == (
        f"owner-change: lovverk#42 {tip} attempt 1 (pinned in lovspor {'a' * 40}, run 7)"
    )
    assert lovverk.remote_ref("refs/heads/main") != git(lovverk.clone, "rev-parse", "HEAD")


def test_applied_pin_is_skipped_on_a_later_run(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "changed\n"})
    pins = lovverk.pins(f"{tip} 42 1 x")
    apply_pins(lovverk.clone, pins, CONTEXT)
    head = git(lovverk.clone, "rev-parse", "HEAD")
    report = apply_pins(lovverk.clone, pins, CONTEXT)
    assert report.skipped and not report.applied and not report.refused
    assert git(lovverk.clone, "rev-parse", "HEAD") == head


def _refusal(lovverk: Lovverk, pins: Path) -> str:
    head = git(lovverk.clone, "rev-parse", "HEAD")
    report = apply_pins(lovverk.clone, pins, CONTEXT)
    assert not report.applied
    assert git(lovverk.clone, "rev-parse", "HEAD") == head
    (entry,) = report.refused
    return str(entry["code"])


def test_consumed_attempt_is_refused_without_a_write(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "changed\n"})
    git(lovverk.origin, "update-ref", f"refs/owner-changes/{tip}/1", tip)
    assert _refusal(lovverk, lovverk.pins(f"{tip} 42 1 x")) == "attempt-consumed"


def test_next_attempt_number_is_applied(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "changed\n"})
    git(lovverk.origin, "update-ref", f"refs/owner-changes/{tip}/1", tip)
    report = apply_pins(lovverk.clone, lovverk.pins(f"{tip} 42 2 retry"), CONTEXT)
    assert len(report.applied) == 1


def test_moved_pull_request_is_refused_as_stale(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "first\n"})
    lovverk.pull_request(42, {DOC: "second\n"})
    assert _refusal(lovverk, lovverk.pins(f"{tip} 42 1 x")) == "stale-pin"
    assert lovverk.remote_ref(f"refs/owner-changes/{tip}/1") == ""


def test_missing_pull_request_is_refused(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "changed\n"})
    assert _refusal(lovverk, lovverk.pins(f"{tip} 43 1 x")) == "pr-unavailable"


def test_out_of_scope_pin_is_refused_before_the_attempt_ref(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {"lover/x.md": "edited law\n"})
    assert _refusal(lovverk, lovverk.pins(f"{tip} 42 1 x")) == "out-of-scope"
    assert lovverk.remote_ref(f"refs/owner-changes/{tip}/1") == ""


def test_one_refused_pin_does_not_stop_the_next(lovverk: Lovverk) -> None:
    bad = lovverk.pull_request(41, {"README.md": "x\n"})
    good = lovverk.pull_request(42, {"lokale-forskrifter/other.md": "new\n"})
    report = apply_pins(lovverk.clone, lovverk.pins(f"{bad} 41 1 bad", f"{good} 42 1 ok"), CONTEXT)
    assert [entry["code"] for entry in report.refused] == ["out-of-scope"]
    assert len(report.applied) == 1


def test_attempt_ref_created_meanwhile_is_not_overwritten(lovverk: Lovverk) -> None:
    """The create-only push itself: the race the ls-remote check cannot close."""
    tip = lovverk.pull_request(42, {DOC: "changed\n"})
    git(lovverk.clone, "fetch", "-q", "origin", "refs/pull/42/head")
    other = lovverk.pull_request(43, {DOC: "other\n"})
    git(lovverk.origin, "update-ref", f"refs/owner-changes/{tip}/1", other)
    pin = Pin(1, tip, 42, 1, "x")
    with pytest.raises(PinRefusedError) as caught:
        _record_attempt(lovverk.clone, pin)
    assert caught.value.code == "attempt-not-recorded"
    assert lovverk.remote_ref(attempt_ref(pin)) == other


def test_pin_without_shared_history_is_refused_not_fatal(lovverk: Lovverk) -> None:
    orphan = lovverk.owner
    git(orphan, "checkout", "-q", "--orphan", "unrelated")
    _write(orphan, {DOC: "unrelated\n"})
    tip = _commit(orphan, "unrelated root")
    git(orphan, "push", "-q", "-f", "origin", "HEAD:refs/pull/44/head")
    assert _refusal(lovverk, lovverk.pins(f"{tip} 44 1 x")) == "no-merge-base"


def test_detached_head_is_an_error(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "changed\n"})
    git(lovverk.clone, "checkout", "-q", "--detach")
    with pytest.raises(LovsporError, match="detached HEAD"):
        apply_pins(lovverk.clone, lovverk.pins(f"{tip} 42 1 x"), CONTEXT)


def test_check_applied_reports_a_file_the_sync_rewrote(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "owner\n"})
    report = apply_pins(lovverk.clone, lovverk.pins(f"{tip} 42 1 x"), CONTEXT)
    report_file = lovverk.tmp / "report.json"
    write_report(report, report_file)
    assert check_applied(lovverk.clone, report_file) == []
    _write(lovverk.clone, {DOC: "rewritten\n"})
    _commit(lovverk.clone, "sync")
    assert check_applied(lovverk.clone, report_file) == [f"lovverk#42: {DOC}"]


def _cli(*args: str) -> Any:
    return CliRunner().invoke(app, ["ops", "owner-changes", *args])


def test_cli_apply_and_check_applied(lovverk: Lovverk) -> None:
    tip = lovverk.pull_request(42, {DOC: "owner\n"})
    report = lovverk.tmp / "report.json"
    corpus = str(lovverk.clone)
    pins = str(lovverk.pins(f"{tip} 42 1 x"))
    result = _cli(
        "apply",
        "--corpus-path",
        corpus,
        "--report",
        str(report),
        "--file",
        pins,
        "--run",
        f"{'b' * 40}:9",
    )
    assert result.exit_code == 0, result.output
    assert f"applied owner change lovverk#42 {tip} attempt 1" in result.stdout
    assert json.loads(report.read_text())["applied"][0]["merge"]
    assert _cli("check-applied", "--corpus-path", corpus, "--report", str(report)).exit_code == 0
    _write(lovverk.clone, {DOC: "rewritten\n"})
    _commit(lovverk.clone, "sync")
    assert _cli("check-applied", "--corpus-path", corpus, "--report", str(report)).exit_code == 1


def test_cli_apply_with_the_committed_file_reports_nothing(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    result = _cli(
        "apply",
        "--corpus-path",
        str(tmp_path),
        "--report",
        str(report),
        "--file",
        str(ROOT / "ops/lovverk-owner-changes"),
    )
    assert result.exit_code == 0
    assert result.stdout == "no pinned owner change\n"
    assert json.loads(report.read_text()) == {
        "applied": [],
        "file_problems": [],
        "refused": [],
        "skipped": [],
    }


def _report_step() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load((ROOT / ".github/workflows/sync.yml").read_text())
    return next(
        step
        for step in workflow["jobs"]["sync"]["steps"]
        if step.get("name") == "Report pinned owner changes not applied"
    )


@pytest.mark.parametrize(
    "existing", ["", "Pinned owner change not applied: lovverk#42 {tip} attempt 1"]
)
def test_report_step_files_each_refusal_once(tmp_path: Path, existing: str) -> None:
    tip = "c" * 40
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "file_problems": [],
                "applied": [],
                "skipped": [],
                "refused": [
                    {
                        "pin": {"pull_request": 42, "commit": tip, "attempt": 1},
                        "code": "overlap",
                        "detail": "also changed on main",
                    }
                ],
            }
        )
    )
    log = tmp_path / "gh.log"
    fake = tmp_path / "gh"
    fake.write_text(
        '#!/bin/sh\necho "$@" >> "$GH_LOG"\n'
        'if [ "$2" = list ]; then printf "%s\\n" "$EXISTING"; fi\n'
    )
    fake.chmod(0o755)
    step = _report_step()
    assert step["continue-on-error"] is True
    assert step["if"] == "${{ !cancelled() && steps.owner_changes.outcome == 'success' }}"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GH_LOG": str(log),
            "EXISTING": existing.format(tip=tip),
            "REPORT": str(report),
            "RUN_URL": "https://example.invalid/run",
            "GITHUB_REPOSITORY": "o/r",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    created = [line for line in log.read_text().splitlines() if line.startswith("issue create")]
    assert len(created) == (0 if existing else 1)
    if not existing:
        assert f"Pinned owner change not applied: lovverk#42 {tip} attempt 1" in created[0]
