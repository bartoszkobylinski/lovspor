"""``identity-apply`` pins the reviewed plan, separates its dry run, and keeps every path it
touches inside the checkout (#910).

Real temporary repositories and plan directories written by ``identity-plan``.
"""

import json
import shutil
from pathlib import Path

import pytest

from lovspor.errors import IdentityMigrationError
from lovspor.identity.apply_commands import identity_apply_impl, verified_plan_files
from lovspor.identity.apply_guards import require_confined, require_pinned, require_safe_names
from lovspor.identity.report import SUMS_FILENAME
from tests.unit.identity_git_fixtures import commit_all, git
from tests.unit.test_identity_apply_command import (
    _apply,
    _invoke,
    _pin,
    _plan,
    _status,
    _tagged,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The #871 base repository with its U1 threshold tagged ``u1``."""
    return _tagged(tmp_path)


def test_a_dry_run_checks_everything_and_writes_nothing(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    report = json.loads((plan_dir / "report.json").read_text(encoding="utf-8"))

    code, output = _apply(repo, plan_dir, "--dry-run")

    assert code == 0, output
    summary = json.loads(output.strip().splitlines()[-1])
    assert summary["dry_run"] is True
    assert summary["verified_files"] == 0
    assert summary["changed_paths_sha256"] == report["changed_paths_sha256"]
    assert _status(repo) == []
    assert _apply(repo, plan_dir)[0] == 0
    assert _status(repo) == report["changed_paths"]


def test_a_dry_run_refuses_what_the_apply_refuses(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    (repo / "stray.txt").write_text("x\n", encoding="utf-8")

    code, output = _apply(repo, plan_dir, "--dry-run")

    assert code == 2
    assert "not clean" in output


def test_apply_without_a_pin_is_refused(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")

    code, _ = _invoke("identity-apply", "--repo", str(repo), "--plan", str(plan_dir))

    assert code != 0
    assert _status(repo) == []


@pytest.mark.parametrize("pin", ["f" * 64, "F" * 64, "abc", ""])
def test_a_pin_other_than_the_reviewed_plan_is_refused(
    repo: Path, tmp_path: Path, pin: str
) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")

    code, output = _apply(repo, plan_dir, pin=pin)

    assert code == 2
    assert "reviewed" in output or "not a sha256" in output
    assert _status(repo) == []


def test_another_valid_plan_is_refused_under_the_reviewed_pin(repo: Path, tmp_path: Path) -> None:
    """A plan this engine computes, but not the one reviewed: no threshold instead of ``u1``."""
    reviewed = _plan(repo, tmp_path / "reviewed")
    other = ("--base", "HEAD", "--u1-threshold", "HEAD~3", "--out", str(tmp_path / "other"))
    code, output = _invoke("identity-plan", "--repo", str(repo), *other)
    assert code == 0, output
    assert _pin(tmp_path / "other") != _pin(reviewed)

    code, output = _apply(repo, tmp_path / "other", pin=_pin(reviewed))

    assert code == 2
    assert "not the reviewed" in output
    assert _status(repo) == []


@pytest.mark.parametrize("name", ["../evil.json", "/etc/passwd", "planned/../../evil"])
def test_a_plan_entry_outside_the_plan_directory_is_refused(
    repo: Path, tmp_path: Path, name: str
) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    sums = plan_dir / SUMS_FILENAME
    sums.write_text(sums.read_text(encoding="utf-8") + f"{'0' * 64}  {name}\n", encoding="utf-8")

    code, output = _apply(repo, plan_dir)

    assert code == 2
    assert "unsafe path" in output
    assert _status(repo) == []


@pytest.mark.parametrize(
    "name",
    ["../x", "/abs/x", "a/../../x", "a//b", "./a", "a\\..\\b", ".git/hooks/pre-commit", ""],
)
def test_unsafe_names_are_refused(name: str) -> None:
    with pytest.raises(IdentityMigrationError, match="unsafe path"):
        require_safe_names([name])


def test_a_symlink_out_of_the_checkout_is_refused(tmp_path: Path) -> None:
    """The slug histories the plan writes, under a committed symlink to another directory."""
    corpus = _tagged(tmp_path)
    outside = tmp_path / "outside"
    by_id = corpus / "forskrifter/history/by-id"
    if by_id.exists():
        shutil.move(by_id, outside)
    else:
        outside.mkdir()
    by_id.symlink_to(outside, target_is_directory=True)
    commit_all(corpus, "history through a symlink")
    before = sorted(p.name for p in outside.iterdir())
    plan_dir = _plan(corpus, tmp_path / "plan")
    report = json.loads((plan_dir / "report.json").read_text(encoding="utf-8"))
    assert any(p.startswith("forskrifter/history/by-id/") for p in report["changed_paths"])

    code, output = _apply(corpus, plan_dir)

    assert code == 2
    assert "leading out of" in output
    assert sorted(p.name for p in outside.iterdir()) == before
    assert git(corpus, "status", "--porcelain") == ""


def test_paths_inside_the_checkout_pass(tmp_path: Path) -> None:
    (tmp_path / "lover").mkdir()

    require_confined(tmp_path, ["lover/a.md", "lover/history/a.json", "forskrifter/new.md"])


@pytest.mark.parametrize(
    "name", [".GIT/history/x.json", ".Git/config", "lover/.git/x.json", "lover/.GIT/hooks/x.md"]
)
def test_a_git_directory_anywhere_in_any_case_is_refused(name: str) -> None:
    """Case-insensitive file systems (default APFS) treat ``.GIT`` as ``.git``."""
    with pytest.raises(IdentityMigrationError, match="unsafe path"):
        require_safe_names([name])


def test_a_symlink_inside_the_checkout_is_refused_part_way_down(tmp_path: Path) -> None:
    """An in-repo symlink to ``.git`` part-way down the path must not pass as confined."""
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / "lover" / "history").mkdir(parents=True)
    (tmp_path / "lover" / "history" / "link").symlink_to("../../.git", target_is_directory=True)

    with pytest.raises(IdentityMigrationError, match="leading out of"):
        require_confined(tmp_path, ["lover/history/link/hooks/x.json"])


def test_a_symlink_to_another_checkout_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "forskrifter").mkdir()
    (tmp_path / "lover").symlink_to("forskrifter", target_is_directory=True)

    with pytest.raises(IdentityMigrationError, match="leading out of"):
        require_confined(tmp_path, ["lover/a.md"])


def test_the_pinned_bytes_are_the_bytes_parsed(repo: Path, tmp_path: Path) -> None:
    """``SHA256SUMS`` is read once: the bytes hashed for the pin are the bytes parsed."""
    plan_dir = _plan(repo, tmp_path / "plan")
    raw = (plan_dir / SUMS_FILENAME).read_bytes()
    (plan_dir / SUMS_FILENAME).write_text("garbage\n", encoding="utf-8")

    sums = verified_plan_files(plan_dir, raw)

    assert sums and all(len(digest) == 64 for digest in sums.values())


def test_require_pinned_returns_the_hashed_bytes(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")

    raw = require_pinned(plan_dir, _pin(plan_dir))

    assert raw == (plan_dir / SUMS_FILENAME).read_bytes()


def test_an_os_error_while_writing_is_reported_as_partially_written(
    repo: Path, tmp_path: Path
) -> None:
    """A write failure (a read-only tree) is exit 3 with the documented summary, no traceback."""
    plan_dir = _plan(repo, tmp_path / "plan")
    tree = [d for d in repo.rglob("*") if d.is_dir() and ".git" not in d.parts]
    for directory in [repo, *tree]:
        directory.chmod(0o555)
    try:
        code, summary = identity_apply_impl(repo, plan_dir, _pin(plan_dir), False)
    finally:
        for directory in [repo, *tree]:
            directory.chmod(0o755)

    assert code == 3
    assert "partially written" in summary
