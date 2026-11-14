"""The identity gate checks what the migration commit's registry says (#911).

The registry-history check takes the migration commit's ``slugs.json`` as its
baseline, so that commit's content has to be checked against the corpus it
ships with: every owner and prior holder a document of the manifest, every
current record on an address it owns, every slug history naming its
address's owner, and no duplicate key in the file. Each case below is the
complete migration of a real temporary repository, with one thing forged,
committed as the migration commit, then read by the sync-start gate and by
``lovspor audit``.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from lovspor.corpus_audit import audit_published_corpus
from lovspor.errors import IdentitySchemaError
from lovspor.identity.apply import apply_plan
from lovspor.identity.plan import MigrationPlan, plan_migration
from lovspor.identity.registry import EnforcedCorpus, registry_json
from lovspor.identity.registry_history import verified_corpus_identity
from lovspor.identity.report import manifest_json
from lovspor.identity.schema import REGISTRY_FILENAME
from lovspor.sync.orchestrator import _ensure_clean_corpus
from tests.unit.identity_git_fixtures import commit_all, git
from tests.unit.test_identity_migration import KEEP, LOST, NEW, OLD, _base_repo

GHOST = "sf-99999999-9999"


def _registry(repo: Path) -> dict:
    return json.loads((repo / REGISTRY_FILENAME).read_text(encoding="utf-8"))


def _write_registry(repo: Path, data: dict) -> None:
    text = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    (repo / REGISTRY_FILENAME).write_text(text, encoding="utf-8")


def _set_owner(repo: Path, slug: str, owner: str) -> None:
    data = _registry(repo)
    data["slugs"]["forskrifter"][slug]["owner"] = owner
    _write_registry(repo, data)


def _swap(repo: Path, _plan: MigrationPlan) -> None:
    data = _registry(repo)
    entries = data["slugs"]["forskrifter"]
    entries["c"]["owner"], entries["x-2026"]["owner"] = NEW, KEEP
    _write_registry(repo, data)


def _phantom(repo: Path, _plan: MigrationPlan) -> None:
    data = _registry(repo)
    data["slugs"]["forskrifter"]["zz-phantom"] = {"assigned_at": "2026-05-01", "owner": GHOST}
    _write_registry(repo, data)


def _duplicate_key(repo: Path, forged_last: bool) -> None:
    """Write the entry of ``c`` twice, once with a forged owner.

    ``json.loads`` keeps the last of two equal keys, so with the forged copy
    last the forged owner would win.
    """
    text = (repo / REGISTRY_FILENAME).read_text(encoding="utf-8")
    entry = re.search(r'\n\s+"c": \{[^}]*\}', text)
    assert entry is not None
    genuine = entry.group(0)
    forged = genuine.replace(KEEP, GHOST)
    pair = genuine + "," + forged if forged_last else forged + "," + genuine
    (repo / REGISTRY_FILENAME).write_text(text.replace(genuine, pair), encoding="utf-8")


def _duplicate_key_forged_last(repo: Path, _plan: MigrationPlan) -> None:
    _duplicate_key(repo, forged_last=True)


def _without_history_repairs(repo: Path, plan: MigrationPlan) -> None:
    for path in plan.history_repair.changed:
        if git(repo, "ls-tree", "--name-only", plan.base.commit, path):
            git(repo, "checkout", plan.base.commit, "--", path)
        else:
            (repo / path).unlink()


def _without_tombstones(repo: Path, plan: MigrationPlan) -> None:
    """The planned manifest minus the restored records, written around the writer's guard."""
    restored = {tomb.doc_id for tomb in plan.restored}
    documents = {d: r for d, r in plan.planned_manifest.documents.items() if d not in restored}
    manifest = plan.planned_manifest.model_copy(update={"documents": documents})
    (repo / "manifest.json").write_text(manifest_json(manifest), encoding="utf-8")


