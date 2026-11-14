"""The registry keeps every historical owner unless a reviewed list names the change (#911).

The content checks hold the migration commit's registry to the manifest and
slug histories it ships with, so a migration that re-owns a published address
and rewrites that address's slug history to match passes them. These cases
hold the registry to the owners the migration's base published (a current
record: 200; a slug history: 301 or 410), and to the first holder of every
``threshold-exception`` entry, through the sync-start gate and ``lovspor audit``.
"""

import json
from pathlib import Path

import pytest

from lovspor.errors import IdentitySchemaError
from lovspor.identity.apply import apply_plan
from lovspor.identity.owner_continuity import (
    load_reassignments,
    reassigned,
    unlisted_threshold_exceptions,
)
from lovspor.identity.plan import plan_migration
from lovspor.identity.registry import EnforcedCorpus, SlugOwner, SlugRegistry, read_registry
from lovspor.identity.registry_content import manifest_checks, misowned_histories
from lovspor.identity.registry_history import verified_corpus_identity
from lovspor.storage.manifest import read_manifest
from lovspor.sync.orchestrator import _ensure_clean_corpus
from tests.unit.identity_git_fixtures import commit_all
from tests.unit.identity_owner_fixtures import FIXTURE_U1, allow_fixture_u1, list_reassignments
from tests.unit.test_identity_migration import KEEP, LOST, NEW, OLD, _base_repo
from tests.unit.test_identity_registry_content import _audit_finding, _registry, _write_registry


def _migrated(tmp_path: Path, threshold: str | None = None, commit: bool = True) -> Path:
    repo, shas = _base_repo(tmp_path)
    apply_plan(repo, plan_migration(repo, "HEAD", shas.get(threshold or "", threshold)))
    if commit:
        commit_all(repo, "migration: repair document identity (#871)")
    return repo


def _refused_everywhere(repo: Path, match: str) -> None:
    with pytest.raises(IdentitySchemaError, match=match):
        verified_corpus_identity(repo)
    with pytest.raises(IdentitySchemaError, match=match):
        _ensure_clean_corpus(repo)
    assert "historical owner" in _audit_finding(repo)


def test_an_unlisted_u1_reassignment_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    list_reassignments(monkeypatch, tmp_path, [])
    repo = _migrated(tmp_path)

    _refused_everywhere(repo, f"forskrifter/x: {NEW} at the base, {OLD} in the registry")


def test_a_listed_reassignment_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    allow_fixture_u1(monkeypatch, tmp_path)
    repo = _migrated(tmp_path)

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    _ensure_clean_corpus(repo)
    assert _audit_finding(repo) == ""


def test_a_listed_reassignment_to_another_document_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    list_reassignments(monkeypatch, tmp_path, [{**FIXTURE_U1, "to_doc_id": KEEP}])
    repo = _migrated(tmp_path)

    _refused_everywhere(repo, "forskrifter/x")


def test_an_uncommitted_migration_is_held_to_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    list_reassignments(monkeypatch, tmp_path, [])
    repo = _migrated(tmp_path, commit=False)

    with pytest.raises(IdentitySchemaError, match="forskrifter/x"):
        verified_corpus_identity(repo)


def _reown_removed_address(repo: Path) -> None:
    """Give the 410 address ``d`` (LOST's) to KEEP, its slug history rewritten to match."""
    data = _registry(repo)
    data["slugs"]["forskrifter"]["d"]["owner"] = KEEP
    _write_registry(repo, data)
    history = repo / "forskrifter/history/d.json"
    payload = json.loads(history.read_text(encoding="utf-8"))
    history.write_text(json.dumps({**payload, "doc_id": KEEP}) + "\n", encoding="utf-8")


