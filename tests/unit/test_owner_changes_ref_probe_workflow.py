"""The one-time owner-change ref probe: its steps, run against a local bare repository."""

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/owner-changes-ref-probe.yml"
PROBE_REF = "refs/owner-changes/probe-20261125/1"


def _workflow() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return loaded


def _job() -> dict[str, Any]:
    job: dict[str, Any] = _workflow()["jobs"]["probe"]
    return job


def _step(name: str) -> str:
    run: str = next(step["run"] for step in _job()["steps"] if step["name"] == name)
    return run


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    config = tmp_path / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    _git(
        work,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "sealed head",
    )
    bare = tmp_path / "lovverk.git"
    _git(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    _git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
    _git(bare, "update-ref", "refs/tags/sync/seal-1-1", "HEAD")
    return bare, _git(bare, "rev-parse", "HEAD")


def _run(
    step: str, tmp_path: Path, bare: Path, target: str, created: str = "true"
) -> subprocess.CompletedProcess[str]:
    env = _job()["env"]
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", _step(step)],
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path / "runner"),
            "LOVVERK_URL": f"file://{bare}",
            "PROBE_REF": env["PROBE_REF"],
            "TARGET": target,
            "WATCHED": env["WATCHED"],
            "CREATED": created,
        },
        capture_output=True,
        text=True,
        check=False,
    )


_SEQUENCE = [
    "Record remote refs before",
    "Fetch the target commit",
    "Create the probe ref, create-only",
    "Check a repeated create cannot overwrite it (dry run, no write)",
    "Confirm nothing else changed",
]


def test_probe_is_manual_main_only_and_scoped_to_the_sync_environment() -> None:
    workflow = _workflow()
    assert set(workflow[True]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {}
    job = _job()
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["environment"] == "lovverk-sync"
    assert all("uses" not in step for step in job["steps"])
    assert job["steps"][-1]["if"] == "always()"
    assert job["env"]["PROBE_REF"] == PROBE_REF
    assert [step["name"] for step in job["steps"][2:-1]] == _SEQUENCE


def test_probe_ref_can_never_be_a_real_attempt_ref() -> None:
    """Real attempt refs are refs/owner-changes/<40 hex>/<attempt>."""
    middle = PROBE_REF.split("/")[2]
    assert len(middle) != 40 or not all(c in "0123456789abcdef" for c in middle)


def test_exactly_one_step_writes() -> None:
    writes = [
        step["name"]
        for step in _job()["steps"]
        for line in step.get("run", "").splitlines()
        if re.search(r"\bpush --", line) and "--dry-run" not in line
    ]
    assert writes == ["Create the probe ref, create-only"]
    assert '--force-with-lease="$PROBE_REF:"' in _step("Create the probe ref, create-only")


def test_full_sequence_creates_only_the_probe_ref(tmp_path: Path, remote: tuple[Path, str]) -> None:
    bare, target = remote
    (tmp_path / "runner").mkdir()
    before = _git(bare, "for-each-ref", "--format=%(objectname) %(refname)")
    for step in _SEQUENCE:
        result = _run(step, tmp_path, bare, target)
        assert result.returncode == 0, f"{step}\n{result.stdout}\n{result.stderr}"
    after = _git(bare, "for-each-ref", "--format=%(objectname) %(refname)")
    assert sorted(after.splitlines()) == sorted([*before.splitlines(), f"{target} {PROBE_REF}"])


def test_second_dispatch_stops_before_any_push(tmp_path: Path, remote: tuple[Path, str]) -> None:
    bare, target = remote
    (tmp_path / "runner").mkdir()
    _git(bare, "update-ref", PROBE_REF, target)
    result = _run("Record remote refs before", tmp_path, bare, target)
    assert result.returncode == 1
    assert "already exists" in result.stdout


def test_create_step_fails_if_the_ref_appeared_meanwhile(
    tmp_path: Path, remote: tuple[Path, str]
) -> None:
    """The server refuses the create-only push; the ref keeps its value."""
    bare, target = remote
    (tmp_path / "runner").mkdir()
    assert _run("Fetch the target commit", tmp_path, bare, target).returncode == 0
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    _git(
        other,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "someone else",
    )
    _git(other, "push", "-q", "origin", f"HEAD:{PROBE_REF}")
    planted = _git(bare, "rev-parse", PROBE_REF)
    result = _run("Create the probe ref, create-only", tmp_path, bare, target)
    assert result.returncode == 1
    assert _git(bare, "rev-parse", PROBE_REF) == planted


def test_final_check_fails_if_main_moved(tmp_path: Path, remote: tuple[Path, str]) -> None:
    bare, target = remote
    (tmp_path / "runner").mkdir()
    for step in _SEQUENCE[:-1]:
        assert _run(step, tmp_path, bare, target).returncode == 0
    work = tmp_path / "mover"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    _git(
        work,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "moved",
    )
    _git(work, "push", "-q", "origin", "HEAD:main")
    assert _run(_SEQUENCE[-1], tmp_path, bare, target).returncode == 1


def test_final_check_runs_after_a_failed_write_and_expects_no_probe(
    tmp_path: Path, remote: tuple[Path, str]
) -> None:
    bare, target = remote
    (tmp_path / "runner").mkdir()
    final = next(step for step in _job()["steps"] if step["name"] == _SEQUENCE[-1])
    assert final["if"] == "always() && steps.before.outcome == 'success'"
    assert final["env"]["CREATED"] == "${{ steps.create.outcome == 'success' }}"
    assert _run(_SEQUENCE[0], tmp_path, bare, target).returncode == 0
    assert _run(_SEQUENCE[-1], tmp_path, bare, target, created="false").returncode == 0
    assert _run(_SEQUENCE[-1], tmp_path, bare, target, created="true").returncode == 1


def test_probe_runs_one_at_a_time() -> None:
    assert _workflow()["concurrency"] == {
        "group": "owner-changes-ref-probe",
        "cancel-in-progress": False,
    }


def test_dry_run_commit_is_a_descendant_of_the_target(
    tmp_path: Path, remote: tuple[Path, str]
) -> None:
    """The plain-push record is about a fast-forward, so the probe commit must
    sit on top of the target, not on an unrelated root."""
    bare, target = remote
    (tmp_path / "runner").mkdir()
    for step in _SEQUENCE[:4]:
        result = _run(step, tmp_path, bare, target)
        assert result.returncode == 0, f"{step}\n{result.stdout}\n{result.stderr}"
    probe = tmp_path / "runner" / "probe"
    assert _git(probe, "rev-parse", "HEAD^") == target
    plain = _run(_SEQUENCE[3], tmp_path, bare, target)
    assert "plain push, descendant" in plain.stdout
