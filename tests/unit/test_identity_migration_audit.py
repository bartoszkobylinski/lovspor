"""A complete applied identity migration passes both corpus gates (#912).

The real corpus has every tombstone shape the plan restores: a restored first
holder under a current threshold-exception owner, a base tombstone that took
a restored owner's slug after the threshold, and a current later holder the
plan renames. Each restored record shares a ``markdown_path`` with another
record, so the applied migration must not leave two records on one path:
``lovspor audit`` flags that as ``duplicate_path_ownership`` and lovverk's
``check_corpus_integrity.py`` refuses a removed record whose file is on disk.
"""

import json
from collections import Counter
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.identity.apply import apply_plan
from lovspor.identity.plan import plan_migration
from lovspor.identity.registry import THRESHOLD_EXCEPTION
from lovspor.identity.report import write_report
from lovspor.storage.manifest import read_manifest
from tests.unit.identity_git_fixtures import (
    commit_all,
    doc_text,
    init_repo,
    record,
    write_doc,
    write_manifest_json,
)

KEEP = "sf-20250101-0001"  # an ordinary current document
X_OLD = "sf-20250101-0002"  # first holder of x, removed, record lost
X_NEW = "sf-20260601-0003"  # took x before the threshold: owner by exception, current
Y_OWNER = "sf-20250101-0004"  # first holder of y, removed, record lost
Y_LATER = "sf-20260901-0005"  # took y after the threshold, removed: a base tombstone
Z_OWNER = "sf-20250101-0006"  # first holder of z, removed, record lost
Z_LATER = "sf-20260901-0007"  # took z after the threshold, current: renamed (U1)


def _put(repo: Path, slug: str, doc_id: str) -> None:
    write_doc(repo, f"forskrifter/{slug}.md", doc_text(doc_id, slug, f"body of {doc_id}"))
    payload = {"doc_id": doc_id, "events": [], "schema_version": 1, "slug": slug}
    write_doc(repo, f"forskrifter/history/{slug}.json", json.dumps(payload) + "\n")


def _seed(repo: Path) -> None:
    for slug, doc_id in (("c", KEEP), ("x", X_OLD), ("y", Y_OWNER), ("z", Z_OWNER)):
        _put(repo, slug, doc_id)
    docs = {d: record(d, s) for s, d in (("c", KEEP), ("x", X_OLD), ("y", Y_OWNER), ("z", Z_OWNER))}
    write_manifest_json(repo, docs, "2026-05-01T00:00:00Z")
    commit_all(repo, "sync: seed")
    for slug in ("x", "y", "z"):
        (repo / f"forskrifter/{slug}.md").unlink()
    removed = {
        d: record(d, s, "removed") for s, d in (("x", X_OLD), ("y", Y_OWNER), ("z", Z_OWNER))
    }
    write_manifest_json(repo, {KEEP: record(KEEP, "c"), **removed}, "2026-05-02T00:00:00Z")
    commit_all(repo, "remove(forskrift): x, y, z")
    write_manifest_json(repo, {KEEP: record(KEEP, "c")}, "2026-07-02T00:00:00Z")
    commit_all(repo, "sync: update manifest, index, and history")


def _build(tmp_path: Path) -> tuple[Path, str]:
    repo = init_repo(tmp_path / "lovverk")
    _seed(repo)
    _put(repo, "x", X_NEW)
    write_manifest_json(
        repo, {KEEP: record(KEEP, "c"), X_NEW: record(X_NEW, "x")}, "2026-08-01T00:00:00Z"
    )
    threshold = commit_all(repo, "add(forskrift): x")
    for slug, doc_id in (("y", Y_LATER), ("z", Z_LATER)):
        _put(repo, slug, doc_id)
    later = {
        X_NEW: record(X_NEW, "x"),
        Y_LATER: record(Y_LATER, "y"),
        Z_LATER: record(Z_LATER, "z"),
    }
    write_manifest_json(repo, {KEEP: record(KEEP, "c"), **later}, "2026-09-01T00:00:00Z")
    commit_all(repo, "add(forskrift): y, z")
    (repo / "forskrifter/y.md").unlink()
    later[Y_LATER] = record(Y_LATER, "y", "removed")
    write_manifest_json(repo, {KEEP: record(KEEP, "c"), **later}, "2026-10-01T00:00:00Z")
    commit_all(repo, "remove(forskrift): y")
    return repo, threshold