FORGERIES: dict[str, Callable[[Path, MigrationPlan], None]] = {
    "owner-of-a-current-address-does-not-exist": lambda r, _p: _set_owner(r, "c", GHOST),
    "owner-of-a-removed-address-does-not-exist": lambda r, _p: _set_owner(r, "x", GHOST),
    "two-owners-swapped": _swap,
    "phantom-entry": _phantom,
    "duplicate-key-forged-owner-last": _duplicate_key_forged_last,
    "without-the-history-repairs": _without_history_repairs,
    "without-the-restored-tombstones": _without_tombstones,
}


def _migrate(tmp_path: Path, forge: Callable[[Path, MigrationPlan], None] | None) -> Path:
    repo, _ = _base_repo(tmp_path)
    plan = plan_migration(repo, "HEAD")
    apply_plan(repo, plan)
    if forge is not None:
        forge(repo, plan)
    commit_all(repo, "migration: repair document identity (#871)")
    return repo


def _audit_finding(repo: Path) -> str:
    """The ``identity_schema`` findings of ``lovspor audit``, joined."""
    findings = audit_published_corpus(repo, None).findings
    return " ".join(f.detail for f in findings if f.kind == "identity_schema")


def test_the_complete_migration_passes_the_gate(tmp_path: Path) -> None:
    repo = _migrate(tmp_path, None)

    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    assert _audit_finding(repo) == ""


@pytest.mark.parametrize("forgery", sorted(FORGERIES))
def test_a_forged_migration_commit_is_refused_at_sync_start_and_by_the_audit(
    tmp_path: Path, forgery: str
) -> None:
    repo = _migrate(tmp_path, FORGERIES[forgery])

    with pytest.raises(IdentitySchemaError):
        verified_corpus_identity(repo)
    with pytest.raises(IdentitySchemaError):
        _ensure_clean_corpus(repo)
    assert _audit_finding(repo) != ""


def test_the_refusal_names_the_misowned_address(tmp_path: Path) -> None:
    repo = _migrate(tmp_path, _swap)

    with pytest.raises(IdentitySchemaError, match=r"forskrifter/c.*owned by " + NEW):
        verified_corpus_identity(repo)


def test_a_missing_owner_is_named(tmp_path: Path) -> None:
    repo = _migrate(tmp_path, _phantom)

    with pytest.raises(IdentitySchemaError, match=f"forskrifter/zz-phantom.*{GHOST}"):
        verified_corpus_identity(repo)


@pytest.mark.parametrize("forged_last", [True, False], ids=["forged-last", "forged-first"])
def test_a_duplicate_key_is_refused_whichever_owner_would_win(
    tmp_path: Path, forged_last: bool
) -> None:
    repo = _migrate(tmp_path, lambda r, _p: _duplicate_key(r, forged_last))

    with pytest.raises(IdentitySchemaError, match="duplicate key"):
        verified_corpus_identity(repo)


def test_the_partial_migration_of_the_issue_is_refused(tmp_path: Path) -> None:
    """#911 as filed: declaration and registry written, base manifest kept, no renames."""
    repo, _ = _base_repo(tmp_path)
    plan = plan_migration(repo, "HEAD")
    (repo / REGISTRY_FILENAME).write_text(registry_json(plan.planned_registry), encoding="utf-8")
    (repo / "identity.json").write_text('{"schema_version": 1}\n', encoding="utf-8")
    commit_all(repo, "migration: declaration and registry only")

    with pytest.raises(
        IdentitySchemaError, match=f"forskrifter/x holds {NEW} but is owned by {OLD}"
    ):
        verified_corpus_identity(repo)


def test_every_restored_document_stays_an_owner_or_prior_holder(tmp_path: Path) -> None:
    """The control for the tombstone case: the complete migration keeps both restored ids."""
    repo = _migrate(tmp_path, None)

    documents = json.loads((repo / "manifest.json").read_text(encoding="utf-8"))["documents"]
    assert {OLD, LOST} <= set(documents)
