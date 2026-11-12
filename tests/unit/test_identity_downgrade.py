"""A migrated corpus never silently returns to the legacy identity schema (#871, N1).

``identity.json`` and ``slugs.json`` live in the corpus, so one commit can
delete or truncate both. The evidence that the corpus was migrated is its git
history: a commit that ever touched ``identity.json``. Every case is a real
temporary git repository, because what is under test is how that history is
read.
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.errors import IdentityMigrationError, IdentitySchemaError
from lovspor.history import HistoryEvent, Lineage, LineageStep
from lovspor.identity.apply import apply_plan
from lovspor.identity.history import write_document_history
from lovspor.identity.plan import plan_migration
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
    verified_corpus_identity,
)
from lovspor.identity.schema import IDENTITY_FILENAME, declaration_json
from lovspor.storage.manifest import Manifest
from lovspor.sync.orchestrator import _ensure_clean_corpus
from tests.unit.identity_git_fixtures import (
    commit_all,
    git,
    init_repo,
    record,
    write_manifest_json,
)
from tests.unit.test_identity_migration import _base_repo

AT = datetime(2026, 11, 1, tzinfo=UTC)
FIRST = "sf-20250101-0001"
SECOND = "sf-20250101-0002"
DOCS = {FIRST: record(FIRST, "a"), SECOND: record(SECOND, "b")}


def _registry_text(docs: dict[str, dict]) -> str:
    registry = SlugRegistry()
    for doc_id, raw in sorted(docs.items()):
        owner = SlugOwner(owner=doc_id, assigned_at="2026-06-01T00:00:00+00:00")
        registry = registry.assign("forskrifter", raw["slug"], owner)
    return registry_json(registry)


def _seeded(tmp_path: Path) -> Path:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(repo, DOCS, "2026-06-01T00:00:00Z")
    commit_all(repo, "sync: seed")
    return repo


def _migrate(repo: Path) -> str:
    (repo / REGISTRY_FILENAME).write_text(_registry_text(DOCS), encoding="utf-8")
    (repo / IDENTITY_FILENAME).write_text(declaration_json(), encoding="utf-8")
    return commit_all(repo, "migration: declare identity schema 1")


@pytest.fixture
def migrated(tmp_path: Path) -> tuple[Path, str]:
    repo = _seeded(tmp_path)
    return repo, _migrate(repo)


def _write_history(repo: Path) -> None:
    event = HistoryEvent(date=date(2026, 9, 17), commit="02c0a52", type="added", subject="add")
    lineage = Lineage(doc_id=FIRST, steps=[LineageStep(event=event, path="forskrifter/a.md")])
    write_document_history(repo, lineage, lineage.record("a"), repo / "forskrifter")


#: Every path that writes or validates the corpus's identity state, as one call.
POINTS: dict[str, tuple[Callable[[Path], object], type[Exception]]] = {
    "corpus_identity": (corpus_identity, IdentitySchemaError),
    "verified_corpus_identity": (verified_corpus_identity, IdentitySchemaError),
    "sync_start": (_ensure_clean_corpus, IdentitySchemaError),
    "registered_ownership": (lambda repo: registered_ownership(repo, {}), IdentitySchemaError),
    "update_slug_registry": (
        lambda repo: update_slug_registry(repo, Manifest(generated_at=AT, documents={})),
        IdentitySchemaError,
    ),
    "write_document_history": (_write_history, IdentitySchemaError),
    "plan_migration": (lambda repo: plan_migration(repo, "HEAD"), IdentityMigrationError),
}


def _audit(repo: Path) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["audit", "--corpus-path", str(repo)])
    return result.exit_code, result.output


# --- deletion ---


@pytest.mark.parametrize(
    "deleted",
    [(IDENTITY_FILENAME,), (REGISTRY_FILENAME,), (IDENTITY_FILENAME, REGISTRY_FILENAME)],
)
@pytest.mark.parametrize("point", sorted(POINTS))
def test_a_committed_deletion_fails_closed_everywhere(
    migrated: tuple[Path, str], point: str, deleted: tuple[str, ...]
) -> None:
    repo, _ = migrated
    for name in deleted:
        (repo / name).unlink()
    commit_all(repo, "delete the identity state")
    call, error = POINTS[point]

    with pytest.raises(error):
        call(repo)

    kept = {IDENTITY_FILENAME, REGISTRY_FILENAME, "manifest.json"} - set(deleted)
    assert {p.name for p in repo.iterdir() if p.is_file()} == kept


def test_deleting_both_files_names_the_migration_and_the_recovery(
    migrated: tuple[Path, str],
) -> None:
    repo, migration = migrated
    (repo / IDENTITY_FILENAME).unlink()
    (repo / REGISTRY_FILENAME).unlink()
    deletion = commit_all(repo, "delete the identity state")

    with pytest.raises(IdentitySchemaError) as caught:
        corpus_identity(repo)

    message = str(caught.value)
    assert deletion in message
    assert f"checkout {migration} -- {IDENTITY_FILENAME} {REGISTRY_FILENAME}" in message
    assert "never returns to legacy" in message


def test_the_named_recovery_restores_enforcement(migrated: tuple[Path, str]) -> None:
    repo, migration = migrated
    (repo / IDENTITY_FILENAME).unlink()
    (repo / REGISTRY_FILENAME).unlink()
    commit_all(repo, "delete the identity state")

    git(repo, "checkout", migration, "--", IDENTITY_FILENAME, REGISTRY_FILENAME)
    commit_all(repo, "restore the identity state")

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    _ensure_clean_corpus(repo)


@pytest.mark.parametrize(
    "deleted",
    [(IDENTITY_FILENAME,), (REGISTRY_FILENAME,), (IDENTITY_FILENAME, REGISTRY_FILENAME)],
)
def test_an_uncommitted_deletion_fails_closed(
    migrated: tuple[Path, str], deleted: tuple[str, ...]
) -> None:
    repo, _ = migrated
    for name in deleted:
        (repo / name).unlink()

    with pytest.raises(IdentitySchemaError):
        corpus_identity(repo)
    with pytest.raises(IdentitySchemaError):
        registered_ownership(repo, {})
    with pytest.raises(IdentitySchemaError):
        _write_history(repo)


# --- corruption and versions ---


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (IDENTITY_FILENAME, "{bad"),
        (REGISTRY_FILENAME, "{bad"),
        (IDENTITY_FILENAME, ""),
        (REGISTRY_FILENAME, ""),
        (IDENTITY_FILENAME, '{"schema_version": 0}'),
        (IDENTITY_FILENAME, '{"schema_version": 2}'),
        (IDENTITY_FILENAME, '{"schema_version": "1"}'),
        (REGISTRY_FILENAME, '{"schema_version": 2, "slugs": {}}'),
    ],
)
@pytest.mark.parametrize("point", sorted(POINTS))
def test_a_committed_corruption_fails_closed_everywhere(
    migrated: tuple[Path, str], point: str, name: str, text: str
) -> None:
    repo, _ = migrated
    (repo / name).write_text(text, encoding="utf-8")
    commit_all(repo, f"corrupt {name}")
    call, error = POINTS[point]

    with pytest.raises(error):
        call(repo)


# --- truncation ---


@pytest.mark.parametrize("point", sorted(POINTS))
def test_an_empty_registry_beside_a_manifest_with_documents_fails_closed(
    migrated: tuple[Path, str], point: str
) -> None:
    repo, _ = migrated
    (repo / REGISTRY_FILENAME).write_text('{"schema_version": 1, "slugs": {}}', encoding="utf-8")
    commit_all(repo, "truncate the registry")
    call, error = POINTS[point]

    with pytest.raises(error, match="slugs registered"):
        call(repo)


def test_an_empty_registry_beside_an_empty_manifest_is_enforced(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_manifest_json(repo, {}, "2026-06-01T00:00:00Z")
    (repo / REGISTRY_FILENAME).write_text(registry_json(SlugRegistry()), encoding="utf-8")
    (repo / IDENTITY_FILENAME).write_text(declaration_json(), encoding="utf-8")
    commit_all(repo, "migration of an empty corpus")

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    _ensure_clean_corpus(repo)
    assert plan_migration(repo, "HEAD").nothing_to_do


@pytest.mark.parametrize("point", ["verified_corpus_identity", "sync_start", "plan_migration"])
def test_a_registry_missing_a_manifest_address_fails_closed_at_entry(
    migrated: tuple[Path, str], point: str
) -> None:
    repo, _ = migrated
    partial = _registry_text({FIRST: DOCS[FIRST]})
    (repo / REGISTRY_FILENAME).write_text(partial, encoding="utf-8")
    commit_all(repo, "drop one registry entry")
    call, error = POINTS[point]

    with pytest.raises(error, match="forskrifter/b"):
        call(repo)


def test_the_audit_reports_a_truncated_registry(migrated: tuple[Path, str]) -> None:
    repo, _ = migrated
    (repo / REGISTRY_FILENAME).write_text(_registry_text({FIRST: DOCS[FIRST]}), encoding="utf-8")
    commit_all(repo, "drop one registry entry")

    code, output = _audit(repo)

    assert code == 1
    assert "identity_schema" in output


# --- rollback ---


def test_reverting_the_migration_commit_is_refused_with_its_recovery(tmp_path: Path) -> None:
    repo, _ = _base_repo(tmp_path)
    apply_plan(repo, plan_migration(repo, "HEAD"))
    migration = commit_all(repo, "migration: repair document identity (#871)")
    git(repo, "revert", "--no-edit", "HEAD")
    assert not (repo / IDENTITY_FILENAME).exists()

    with pytest.raises(IdentitySchemaError, match=f"checkout {migration} -- {IDENTITY_FILENAME}"):
        _ensure_clean_corpus(repo)
    with pytest.raises(IdentityMigrationError, match="never returns to legacy"):
        plan_migration(repo, "HEAD")
    assert _audit(repo)[0] == 1

    git(repo, "revert", "--no-edit", "HEAD")

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    _ensure_clean_corpus(repo)


def test_a_merge_that_drops_the_declaration_is_still_seen(tmp_path: Path) -> None:
    """Default history simplification would follow only the merge's first parent."""
    repo = _seeded(tmp_path)
    git(repo, "checkout", "-q", "-b", "migration")
    _migrate(repo)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "-s", "ours", "--no-edit", "migration")
    assert not (repo / IDENTITY_FILENAME).exists()

    with pytest.raises(IdentitySchemaError, match="never returns to legacy"):
        corpus_identity(repo)