@pytest.fixture
def applied(tmp_path: Path) -> tuple[Path, str]:
    """The corpus after the full plan was applied and committed, and its threshold."""
    repo, threshold = _build(tmp_path)
    plan = plan_migration(repo, "HEAD", threshold)
    assert not plan.blocked
    assert [(r.doc_id, r.to_slug) for r in plan.renames] == [(Z_LATER, "z-2026")]
    assert plan.planned_registry.slugs["forskrifter"]["x"].basis == THRESHOLD_EXCEPTION
    apply_plan(repo, plan)
    commit_all(repo, "migration: repair document identity (#871)")
    return repo, threshold


@pytest.fixture
def migrated(applied: tuple[Path, str]) -> Path:
    return applied[0]


def test_the_applied_migration_passes_lovspor_audit(migrated: Path) -> None:
    result = CliRunner().invoke(app, ["audit", "--corpus-path", str(migrated)])

    assert result.exit_code == 0, result.output
    assert "duplicate_path_ownership" not in result.output


def test_no_two_records_share_a_markdown_path(migrated: Path) -> None:
    documents = read_manifest(migrated / "manifest.json").documents

    shared = [p for p, n in Counter(r.markdown_path for r in documents.values()).items() if n > 1]
    assert shared == []


def test_no_removed_record_points_at_a_file_on_disk(migrated: Path) -> None:
    """lovverk ``check_corpus_integrity.py``: no removed record still has its file on disk."""
    documents = read_manifest(migrated / "manifest.json").documents

    on_disk = [
        d
        for d, r in documents.items()
        if r.status == "removed" and (migrated / r.markdown_path).exists()
    ]
    assert on_disk == []


def test_a_tombstone_keeps_its_address_when_its_path_is_detached(migrated: Path) -> None:
    documents = read_manifest(migrated / "manifest.json").documents

    assert (documents[X_NEW].status, documents[X_NEW].markdown_path) == (
        "current",
        "forskrifter/x.md",
    )
    assert documents[X_OLD].slug == "x"
    assert documents[X_OLD].markdown_path == f"forskrifter/removed/{X_OLD}.md"
    assert (documents[Y_OWNER].slug, documents[Y_OWNER].markdown_path) == ("y", "forskrifter/y.md")
    assert documents[Y_LATER].slug == "y"
    assert documents[Y_LATER].markdown_path == f"forskrifter/removed/{Y_LATER}.md"
    assert documents[Y_LATER].removed_reason is None  # a base record keeps its own reason
    assert documents[Z_OWNER].markdown_path == "forskrifter/z.md"  # alone once z moved


def test_a_second_plan_on_the_migrated_corpus_has_nothing_to_do(
    applied: tuple[Path, str],
) -> None:
    again = plan_migration(applied[0], "HEAD", applied[1])

    assert again.nothing_to_do
    assert not again.blocked
    assert again.changed_paths == []


def test_the_report_lists_every_detached_tombstone(tmp_path: Path) -> None:
    repo, threshold = _build(tmp_path)

    write_report(plan_migration(repo, "HEAD", threshold), tmp_path / "out")

    data = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert data["counts"]["detached_tombstone_paths"] == 2
    assert data["detached_tombstones"] == [
        {"doc_id": X_OLD, "markdown_path": f"forskrifter/removed/{X_OLD}.md"},
        {"doc_id": Y_LATER, "markdown_path": f"forskrifter/removed/{Y_LATER}.md"},
    ]
