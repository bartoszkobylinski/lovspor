"""The manifest walk and registry construction of the identity migration (#871)."""

import json
from pathlib import Path

import pytest

from lovspor.errors import IdentityMigrationError
from lovspor.identity.migration import build_registry, threshold_owner
from lovspor.identity.plan import plan_migration
from lovspor.identity.registry import SlugOwner, SlugRegistry, registry_json
from lovspor.identity.schema import declaration_json
from lovspor.identity.walk import parse_revision, raw_address, walk_manifests
from tests.unit.identity_git_fixtures import (
    commit_all,
    init_repo,
    record,
    write_doc,
    write_manifest_json,
)


def test_raw_address_falls_back_to_the_file_stem() -> None:
    raw = {"markdown_path": "lover/nl-19990326-014.md", "slug": None}

    assert raw_address("nl-19990326-014", raw, "c1") == ("lover", "nl-19990326-014")


def test_raw_address_refuses_a_record_without_a_path() -> None:
    with pytest.raises(IdentityMigrationError, match="c1: record x has no markdown_path"):
        raw_address("x", {"slug": "x"}, "c1")


@pytest.mark.parametrize(
    ("blob", "message"),
    [
        (None, "no manifest.json"),
        (b'{"generated_at": "2026-05-01T00:00:00Z"}', "unreadable"),
        (b'{"generated_at": "2026-05-01T00:00:00Z", "documents": []}', "not an object"),
        (b"\xff", "unreadable"),
    ],
)
def test_parse_revision_refuses_an_unreadable_revision(blob: bytes | None, message: str) -> None:
    with pytest.raises(IdentityMigrationError, match=message):
        parse_revision(blob, "c1")


def test_parse_revision_normalises_the_timestamp() -> None:
    generated_at, documents = parse_revision(
        b'{"generated_at": "2026-05-01T00:00:00Z", "documents": {}}', "c1"
    )

    assert (generated_at, documents) == ("2026-05-01T00:00:00+00:00", {})


def test_a_base_whose_history_never_touched_the_manifest_is_refused(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_doc(repo, "README.md", "x\n")
    sha = commit_all(repo, "init")

    with pytest.raises(IdentityMigrationError, match="touches manifest.json"):
        walk_manifests(repo, sha)


def test_the_smallest_doc_id_wins_a_slug_first_held_by_two(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    docs = {"sf-2": record("sf-2", "x"), "sf-1": record("sf-1", "x")}
    write_manifest_json(repo, docs, "2026-05-01T00:00:00Z")
    sha = commit_all(repo, "seed")

    walk = walk_manifests(repo, sha)

    spans = walk.holders["forskrifter"]["x"]
    assert [(s.doc_id, s.first_commit, s.last_commit) for s in spans] == [
        ("sf-1", sha, sha),
        ("sf-2", sha, sha),
    ]
    assert threshold_owner(spans, walk, -1).doc_id == "sf-1"
    assert threshold_owner(spans, walk, 0).doc_id == "sf-1"


def test_a_document_that_comes_back_is_not_missing(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(repo, {"sf-1": record("sf-1", "x")}, "2026-05-01T00:00:00Z")
    commit_all(repo, "r1")
    write_manifest_json(repo, {}, "2026-05-02T00:00:00Z")
    gone = commit_all(repo, "r2")
    write_manifest_json(repo, {"sf-1": record("sf-1", "x")}, "2026-05-03T00:00:00Z")
    sha = commit_all(repo, "r3")

    walk = walk_manifests(repo, sha)

    assert walk.last_seen["sf-1"].missing_since is None
    assert gone not in {seen.commit for seen in walk.last_seen.values()}


def test_an_existing_registry_is_kept_and_a_disagreeing_first_holder_is_a_conflict(
    tmp_path: Path,
) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(repo, {"sf-1": record("sf-1", "x")}, "2026-05-01T00:00:00Z")
    sha = commit_all(repo, "seed")
    existing = SlugRegistry().assign("forskrifter", "x", SlugOwner(owner="sf-9", assigned_at="t"))

    registry, conflicts = build_registry(walk_manifests(repo, sha), existing, -1)

    assert registry.owner_of("forskrifter", "x") == "sf-9"
    assert [(c.slug, c.registered_owner, c.planned_owner) for c in conflicts] == [
        ("x", "sf-9", "sf-1")
    ]


def test_a_registry_conflict_at_the_base_blocks_the_plan(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(repo, {"sf-1": record("sf-1", "x")}, "2026-05-01T00:00:00Z")
    existing = SlugRegistry().assign("forskrifter", "x", SlugOwner(owner="sf-9", assigned_at="t"))
    write_doc(repo, "slugs.json", registry_json(existing))
    write_doc(repo, "identity.json", declaration_json())
    commit_all(repo, "seed")

    plan = plan_migration(repo, "HEAD")

    assert plan.blocked
    assert plan.base.registry == existing


@pytest.mark.parametrize("text", ["{", json.dumps({"slugs": {"lover": {"x": {"owner": 1}}}})])
def test_an_unreadable_registry_at_the_base_is_refused(tmp_path: Path, text: str) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(repo, {"sf-1": record("sf-1", "x")}, "2026-05-01T00:00:00Z")
    write_doc(repo, "slugs.json", text)
    commit_all(repo, "seed")

    with pytest.raises(IdentityMigrationError, match="slugs.json"):
        plan_migration(repo, "HEAD")


def test_an_invalid_base_manifest_is_refused(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(
        repo, {"sf-1": {"markdown_path": "forskrifter/x.md"}}, "2026-05-01T00:00:00Z"
    )
    commit_all(repo, "seed")

    with pytest.raises(IdentityMigrationError, match="invalid manifest.json"):
        plan_migration(repo, "HEAD")


def test_a_span_runs_from_first_to_last_revision_holding_the_address(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(repo, {"sf-1": record("sf-1", "x")}, "2026-05-01T00:00:00Z")
    first = commit_all(repo, "r1")
    write_manifest_json(repo, {"sf-1": record("sf-1", "x")}, "2026-05-02T00:00:00Z")
    last = commit_all(repo, "r2")
    write_manifest_json(repo, {"sf-2": record("sf-2", "x")}, "2026-05-03T00:00:00Z")
    sha = commit_all(repo, "r3")

    walk = walk_manifests(repo, sha)

    spans = walk.holders["forskrifter"]["x"]
    assert [(s.doc_id, s.first_commit, s.last_commit) for s in spans] == [
        ("sf-1", first, last),
        ("sf-2", sha, sha),
    ]
    assert spans[1].assigned_at == "2026-05-03T00:00:00+00:00"
    assert threshold_owner(spans, walk, walk.position(last)).doc_id == "sf-1"
    assert threshold_owner(spans, walk, walk.position(sha)).doc_id == "sf-2"
