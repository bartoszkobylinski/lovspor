"""Issue #220: a partial Codex round reports what it found, and no model stands in.

Owner decision 2026-09-29: (b) no model fallback in the test author. A partial
round surfaces the failing tests it already found in the sticky comment.

A partial round is a `codex-author` step that ended in failure after Codex had
started working: the provider refused the model mid-round ("Selected model is
at capacity"), the step hit its ceiling, or the CLI died. Before it died the
agent may have run pytest on its own tests; those `FAILED <nodeid>` lines exist
only in the author's output, so the step keeps that output in RUNNER_TEMP and a
failure-only step turns it into the section the escalation appends.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO / ".github" / "workflows" / "pr-pipeline.yml"
_HELPER = _REPO / "scripts" / "ci" / "partial_round_failures.py"
_SYSTEM_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
_PYTHON_DIR = Path(sys.executable).parent
_SHA = "b" * 40
_RUN_URL = "https://github.com/o/r/actions/runs/7"
_AUTHOR_STEP = "Codex — independent PR test author"
_COLLECT = "Collect the failing tests a partial round already found"
_DOWNLOAD = "Download the partial round's findings"
_ESCALATE = "Escalate — the pipeline failed before the tests ran"
_CAPACITY = "ERROR: Selected model is at capacity. Please try a different model."
# Verbatim from the second PR #217 round quoted in the issue.
_FOUND = (
    "FAILED tests/unit/test_observatory_registry.py::TestDomainsClaimedTwice::"
    "test_one_parent_with_two_subdomain_claims_reports_every_claimant"
    " - AssertionError: assert ['7777', '9999'] == ['7777', '8888', '9999']"
)


def _steps(job_name: str) -> list[dict[str, Any]]:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return list(workflow["jobs"][job_name]["steps"])


def _named_step(job_name: str, name: str) -> dict[str, Any]:
    return next(step for step in _steps(job_name) if step.get("name") == name)


def _render(script: str, values: dict[str, str]) -> str:
    """Substitute the step's ${{ }} expressions; an unknown one fails the test."""
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: values[m.group(1)], script)


