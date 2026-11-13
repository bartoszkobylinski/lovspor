"""A committed registry never loses or rewrites an entry its history held (#871, N1).

The coverage check sees only addresses of the current manifest, and the
writer's append-only guard compares against the file on disk, which is the
edited one. What is under test is how the registry's own git history is read,
so every case is a real temporary git repository.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from lovspor.errors import CorpusStateError, IdentityMigrationError, IdentitySchemaError
from lovspor.identity.history_marker import _evidence_at
from lovspor.identity.plan import plan_migration
from lovspor.identity.registry import (
    REGISTRY_FILENAME,
    EnforcedCorpus,
    LegacyCorpus,
    PriorHolder,
    SlugOwner,
    SlugRegistry,
    corpus_identity,
    registry_json,
    update_slug_registry,
)
from lovspor.identity.registry_history import first_entries, verified_corpus_identity
from lovspor.identity.schema import IDENTITY_FILENAME, declaration_json
from lovspor.storage.manifest import read_manifest
from lovspor.sync.orchestrator import _ensure_clean_corpus
from tests.unit.identity_git_fixtures import commit_all, git, init_repo, record, write_manifest_json
from tests.unit.test_identity_downgrade import DOCS, FIRST, SECOND, _audit, _seeded

AT = "2026-06-01T00:00:00+00:00"
#: A removed document's old address: in the registry, not in the current manifest.
OLD = "sf-20190101-0009"
THIRD = "sf-20250101-0003"


def _owner(doc_id: str) -> SlugOwner:
    return SlugOwner(owner=doc_id, assigned_at=AT)


def _registry() -> SlugRegistry:
    registry = SlugRegistry()
    for doc_id, raw in sorted(DOCS.items()):
        registry = registry.assign("forskrifter", raw["slug"], _owner(doc_id))
    return registry.assign("forskrifter", "old-a", _owner(OLD))


def _write(repo: Path, registry: SlugRegistry) -> None:
    (repo / REGISTRY_FILENAME).write_text(registry_json(registry), encoding="utf-8")


def _set(registry: SlugRegistry, slug: str, entry: SlugOwner | None) -> SlugRegistry:
    """``registry`` with one entry replaced or dropped, as a hand edit would leave it."""
    entries = dict(registry.slugs["forskrifter"])
    if entry is None:
        del entries[slug]
    else:
        entries[slug] = entry
    return registry.model_copy(update={"slugs": {**registry.slugs, "forskrifter": entries}})


@pytest.fixture
def migrated(tmp_path: Path) -> Path:
    repo = _seeded(tmp_path)
    _write(repo, _registry())
    (repo / IDENTITY_FILENAME).write_text(declaration_json(), encoding="utf-8")
    commit_all(repo, "migration: declare identity schema 1")
    return repo


def _plan(repo: Path) -> object:
    return plan_migration(repo, "HEAD")


def _audit_refuses(repo: Path) -> None:
    code, output = _audit(repo)
    assert code == 1
    assert "identity_schema" in output


#: The three entry points, each with the error it raises.
ENTRY: dict[str, tuple[Callable[[Path], object], type[Exception]]] = {
    "verified_corpus_identity": (verified_corpus_identity, IdentitySchemaError),
    "sync_start": (_ensure_clean_corpus, IdentitySchemaError),
    "plan_migration": (_plan, IdentityMigrationError),
}

THRESHOLD = SlugOwner(
    owner=OLD,
    assigned_at=AT,
    basis="threshold-exception",
    prior_holders=(PriorHolder(doc_id=THIRD, first_commit="a" * 40, last_commit="b" * 40),),
)

#: Hand edits the coverage check cannot see.
EDITS: dict[str, tuple[str, SlugOwner | None]] = {
    "drop a non-manifest entry": ("old-a", None),
    "re-own a non-manifest entry": ("old-a", _owner(THIRD)),
    "re-own a manifest address": ("a", _owner(SECOND)),
    "change assigned_at": ("a", SlugOwner(owner=FIRST, assigned_at="2020-01-01T00:00:00+00:00")),
    "change basis and prior_holders": ("old-a", THRESHOLD),
}


@pytest.mark.parametrize("edit", sorted(EDITS))
@pytest.mark.parametrize("point", sorted(ENTRY))
def test_a_committed_edit_of_a_registry_entry_is_refused(
    migrated: Path, point: str, edit: str
) -> None:
    slug, entry = EDITS[edit]
    _write(migrated, _set(_registry(), slug, entry))
    commit_all(migrated, f"hand edit: {edit}")
    call, error = ENTRY[point]

    with pytest.raises(error, match=f"forskrifter/{slug}"):
        call(migrated)


@pytest.mark.parametrize("edit", sorted(EDITS))
def test_the_audit_reports_a_committed_edit(migrated: Path, edit: str) -> None:
    slug, entry = EDITS[edit]
    _write(migrated, _set(_registry(), slug, entry))
    commit_all(migrated, f"hand edit: {edit}")

    _audit_refuses(migrated)


def test_the_refusal_names_the_entry_its_owner_and_the_commit_that_held_it(
    migrated: Path,
) -> None:
    migration = git(migrated, "rev-parse", "HEAD")
    _write(migrated, _set(_registry(), "old-a", None))
    commit_all(migrated, "drop old-a")

    with pytest.raises(IdentitySchemaError) as caught:
        verified_corpus_identity(migrated)

    message = str(caught.value)
    assert f"forskrifter/old-a: drops the entry of {OLD} from {migration}" in message
    assert f"show <commit>:{REGISTRY_FILENAME}" in message


def test_edits_spread_over_several_commits_are_all_counted(migrated: Path) -> None:
    """Two hand commits between syncs cannot launder a drop: every revision is read."""
    _write(migrated, _set(_registry(), "a", _owner(SECOND)))
    commit_all(migrated, "re-own a")
    reowned = _set(_registry(), "a", _owner(SECOND))
    _write(migrated, _set(reowned, "old-a", None))
    commit_all(migrated, "drop old-a")
    write_manifest_json(migrated, DOCS, "2026-06-03T00:00:00Z")
    commit_all(migrated, "an unrelated manifest commit")

    with pytest.raises(IdentitySchemaError, match=r"\(2 entr"):
        verified_corpus_identity(migrated)
    _audit_refuses(migrated)


def test_an_entry_appended_after_the_migration_is_held_too(migrated: Path) -> None:
    docs = {**DOCS, THIRD: record(THIRD, "c")}
    write_manifest_json(migrated, docs, "2026-06-02T00:00:00Z")
    update_slug_registry(migrated, read_manifest(migrated / "manifest.json"))
    commit_all(migrated, "sync: add c")
    write_manifest_json(migrated, DOCS, "2026-06-03T00:00:00Z")
    _write(migrated, _registry())
    commit_all(migrated, "hand edit: drop c from the manifest and the registry")

    with pytest.raises(IdentitySchemaError, match=f"forskrifter/c: drops the entry of {THIRD}"):
        _ensure_clean_corpus(migrated)


def test_appends_by_the_sync_pass_every_entry_point(migrated: Path) -> None:
    for day, doc_id, slug in [("02", THIRD, "c"), ("03", "sf-20250101-0004", "d")]:
        docs = {**read_manifest(migrated / "manifest.json").model_dump(mode="json")["documents"]}
        docs[doc_id] = record(doc_id, slug)
        write_manifest_json(migrated, docs, f"2026-06-{day}T00:00:00Z")
        update_slug_registry(migrated, read_manifest(migrated / "manifest.json"))
        commit_all(migrated, f"sync: add {slug}")

    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)
    _ensure_clean_corpus(migrated)
    assert plan_migration(migrated, "HEAD").nothing_to_do
    assert "identity_schema" not in _audit(migrated)[1]


def test_a_formatting_only_rewrite_is_tolerated(migrated: Path) -> None:
    """Ownership is the content; the next sync writes the canonical form again."""
    data = json.loads((migrated / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    for entry in data["slugs"]["forskrifter"].values():
        entry["prior_holders"] = []
    (migrated / REGISTRY_FILENAME).write_text(json.dumps(data, indent=4), encoding="utf-8")
    commit_all(migrated, "reformat the registry")

    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)
    assert "identity_schema" not in _audit(migrated)[1]


def test_restoring_the_entry_recovers(migrated: Path) -> None:
    _write(migrated, _set(_registry(), "old-a", _owner(THIRD)))
    commit_all(migrated, "re-own old-a")
    _write(migrated, _registry())
    commit_all(migrated, "restore old-a")

    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)
    _ensure_clean_corpus(migrated)


def test_an_unparseable_revision_later_repaired_is_tolerated(migrated: Path) -> None:
    (migrated / REGISTRY_FILENAME).write_text("{bad", encoding="utf-8")
    commit_all(migrated, "corrupt the registry")
    _write(migrated, _registry())
    commit_all(migrated, "repair the registry")

    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)


def test_an_uncommitted_edit_is_refused(migrated: Path) -> None:
    _write(migrated, _set(_registry(), "old-a", None))

    with pytest.raises(CorpusStateError, match="uncommitted"):
        _ensure_clean_corpus(migrated)
    with pytest.raises(IdentitySchemaError, match="forskrifter/old-a"):
        verified_corpus_identity(migrated)
    _audit_refuses(migrated)


def test_a_declared_corpus_in_a_subdirectory_cannot_be_verified(migrated: Path) -> None:
    inner = migrated / "nested"
    inner.mkdir()
    for name in (IDENTITY_FILENAME, REGISTRY_FILENAME, "manifest.json"):
        (inner / name).write_bytes((migrated / name).read_bytes())

    with pytest.raises(IdentitySchemaError, match="not the root"):
        verified_corpus_identity(inner)


def test_a_corpus_migrated_but_not_yet_committed_has_no_history_to_hold(tmp_path: Path) -> None:
    repo = _seeded(tmp_path)
    _write(repo, _registry())
    (repo / IDENTITY_FILENAME).write_text(declaration_json(), encoding="utf-8")

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)


# --- memoisation ---


def test_the_history_is_read_once_per_commit(migrated: Path) -> None:
    head = git(migrated, "rev-parse", "HEAD")
    first_entries.cache_clear()

    verified_corpus_identity(migrated)
    verified_corpus_identity(migrated)

    info = first_entries.cache_info()
    assert (info.misses, info.hits) == (1, 1)
    assert set(first_entries(migrated.resolve(), head)["forskrifter"]) == {"a", "b", "old-a"}


def test_a_memoised_verdict_does_not_outlive_its_commit(migrated: Path) -> None:
    verified_corpus_identity(migrated)
    _write(migrated, _set(_registry(), "old-a", None))
    commit_all(migrated, "drop old-a")

    with pytest.raises(IdentitySchemaError, match="forskrifter/old-a"):
        verified_corpus_identity(migrated)


def test_a_memoised_legacy_verdict_does_not_outlive_its_commit(tmp_path: Path) -> None:
    repo = _seeded(tmp_path)
    _evidence_at.cache_clear()
    assert isinstance(corpus_identity(repo), LegacyCorpus)
    assert isinstance(corpus_identity(repo), LegacyCorpus)
    assert _evidence_at.cache_info().hits == 1

    _write(repo, _registry())
    (repo / IDENTITY_FILENAME).write_text(declaration_json(), encoding="utf-8")
    commit_all(repo, "migration")
    (repo / IDENTITY_FILENAME).unlink()
    (repo / REGISTRY_FILENAME).unlink()
    commit_all(repo, "delete the identity state")

    with pytest.raises(IdentitySchemaError, match="never returns to legacy"):
        corpus_identity(repo)


def test_an_empty_repository_is_never_memoised(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    _evidence_at.cache_clear()

    assert isinstance(corpus_identity(repo), LegacyCorpus)
    assert _evidence_at.cache_info().currsize == 0


def test_a_merge_cannot_hide_an_entry_committed_on_its_other_parent(migrated: Path) -> None:
    """The full-history contract includes entries discarded by an ours merge."""
    git(migrated, "checkout", "-q", "-b", "side-sync")
    _write(migrated, _registry().assign("forskrifter", "side-only", _owner(THIRD)))
    side_commit = commit_all(migrated, "publish an address on the side branch")
    git(migrated, "checkout", "-q", "main")
    git(migrated, "merge", "-q", "-s", "ours", "--no-edit", "side-sync")

    with pytest.raises(IdentitySchemaError, match=f"side-only: drops.*{side_commit}"):
        verified_corpus_identity(migrated)
    with pytest.raises(IdentityMigrationError, match="side-only: drops"):
        plan_migration(migrated, "HEAD")


def test_entries_after_the_first_revision_batch_are_still_protected(migrated: Path) -> None:
    """More than sixteen revisions must preserve the entry-to-commit alignment."""
    registry = _registry()
    for index in range(17):
        registry = registry.assign("forskrifter", f"extra-{index}", _owner(THIRD))
        _write(migrated, registry)
        added = commit_all(migrated, f"append address {index}")
    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)
    _write(migrated, _set(registry, "extra-16", None))
    commit_all(migrated, "drop the last address")

    with pytest.raises(IdentitySchemaError, match=f"extra-16: drops.*{added}"):
        verified_corpus_identity(migrated)


@pytest.mark.parametrize("field", ["doc_id", "first_commit", "last_commit"])
def test_each_prior_holder_field_is_immutable(migrated: Path, field: str) -> None:
    """Prior holders are part of the immutable entry (docs/data-model.md)."""
    registry = _registry().assign("forskrifter", "exception", THRESHOLD)
    _write(migrated, registry)
    original = commit_all(migrated, "append a threshold exception")
    replacement = FIRST if field == "doc_id" else "c" * 40
    holder = THRESHOLD.prior_holders[0].model_copy(update={field: replacement})
    changed = THRESHOLD.model_copy(update={"prior_holders": (holder,)})
    _write(migrated, _set(registry, "exception", changed))
    commit_all(migrated, f"rewrite prior holder {field}")

    with pytest.raises(IdentitySchemaError, match=f"exception: rewrites.*{original}"):
        verified_corpus_identity(migrated)