# --- history that cannot be read ---


def test_a_shallow_clone_cannot_prove_legacy(tmp_path: Path) -> None:
    source = _seeded(tmp_path)
    write_manifest_json(source, DOCS, "2026-06-02T00:00:00Z")
    commit_all(source, "sync: second")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{source}", str(clone))

    with pytest.raises(IdentitySchemaError, match="shallow"):
        corpus_identity(clone)
    with pytest.raises(IdentityMigrationError, match="shallow"):
        plan_migration(clone, "HEAD")


def test_a_shallow_clone_of_a_declared_corpus_is_enforced(
    migrated: tuple[Path, str], tmp_path: Path
) -> None:
    repo, _ = migrated
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(clone))

    assert isinstance(verified_corpus_identity(clone), EnforcedCorpus)


def test_a_directory_without_git_history_cannot_prove_legacy(tmp_path: Path) -> None:
    with pytest.raises(IdentitySchemaError, match="cannot read the corpus history"):
        corpus_identity(tmp_path)


def test_a_subdirectory_of_another_repository_cannot_prove_legacy(tmp_path: Path) -> None:
    outer = _seeded(tmp_path)
    inner = outer / "nested"
    inner.mkdir()

    with pytest.raises(IdentitySchemaError, match="not the root"):
        corpus_identity(inner)


# --- the explicit legacy mode ---


def test_a_never_migrated_corpus_syncs_in_the_legacy_mode(tmp_path: Path) -> None:
    repo = _seeded(tmp_path)

    identity = verified_corpus_identity(repo)

    assert isinstance(identity, LegacyCorpus)
    assert identity.schema_version == 0
    _ensure_clean_corpus(repo)
    assert registered_ownership(repo, {"d": {"s": FIRST}}) == {"d": {"s": FIRST}}
    assert "identity_schema" not in _audit(repo)[1]


def test_an_empty_repository_is_the_legacy_mode(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")

    assert isinstance(corpus_identity(repo), LegacyCorpus)


def test_a_migrated_corpus_passes_every_point(migrated: tuple[Path, str]) -> None:
    repo, _ = migrated

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    _ensure_clean_corpus(repo)
    assert plan_migration(repo, "HEAD").nothing_to_do
    assert "identity_schema" not in _audit(repo)[1]
    assert json.loads((repo / IDENTITY_FILENAME).read_text(encoding="utf-8")) == {
        "schema_version": 1
    }
