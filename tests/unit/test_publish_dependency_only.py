"""The source-vs-representation split, end to end (ADR-0013 Validation).

A document's ``source_revision`` moves only with its own Markdown; its
``representation_hash`` moves whenever its emitted HTML does, including for
a change that lives entirely in a document it references. Two corpus
commits are built and the referencing document's companion is compared
across them, for the ADR's dependency-only fixture (a referenced
document's slug is renamed) and for the dependant-set case the churn test
leaves unexercised (a cross-reference that newly resolves).
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lovspor.publish.emit import emit_site
from tests.unit.test_publish_emit import DOC, REGULATION, _record, _run_git

REFERENCING = "lov/testloven"
OLD_TARGET = '<a href="/forskrift/testforskriften/paragraf/2/">'
NEW_TARGET = '<a href="/forskrift/testforskriften-ny/paragraf/2/">'


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_manifest(repo: Path, documents: dict[str, Any], message: str) -> str:
    manifest = {"version": 1, "generated_at": "2026-01-01T00:00:00Z", "documents": documents}
    (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", message)
    return _head(repo)


def _law() -> dict[str, object]:
    return _record("lov", "lover/testloven.md", "testloven", "Testloven")


def _regulation(slug: str) -> dict[str, object]:
    return _record("forskrift", "forskrifter/testforskriften.md", slug, "Testforskriften")


def _init(tmp_path: Path) -> Path:
    repo = tmp_path / "lovverk"
    (repo / "lover").mkdir(parents=True)
    (repo / "forskrifter").mkdir()
    _run_git(repo, "init", "-q")
    (repo / "lover/testloven.md").write_text(DOC, encoding="utf-8")
    return repo


def _add_regulation(repo: Path) -> None:
    (repo / "forskrifter/testforskriften.md").write_text(REGULATION, encoding="utf-8")


def _build(repo: Path, sha: str, out: Path) -> tuple[dict[str, Any], str]:
    emit_site(repo, sha, out)
    page = out / REFERENCING
    companion = json.loads((page / "index.json").read_text(encoding="utf-8"))
    return companion["provenance"], (page / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def slug_rename(tmp_path: Path) -> tuple[Path, str, str]:
    repo = _init(tmp_path)
    _add_regulation(repo)
    before = _commit_manifest(
        repo, {"doc-1": _law(), "doc-2": _regulation("testforskriften")}, "both"
    )
    after = _commit_manifest(
        repo, {"doc-1": _law(), "doc-2": _regulation("testforskriften-ny")}, "rename"
    )
    return repo, before, after


@pytest.fixture
def newly_resolving(tmp_path: Path) -> tuple[Path, str, str]:
    repo = _init(tmp_path)
    before = _commit_manifest(repo, {"doc-1": _law()}, "law alone")
    _add_regulation(repo)
    after = _commit_manifest(
        repo, {"doc-1": _law(), "doc-2": _regulation("testforskriften")}, "target lands"
    )
    return repo, before, after


class TestReferencedSlugRename:
    def test_source_revision_stays_while_representation_hash_moves(
        self, slug_rename: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        repo, before, after = slug_rename
        old, _ = _build(repo, before, tmp_path / "old")
        new, _ = _build(repo, after, tmp_path / "new")

        assert new["source_revision"] == old["source_revision"]
        assert new["representation_hash"] != old["representation_hash"]

    def test_the_rewritten_href_is_what_moved(
        self, slug_rename: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        repo, before, after = slug_rename
        _, old_html = _build(repo, before, tmp_path / "old")
        _, new_html = _build(repo, after, tmp_path / "new")

        assert OLD_TARGET in old_html
        assert NEW_TARGET in new_html
        assert old_html.replace(OLD_TARGET, NEW_TARGET) == new_html

    def test_the_rename_commit_does_not_touch_the_referencing_source(
        self, slug_rename: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        repo, before, after = slug_rename
        new, _ = _build(repo, after, tmp_path / "new")

        assert new["source_revision"] == before
        assert new["source_revision"] != after


class TestCrossReferenceNewlyResolves:
    def test_source_revision_stays_while_representation_hash_moves(
        self, newly_resolving: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        repo, before, after = newly_resolving
        old, _ = _build(repo, before, tmp_path / "old")
        new, _ = _build(repo, after, tmp_path / "new")

        assert new["source_revision"] == old["source_revision"] == before
        assert new["representation_hash"] != old["representation_hash"]

    def test_the_reference_turns_from_text_into_a_link(
        self, newly_resolving: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        repo, before, after = newly_resolving
        _, old_html = _build(repo, before, tmp_path / "old")
        _, new_html = _build(repo, after, tmp_path / "new")

        assert "forskriften § 2" in old_html
        assert OLD_TARGET not in old_html
        assert OLD_TARGET in new_html
