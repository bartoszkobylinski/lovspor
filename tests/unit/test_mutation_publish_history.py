"""Observable continuity and history contracts for the reported mutation surface."""

import json
import subprocess
from pathlib import Path

import pytest

from lovspor.publish.check import check_continuity
from lovspor.publish.inventory import PublishError
from lovspor.publish.provision_history import (
    _blob,
    _git_text,
    once_publishable_pids,
    parse_removed_pids,
)
from tests.unit.test_publish_redirects import _doc, _git, _head, _repo


def _tree(root: Path, redirects: dict[str, str], gone: list[str]) -> Path:
    root.mkdir()
    (root / "lov/base").mkdir(parents=True)
    (root / "lov/base/index.html").write_text("base")
    (root / "lov/base/index.json").write_text("{}")
    (root / "redirect-map.json").write_text(
        json.dumps(
            {"redirects": [{"from": k, "to": v} for k, v in redirects.items()], "gone": gone}
        ),
        encoding="utf-8",
    )
    return root


def test_previous_redirects_are_counted_individually(tmp_path: Path) -> None:
    previous = _tree(tmp_path / "previous", {"/old-a/": "/new/", "/old-b/": "/new/"}, [])
    current = _tree(tmp_path / "current", {"/old-a/": "/new/", "/old-b/": "/new/"}, [])
    (current / "new").mkdir()
    (current / "new/index.html").write_text("live")
    report = check_continuity(previous, current)
    assert (report.live, report.moved, report.gone) == (2, 2, 0)


def test_twin_mismatch_names_both_actual_fates(tmp_path: Path) -> None:
    previous = _tree(tmp_path / "previous", {}, [])
    (previous / "lov/a").mkdir(parents=True)
    (previous / "lov/a/index.html").write_text("old")
    current = _tree(tmp_path / "current", {"/lov/a/index.json": "/lov/base/index.json"}, [])
    (current / "lov/a").mkdir(parents=True)
    (current / "lov/a/index.html").write_text("live")
    with pytest.raises(PublishError) as error:
        check_continuity(previous, current)
    assert str(error.value) == (
        "/lov/a/ answers live but its JSON twin answers moved: "
        "HTML and JSON name one entity and must share one fate"
    )


@pytest.mark.parametrize("path", ["lover/a.md ", "lover/aX"])
def test_removed_heading_preserves_path_characters(path: str) -> None:
    assert parse_removed_pids(f"--- a/{path}\t\n@@ -1 +0,0 @@\n-### § 3. T\n") == {
        path: frozenset({"3"})
    }


def test_hunk_without_old_path_is_not_evidence() -> None:
    assert parse_removed_pids("@@ -1 +0,0 @@\n-### § 3. T\n") == {}


def test_initial_header_and_heading_levels() -> None:
    assert parse_removed_pids("--- a/lover/a.md\n@@ -1 +0,0 @@\n-## § 3. T\n") == {
        "lover/a.md": frozenset({"3"})
    }


def test_git_errors_are_not_empty_history(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        once_publishable_pids(repo, "missing-revision", "lover/a.md")


def test_git_paths_and_blob_text_are_unicode(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = "lover/blå.md"
    (repo / path).write_text(_doc("Blå", "lov/2020-01-01-1", ("3",)), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "document", day=1)
    sha = _head(repo)
    assert _git_text(repo, ["ls-tree", "--name-only", "-r", sha]) == path + "\n"
    assert any("Blå" in line for line in _blob(repo, f"{sha}:{path}"))
    assert once_publishable_pids(repo, sha, path) == frozenset({"3"})
    assert _blob(repo, f"{sha}:lover/absent.md") == []


def test_history_commit_ids_cannot_be_shadowed_by_short_named_refs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    path = "lover/a.md"
    document = repo / path
    document.write_text(_doc("A", "lov/2020-01-01-1", ("1",)), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first", day=1)
    first = _head(repo)
    document.write_text(_doc("A", "lov/2020-01-01-1", ("2",)), encoding="utf-8")
    _git(repo, "commit", "-qam", "second", day=2)
    sha = _head(repo)
    short = _git_text(repo, ["rev-parse", "--short", sha]).strip()
    _git(repo, "tag", short, first)
    assert once_publishable_pids(repo, sha, path) == frozenset({"1", "2"})


def test_empty_redirect_target_does_not_resolve_to_an_unrelated_file(tmp_path: Path) -> None:
    previous = _tree(tmp_path / "previous", {"/old/": "/lov/base/"}, [])
    current = _tree(tmp_path / "current", {"/old/": ""}, [])
    (current / "XXXX").write_text("unrelated")
    with pytest.raises(PublishError, match="which is not a live page"):
        check_continuity(previous, current)