def test_a_coordinated_historical_owner_forgery_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forgery the content checks cannot see: registry and slug history agree."""
    allow_fixture_u1(monkeypatch, tmp_path)
    repo, _ = _base_repo(tmp_path)
    apply_plan(repo, plan_migration(repo, "HEAD"))
    _reown_removed_address(repo)
    commit_all(repo, "migration: repair document identity (#871)")
    registry = read_registry(repo)
    assert registry is not None
    manifest = read_manifest(repo / "manifest.json")
    assert all(not found for _, found in manifest_checks(registry, manifest))
    assert misowned_histories(repo, registry) == []

    _refused_everywhere(repo, f"forskrifter/d: {LOST} at the base, {KEEP} in the registry")


def test_an_unlisted_threshold_exception_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A threshold after x's reuse keeps NEW over x's first holder OLD."""
    list_reassignments(monkeypatch, tmp_path, [])
    repo = _migrated(tmp_path, threshold="r4")
    registry = read_registry(repo)
    assert registry is not None
    assert registry.slugs["forskrifter"]["x"].basis == "threshold-exception"

    _refused_everywhere(repo, f"forskrifter/x: {NEW} kept over first holder {OLD}")


def test_a_listed_threshold_exception_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exception = {"directory": "forskrifter", "slug": "x", "first_holder": OLD, "owner": NEW}
    list_reassignments(monkeypatch, tmp_path, [], [{**exception, "reason": "fixture"}])
    repo = _migrated(tmp_path, threshold="r4")

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    assert _audit_finding(repo) == ""


def test_the_reviewed_list_names_the_two_899_repairs() -> None:
    reviewed = load_reassignments()

    assert reviewed.changes[("forskrifter", "endr-i-bilforskriften")] == (
        "sf-20260706-1512",
        "sf-20260513-0853",
    )
    assert reviewed.changes[("forskrifter", "endr-i-tollavgiftsforskriften")] == (
        "sf-20260821-1659",
        "sf-20251219-2804",
    )
    assert len(reviewed.changes) == 13
    assert len(reviewed.threshold) == 56
    assert reviewed.threshold[("forskrifter", "endr-i-bilforskriften")] == (
        "sf-20260324-0472",
        "sf-20260513-0853",
    )


def test_the_899_repairs_pass_only_as_listed() -> None:
    reviewed = load_reassignments()
    owners = {("forskrifter", "endr-i-bilforskriften"): "sf-20260706-1512"}

    def registry(owner: str) -> SlugRegistry:
        entry = SlugOwner(owner=owner, assigned_at="2026-05-22")
        return SlugRegistry().assign("forskrifter", "endr-i-bilforskriften", entry)

    assert reassigned(owners, registry("sf-20260513-0853"), reviewed.changes) == []
    assert reassigned(owners, registry("sf-20260324-0472"), reviewed.changes) != []
    assert reassigned(owners, registry("sf-20260513-0853"), {}) != []


def test_every_reviewed_entry_is_a_change() -> None:
    reviewed = load_reassignments()

    assert all(old != new for old, new in reviewed.changes.values())
    assert all(old != new for old, new in reviewed.threshold.values())


def test_a_first_holder_registry_needs_no_threshold_listing() -> None:
    entry = SlugOwner(owner=OLD, assigned_at="2026")
    registry = SlugRegistry().assign("forskrifter", "x", entry)

    assert unlisted_threshold_exceptions(registry, {}) == []


@pytest.mark.parametrize(
    "content",
    [
        "{",
        '{"reassignments": [], "threshold_exceptions": []}',
        '{"schema_version": 2, "reassignments": [], "threshold_exceptions": []}',
    ],
    ids=["malformed", "no-version", "other-version"],
)
def test_an_unreadable_list_fails_closed(tmp_path: Path, content: str) -> None:
    path = tmp_path / "list.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(IdentitySchemaError, match="unreadable owner reassignment list"):
        load_reassignments(path)


def test_an_address_listed_twice_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    data = {"schema_version": 1, "reassignments": [FIXTURE_U1, FIXTURE_U1]}
    path.write_text(json.dumps({**data, "threshold_exceptions": []}), encoding="utf-8")

    with pytest.raises(IdentitySchemaError, match="listed twice"):
        load_reassignments(path)