def _run_step(script: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a step body the way GitHub runs it: `bash -e`."""
    return subprocess.run(
        ["bash", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def _collect(tmp_path: Path, log: str | None) -> str:
    if log is not None:
        (tmp_path / "codex-author.log").write_text(log, encoding="utf-8")
    out = tmp_path / "out.md"
    args = ["--log", str(tmp_path / "codex-author.log"), "--out", str(out)]
    subprocess.run([sys.executable, str(_HELPER), *args], check=True, capture_output=True)
    return out.read_text(encoding="utf-8")


class TestTheHelperReadsTheAuthorsOwnLog:
    def test_the_failing_test_the_round_found_reaches_the_section(self, tmp_path: Path) -> None:
        log = f"exec pytest tests/unit/test_observatory_registry.py -q\n{_FOUND}\n{_CAPACITY}\n"

        section = _collect(tmp_path, log)

        assert _FOUND in section
        assert "#220" in section
        assert _CAPACITY not in section

    def test_a_round_that_found_nothing_adds_nothing(self, tmp_path: Path) -> None:
        assert _collect(tmp_path, f"exec pytest\n3 passed in 0.4s\n{_CAPACITY}\n") == ""

    def test_a_round_with_no_log_at_all_adds_nothing(self, tmp_path: Path) -> None:
        assert _collect(tmp_path, None) == ""

    def test_timestamps_colour_and_repeats_are_dropped(self, tmp_path: Path) -> None:
        log = (
            "2026-08-31T08:20:01.1Z \x1b[31mFAILED\x1b[0m tests/unit/test_a.py::test_x - E\n"
            "2026-08-31T08:21:09.4Z FAILED tests/unit/test_a.py::test_x - E\n"
            "FAILED tests/unit/test_b.py::test_y\n"
        )

        lines = _collect(tmp_path, log).splitlines()

        assert lines.count("FAILED tests/unit/test_a.py::test_x - E") == 1
        assert "FAILED tests/unit/test_b.py::test_y" in lines
        assert lines.index("FAILED tests/unit/test_a.py::test_x - E") < lines.index(
            "FAILED tests/unit/test_b.py::test_y"
        )

    def test_prose_that_mentions_failed_is_not_a_finding(self, tmp_path: Path) -> None:
        assert _collect(tmp_path, "the FAILED run was retried\n1 failed, 2 passed\n") == ""

    def test_a_long_list_is_capped_and_says_how_many_it_left_out(self, tmp_path: Path) -> None:
        log = "".join(f"FAILED tests/unit/test_m.py::test_{n}\n" for n in range(45))

        section = _collect(tmp_path, log)

        assert "FAILED tests/unit/test_m.py::test_29" in section
        assert "FAILED tests/unit/test_m.py::test_30" not in section
        assert "15 more" in section

    def test_a_backtick_fence_in_a_message_cannot_close_the_block(self, tmp_path: Path) -> None:
        section = _collect(tmp_path, "FAILED tests/unit/test_c.py::test_z - ```boom\n")

        assert section.count("```") == 2


def _fake_codex(bin_dir: Path, exec_output: str) -> None:
    """`codex app-server` reports a 10% account; `codex exec` dies at capacity."""
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if sys.argv[1] == 'exec':\n"
        f"    sys.stdout.write({exec_output!r})\n"
        "    sys.exit(1)\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if message.get('id') == 0:\n"
        "        print(json.dumps({'id': 0, 'result': {}}), flush=True)\n"
        "    elif message.get('id') == 1:\n"
        "        result = {'rateLimits': {'primary': {'usedPercent': 10}}}\n"
        "        print(json.dumps({'id': 1, 'result': result}), flush=True)\n"
    )
    codex.chmod(0o755)


def _author_sandbox(tmp_path: Path) -> dict[str, str]:
    """The author step's tree: its helpers, a logged-in primary, a stubbed fallback."""
    ci = tmp_path / "work" / "scripts" / "ci"
    ci.mkdir(parents=True)
    for helper in ("codex_account_failover.py", "fd_lock.py"):
        shutil.copy(_REPO / "scripts" / "ci" / helper, ci / helper)
    fallback = ci / "claude_test_author.sh"
    fallback.write_text(f'#!/bin/sh\ntouch "{tmp_path}/fallback-ran"\n')
    fallback.chmod(0o755)
    prompt = tmp_path / "work" / ".github" / "codex"
    prompt.mkdir(parents=True)
    (prompt / "pr-tests.md").write_text("write tests\n", encoding="utf-8")
    (tmp_path / "home" / ".codex-lovspor").mkdir(parents=True)
    (tmp_path / "home" / ".codex-lovspor" / "auth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "bin").mkdir()
    _fake_codex(tmp_path / "bin", f"exec pytest\n{_FOUND}\n{_CAPACITY}\n")
    (tmp_path / "github-output").write_text("", encoding="utf-8")
    return {
        "PATH": f"{tmp_path / 'bin'}:{_PYTHON_DIR}:{_SYSTEM_PATH}",
        "HOME": str(tmp_path / "home"),
        "RUNNER_TEMP": str(tmp_path),
        "RUNNER_NAME": "test-runner",
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
    }


class TestNoModelFallbackInTheTestAuthor:
    """The owner chose (b): a capacity refusal is not routed to another model."""

    def test_a_capacity_refusal_fails_the_round_without_the_fallback_author(
        self, tmp_path: Path
    ) -> None:
        env = _author_sandbox(tmp_path)
        step = _named_step("codex-author", _AUTHOR_STEP)

        result = _run_step(step["run"], tmp_path / "work", env)

        assert result.returncode == 1
        assert not (tmp_path / "fallback-ran").exists()
        assert "author=" not in (tmp_path / "github-output").read_text(encoding="utf-8")

    def test_the_round_keeps_its_own_output_for_the_findings(self, tmp_path: Path) -> None:
        env = _author_sandbox(tmp_path)
        step = _named_step("codex-author", _AUTHOR_STEP)

        _run_step(step["run"], tmp_path / "work", env)

        log = (tmp_path / "codex-author.log").read_text(encoding="utf-8")
        assert _FOUND in log
        assert _CAPACITY in log

    def test_codex_is_never_told_to_switch_models(self) -> None:
        run = _named_step("codex-author", _AUTHOR_STEP)["run"]
        invocation = run[run.index("-- codex exec") : run.index("status=")]

        assert not re.search(r"(^|\s)(-m|--model|--profile|-c)(\s|=)", invocation)


class TestTheAuthorLanePreservesItsFindings:
    def test_the_collect_step_runs_only_on_failure_and_is_guarded(self) -> None:
        collect = _named_step("codex-author", _COLLECT)

        assert collect["if"] == "failure()"
        assert "[ -x scripts/ci/partial_round_failures.py ] ||" in collect["run"]

    def test_a_tree_without_the_helper_still_leaves_an_empty_section(self, tmp_path: Path) -> None:
        run = _named_step("codex-author", _COLLECT)["run"]
        env = {"PATH": _SYSTEM_PATH, "RUNNER_TEMP": str(tmp_path)}

        result = _run_step(run, tmp_path, env)

        assert result.returncode == 0
        assert (tmp_path / "partial-round-failures.md").read_text(encoding="utf-8") == ""

    def test_the_collect_step_writes_the_section_from_the_log(self, tmp_path: Path) -> None:
        ci = tmp_path / "scripts" / "ci"
        ci.mkdir(parents=True)
        shutil.copy(_HELPER, ci / _HELPER.name)
        (ci / _HELPER.name).chmod(0o755)
        (tmp_path / "codex-author.log").write_text(f"{_FOUND}\n{_CAPACITY}\n", encoding="utf-8")
        env = {
            "PATH": f"{_PYTHON_DIR}:{_SYSTEM_PATH}",
            "RUNNER_TEMP": str(tmp_path),
        }

        _run_step(_named_step("codex-author", _COLLECT)["run"], tmp_path, env)

        assert _FOUND in (tmp_path / "partial-round-failures.md").read_text(encoding="utf-8")

    def test_the_findings_ride_in_the_preserved_work_artifact(self) -> None:
        upload = _named_step("codex-author", "Upload preserved agent work")
        steps = [step.get("name") for step in _steps("codex-author")]

        assert "${{ runner.temp }}/partial-round-failures.md" in upload["with"]["path"]
        assert steps.index(_COLLECT) < steps.index("Upload preserved agent work")


def _escalation_sandbox(tmp_path: Path) -> dict[str, str]:
    ci = tmp_path / "scripts" / "ci"
    ci.mkdir(parents=True)
    sticky = ci / "pr_sticky_comment.sh"
    sticky.write_text(f'#!/bin/sh\ncp "$3" "{tmp_path}/body"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/gh-calls"\n')
    for executable in (sticky, gh):
        executable.chmod(0o755)
    return {"PATH": f"{bin_dir}:{_SYSTEM_PATH}", "RUNNER_TEMP": str(tmp_path)}


def _escalate(tmp_path: Path, findings: str | None) -> str:
    env = _escalation_sandbox(tmp_path)
    if findings is not None:
        (tmp_path / "author-partial").mkdir()
        (tmp_path / "author-partial" / "partial-round-failures.md").write_text(findings)
    values = {
        "github.event.pull_request.number": "217",
        "github.event.pull_request.head.sha": _SHA,
        "github.server_url": "https://github.com",
        "github.repository": "o/r",
        "github.run_id": "7",
    }
    step = _named_step("codex-tests", _ESCALATE)
    result = _run_step(_render(step["run"], values), tmp_path, env)
    assert result.returncode == 0, result.stderr
    return (tmp_path / "body").read_text(encoding="utf-8")


class TestTheStickyCommentCarriesThePartialRound:
    def test_the_found_failure_reaches_the_sticky_comment(self, tmp_path: Path) -> None:
        body = _escalate(tmp_path, f"Found before it ended (#220):\n\n```text\n{_FOUND}\n```\n")

        assert _FOUND in body
        assert f"agent-work-author-{_SHA}" in body
        assert _RUN_URL in body

    @pytest.mark.parametrize("findings", [None, ""])
    def test_a_round_that_found_nothing_keeps_the_old_comment(
        self, tmp_path: Path, findings: str | None
    ) -> None:
        body = _escalate(tmp_path, findings)

        assert "FAILED" not in body
        assert f"agent-work-author-{_SHA}" not in body
        assert "codex-tests BLOCKED before the tests ran" in body

    def test_the_download_is_best_effort_and_only_after_an_author_failure(self) -> None:
        download = _named_step("codex-tests", _DOWNLOAD)
        steps = [step.get("name") for step in _steps("codex-tests")]

        assert download["if"] == "failure() && needs.codex-author.result == 'failure'"
        assert download["continue-on-error"] is True
        assert download["with"] == {
            "name": "agent-work-author-${{ github.event.pull_request.head.sha }}",
            "path": "${{ runner.temp }}/author-partial",
        }
        assert steps.index(_DOWNLOAD) < steps.index(_ESCALATE)
