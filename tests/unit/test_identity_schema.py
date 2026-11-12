"""The versioned, fail-closed identity schema of a corpus (#871, ``identity.json``).

The registry guards run because the corpus declares the enforced schema, never
because ``slugs.json`` happens to exist: a declared corpus without a readable
registry fails closed at every point that enforces ownership, an undeclared
one runs in the named legacy mode, and an unknown declaration is refused.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lovspor.errors import CorpusStateError, IdentitySchemaError
from lovspor.history import HistoryEvent, Lineage, LineageStep
from lovspor.identity.history import write_document_history
from lovspor.identity.registry import (
    REGISTRY_FILENAME,
    EnforcedCorpus,
    LegacyCorpus,
    SlugOwner,
    SlugRegistry,
    corpus_identity,
    registered_ownership,
    registry_json,
    update_slug_registry,
)
from lovspor.identity.schema import (
    ENFORCED_IDENTITY_SCHEMA,
    IDENTITY_FILENAME,
    LEGACY_IDENTITY_SCHEMA,
    declaration_json,
    declared_schema,
    parse_declaration,
    write_declaration,
)
from lovspor.storage.manifest import Manifest, ManifestRecord
from tests.unit.identity_git_fixtures import init_repo

AT = datetime(2026, 11, 1, tzinfo=UTC)
SLUG = "endr-i-tollavgift"
OWNER = SlugOwner(owner="sf-20250101-0001", assigned_at="2026-05-01T00:00:00+00:00")
OWNERS = {"gjeldende-sentrale-forskrifter": {SLUG: "sf-20261002-1999"}}


def _manifest() -> Manifest:
    record = ManifestRecord(
        doc_type="forskrift",
        xml_hash="a" * 64,
        markdown_path="forskrifter/ny.md",
        source_dataset="gjeldende-sentrale-forskrifter",
        last_seen=AT,
        status="current",
        slug="ny",
    )
    return Manifest(generated_at=AT, documents={"sf-1": record})


def _write_history(repo: Path) -> None:
    event = HistoryEvent(date=date(2026, 9, 17), commit="02c0a52", type="added", subject="add")
    lineage = Lineage(
        doc_id="sf-1", steps=[LineageStep(event=event, path=f"forskrifter/{SLUG}.md")]
    )
    write_document_history(repo, lineage, lineage.record(SLUG), repo / "forskrifter")


#: Every point that enforces slug ownership, as one call on a corpus root.
ENFORCING_POINTS: dict[str, Callable[[Path], object]] = {
    "corpus_identity": corpus_identity,
    "registered_ownership": lambda repo: registered_ownership(repo, OWNERS),
    "update_slug_registry": lambda repo: update_slug_registry(repo, _manifest()),
    "write_document_history": _write_history,
}


def _declare(repo: Path, text: str | None = None) -> None:
    (repo / IDENTITY_FILENAME).write_text(text or declaration_json(), encoding="utf-8")


def _registry_file(repo: Path, text: str | None = None) -> None:
    registry = SlugRegistry().assign("forskrifter", SLUG, OWNER)
    (repo / REGISTRY_FILENAME).write_text(text or registry_json(registry), encoding="utf-8")


def _snapshot(repo: Path) -> dict[str, bytes]:
    return {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


# --- the declaration itself ---


def test_no_declaration_is_the_legacy_schema(tmp_path: Path) -> None:
    assert declared_schema(tmp_path) == LEGACY_IDENTITY_SCHEMA == 0


def test_the_written_declaration_is_the_enforced_schema(tmp_path: Path) -> None:
    path = write_declaration(tmp_path)

    assert path == tmp_path / IDENTITY_FILENAME
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": 1}
    assert path.read_text(encoding="utf-8") == declaration_json()
    assert declared_schema(tmp_path) == ENFORCED_IDENTITY_SCHEMA == 1


@pytest.mark.parametrize(
    "blob",
    [
        b"{",
        b"\xff",
        b"[]",
        b"{}",
        b'{"schema_version": "1"}',
        b'{"schema_version": true}',
        b'{"schema_version": 1, "extra": 1}',
    ],
)
def test_an_unreadable_declaration_is_refused(blob: bytes) -> None:
    with pytest.raises(IdentitySchemaError, match="unreadable identity declaration"):
        parse_declaration(blob, IDENTITY_FILENAME)


@pytest.mark.parametrize("version", [0, 2, 99, -1])
def test_a_declared_version_other_than_the_enforced_one_is_refused(version: int) -> None:
    blob = json.dumps({"schema_version": version}).encode()

    with pytest.raises(IdentitySchemaError, match=f"unknown identity schema version {version}"):
        parse_declaration(blob, IDENTITY_FILENAME)


def test_the_error_is_a_corpus_state_error() -> None:
    assert issubclass(IdentitySchemaError, CorpusStateError)


# --- every enforcing point fails closed ---


@pytest.mark.parametrize("point", sorted(ENFORCING_POINTS))
def test_a_declared_corpus_without_a_registry_fails_closed(tmp_path: Path, point: str) -> None:
    _declare(tmp_path)
    before = _snapshot(tmp_path)

    with pytest.raises(IdentitySchemaError, match=f"no {REGISTRY_FILENAME}"):
        ENFORCING_POINTS[point](tmp_path)

    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        '{"slugs": {"lover": {"x": {"owner": 1}}}}',
        '{"schema_version": 2, "slugs": {}}',
    ],
)
@pytest.mark.parametrize("point", sorted(ENFORCING_POINTS))
def test_a_declared_corpus_with_an_unreadable_registry_fails_closed(
    tmp_path: Path, point: str, text: str
) -> None:
    _declare(tmp_path)
    _registry_file(tmp_path, text)
    before = _snapshot(tmp_path)

    with pytest.raises(IdentitySchemaError, match=REGISTRY_FILENAME):
        ENFORCING_POINTS[point](tmp_path)

    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("version", [0, 2])
@pytest.mark.parametrize("point", sorted(ENFORCING_POINTS))
def test_an_unknown_declared_version_fails_closed(tmp_path: Path, point: str, version: int) -> None:
    _declare(tmp_path, json.dumps({"schema_version": version}))
    _registry_file(tmp_path)
    before = _snapshot(tmp_path)

    with pytest.raises(IdentitySchemaError, match="unknown identity schema version"):
        ENFORCING_POINTS[point](tmp_path)

    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("point", sorted(ENFORCING_POINTS))
def test_a_registry_no_declaration_covers_fails_closed(tmp_path: Path, point: str) -> None:
    """Deleting ``identity.json`` never turns a migrated corpus back into legacy."""
    _registry_file(tmp_path)
    before = _snapshot(tmp_path)

    with pytest.raises(IdentitySchemaError, match=f"no {IDENTITY_FILENAME}"):
        ENFORCING_POINTS[point](tmp_path)

    assert _snapshot(tmp_path) == before


# --- the two legal states ---


def test_a_declared_corpus_with_a_registry_is_enforced(tmp_path: Path) -> None:
    _declare(tmp_path)
    _registry_file(tmp_path)

    identity = corpus_identity(tmp_path)

    assert isinstance(identity, EnforcedCorpus)
    assert identity.schema_version == ENFORCED_IDENTITY_SCHEMA
    assert identity.registry.owner_of("forskrifter", SLUG) == OWNER.owner
    merged = registered_ownership(tmp_path, OWNERS)
    assert merged["gjeldende-sentrale-forskrifter"][SLUG] == OWNER.owner


def test_an_undeclared_corpus_is_the_named_legacy_mode(tmp_path: Path) -> None:
    init_repo(tmp_path)
    identity = corpus_identity(tmp_path)

    assert isinstance(identity, LegacyCorpus)
    assert identity.schema_version == LEGACY_IDENTITY_SCHEMA


def test_legacy_mode_syncs_on_manifest_ownership_and_says_so_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    init_repo(tmp_path)
    caplog.set_level(logging.INFO, logger="lovspor.identity.registry")

    assert registered_ownership(tmp_path, OWNERS) == OWNERS
    assert update_slug_registry(tmp_path, _manifest()) == []
    _write_history(tmp_path)

    legacy = [r for r in caplog.records if "legacy" in r.getMessage()]
    assert len(legacy) == 1
    assert "identity schema 0" in legacy[0].getMessage()
    assert IDENTITY_FILENAME in legacy[0].getMessage()


def test_the_sync_writers_never_create_the_declaration_or_the_registry(tmp_path: Path) -> None:
    init_repo(tmp_path)
    update_slug_registry(tmp_path, _manifest())
    registered_ownership(tmp_path, OWNERS)
    _write_history(tmp_path)

    assert not (tmp_path / IDENTITY_FILENAME).exists()
    assert not (tmp_path / REGISTRY_FILENAME).exists()


def test_extending_an_enforced_registry_leaves_the_declaration_alone(tmp_path: Path) -> None:
    _declare(tmp_path)
    _registry_file(tmp_path)
    declaration = (tmp_path / IDENTITY_FILENAME).read_bytes()

    paths = update_slug_registry(tmp_path, _manifest())

    assert paths == [tmp_path / REGISTRY_FILENAME]
    assert (tmp_path / IDENTITY_FILENAME).read_bytes() == declaration
