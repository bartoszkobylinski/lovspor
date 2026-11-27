"""The registry keeps every historical owner unless an approved list names the change
(#911, #921).

The content checks hold the migration commit's registry to the manifest and
slug histories it ships with, so a migration that re-owns a published address
and rewrites that address's slug history to match passes them. These cases
hold the registry to the owners the migration's base published (a current
record: 200; a slug history: 301 or 410), and to the first owner of every
``threshold-exception`` entry, through the sync-start gate and ``lovspor audit``.
A listed change counts only when the list is approved by its digest, bound to
the migration's base, and (for a threshold exception) its Git evidence holds.
"""

import json
from pathlib import Path

import pytest

from lovspor.errors import IdentitySchemaError
from lovspor.identity.apply import apply_plan
from lovspor.identity.owner_continuity import reassigned, threshold_findings
from lovspor.identity.owner_evidence import evidence_problems
from lovspor.identity.owner_exceptions import (
    REASSIGNMENTS_PATH,
    ReassignmentList,
    ThresholdException,
    load_reassignments,
    read_list,
)
from lovspor.identity.plan import plan_migration
from lovspor.identity.published import migration_base
from lovspor.identity.registry import (
    EnforcedCorpus,
    PriorHolder,
    SlugOwner,
    SlugRegistry,
    read_registry,
)
from lovspor.identity.registry_content import manifest_checks, misowned_histories
from lovspor.identity.registry_history import verified_corpus_identity
from lovspor.storage.manifest import read_manifest
from lovspor.sync.orchestrator import _ensure_clean_corpus
from tests.unit.identity_git_fixtures import commit_all
from tests.unit.identity_owner_fixtures import (
    FIXTURE_U1,
    allow_fixture_u1,
    fixture_exception,
    install,
    list_reassignments,
    pending_list,
    signed,
)
from tests.unit.test_identity_migration import KEEP, LOST, NEW, OLD, _base_repo
from tests.unit.test_identity_registry_content import _audit_finding, _registry, _write_registry

ZERO = "0" * 40


def _migrated(tmp_path: Path, commit: bool = True) -> Path:
    repo, _ = _base_repo(tmp_path)
    apply_plan(repo, plan_migration(repo, "HEAD"))
    if commit:
        commit_all(repo, "migration: repair document identity (#871)")
    return repo


def _threshold_migrated(tmp_path: Path, first_owner: str | None = None) -> tuple[Path, dict]:
    """Threshold r4 keeps NEW on ``x`` over its first holder OLD (first at r1, NEW at r4);
    ``first_owner`` forges the entry's recorded first owner in the migration commit."""
    repo, shas = _base_repo(tmp_path)
    apply_plan(repo, plan_migration(repo, "HEAD", shas["r4"]))
    if first_owner is not None:
        _rewrite_first_owner(repo, first_owner)
    commit_all(repo, "migration: repair document identity (#871)")
    return repo, shas


def _rewrite_first_owner(repo: Path, doc_id: str) -> None:
    data = _registry(repo)
    data["slugs"]["forskrifter"]["x"]["prior_holders"][0]["doc_id"] = doc_id
    _write_registry(repo, data)


def _base_of(repo: Path) -> str:
    base = migration_base(repo)
    assert base is not None
    return base


def _install(monkeypatch: pytest.MonkeyPatch, repo: Path, listed: ReassignmentList) -> Path:
    return install(monkeypatch, repo.parent / "list.json", listed)


def _refused_everywhere(repo: Path, match: str) -> None:
    with pytest.raises(IdentitySchemaError, match=match):
        verified_corpus_identity(repo)
    with pytest.raises(IdentitySchemaError, match=match):
        _ensure_clean_corpus(repo)
    assert "historical owner" in _audit_finding(repo)


def _passes_everywhere(repo: Path) -> None:
    assert isinstance(verified_corpus_identity(repo), EnforcedCorpus)
    _ensure_clean_corpus(repo)
    assert _audit_finding(repo) == ""


def test_an_unlisted_u1_reassignment_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _migrated(tmp_path)
    list_reassignments(monkeypatch, repo, [])

    _refused_everywhere(repo, f"forskrifter/x: {NEW} at the base, {OLD} in the registry")


def test_a_listed_reassignment_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _migrated(tmp_path)
    allow_fixture_u1(monkeypatch, repo)

    _passes_everywhere(repo)


def test_a_listed_reassignment_to_another_document_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _migrated(tmp_path)
    list_reassignments(monkeypatch, repo, [{**FIXTURE_U1, "to_doc_id": KEEP}])

    _refused_everywhere(repo, "forskrifter/x")


def test_an_uncommitted_migration_is_held_to_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _migrated(tmp_path, commit=False)
    list_reassignments(monkeypatch, repo, [])

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
    repo, _ = _base_repo(tmp_path)
    apply_plan(repo, plan_migration(repo, "HEAD"))
    _reown_removed_address(repo)
    commit_all(repo, "migration: repair document identity (#871)")
    allow_fixture_u1(monkeypatch, repo)
    registry = read_registry(repo)
    assert registry is not None
    manifest = read_manifest(repo / "manifest.json")
    assert all(not found for _, found in manifest_checks(registry, manifest))
    assert misowned_histories(repo, registry) == []

    _refused_everywhere(repo, f"forskrifter/d: {LOST} at the base, {KEEP} in the registry")


# --- approval: digest-bound, fail closed ---


def test_a_pending_list_lists_nothing_and_names_the_approval_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _migrated(tmp_path)
    listed = pending_list(_base_of(repo), [FIXTURE_U1])
    _install(monkeypatch, repo, listed)

    _refused_everywhere(repo, "BLOCKED: pending owner approval of digest [0-9a-f]{64}")
    with pytest.raises(
        IdentitySchemaError,
        match=r"identity-exceptions-approve --path \S+/list\.json --digest [0-9a-f]{64} --by",
    ):
        verified_corpus_identity(repo)


def test_a_list_edited_after_approval_lists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _migrated(tmp_path)
    approved = signed(pending_list(_base_of(repo), [FIXTURE_U1]))
    edited_entry = approved.reassignments[0].model_copy(update={"reason": "edited"})
    _install(monkeypatch, repo, approved.model_copy(update={"reassignments": (edited_entry,)}))

    _refused_everywhere(repo, "BLOCKED: approval digest [0-9a-f]{64} is not the list's")


def test_reassignments_reviewed_against_another_base_do_not_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _migrated(tmp_path)
    _install(monkeypatch, repo, signed(pending_list("b" * 40, [FIXTURE_U1])))

    _refused_everywhere(repo, f"for base {'b' * 40}, not the migration's base {_base_of(repo)}")


# --- threshold exceptions: two owners, Git evidence ---


def test_an_unlisted_threshold_exception_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _threshold_migrated(tmp_path)
    list_reassignments(monkeypatch, repo, [])
    entry = (read_registry(repo) or SlugRegistry()).slugs["forskrifter"]["x"]
    assert (entry.basis, entry.served_owner_doc_id, entry.first_owner_doc_id) == (
        "threshold-exception",
        NEW,
        OLD,
    )

    _refused_everywhere(repo, f"forskrifter/x: {NEW} kept over first holder {OLD}, unlisted")


def test_a_listed_threshold_exception_with_its_evidence_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, shas = _threshold_migrated(tmp_path)
    list_reassignments(monkeypatch, repo, [], [fixture_exception(shas["r1"], shas["r4"])])

    _passes_everywhere(repo)


def test_a_pending_threshold_exception_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, shas = _threshold_migrated(tmp_path)
    exception = fixture_exception(shas["r1"], shas["r4"])
    _install(monkeypatch, repo, pending_list(_base_of(repo), [], [exception]))

    _refused_everywhere(repo, f"{NEW} kept over first holder {OLD}, unlisted.*BLOCKED: pending")


def _tampered(shas: dict[str, str], **evidence: str) -> dict[str, object]:
    entry = fixture_exception(shas["r1"], shas["r4"])
    return {**entry, "evidence": {**entry["evidence"], **evidence}}  # type: ignore[dict-item]


def test_re_signed_served_evidence_where_it_did_not_hold_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-signed, so the digest matches: the corpus itself contradicts the evidence."""
    repo, shas = _threshold_migrated(tmp_path)
    list_reassignments(
        monkeypatch, repo, [], [_tampered(shas, served_owner_first_commit=shas["r1"])]
    )

    _refused_everywhere(repo, f"forskrifter/x: served owner {NEW} does not hold it at {shas['r1']}")


def test_re_signed_evidence_outside_the_corpus_history_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, shas = _threshold_migrated(tmp_path)
    list_reassignments(monkeypatch, repo, [], [_tampered(shas, served_owner_first_commit=ZERO)])

    _refused_everywhere(
        repo, f"forskrifter/x: served_owner_first_commit {ZERO} is not in the history of"
    )


def test_a_threshold_before_the_served_owner_arrived_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At r1 the address published OLD: NEW was no owner at that threshold."""
    repo, shas = _threshold_migrated(tmp_path)
    exception = fixture_exception(shas["r1"], shas["r4"])
    listed = signed(pending_list(_base_of(repo), [], [exception], threshold=shas["r1"]))
    _install(monkeypatch, repo, listed)

    _refused_everywhere(repo, f"the owner published at the threshold {shas['r1']} is {OLD}")


def _first_commit_findings(
    repo: Path, monkeypatch: pytest.MonkeyPatch, shas: dict[str, str], commit: str
) -> list[str]:
    """The findings when list and registry both name ``commit`` as OLD's first holding."""
    exception = _tampered(shas, first_owner_first_commit=commit)
    path = _install(monkeypatch, repo, signed(pending_list(_base_of(repo), [], [exception])))
    first = PriorHolder(doc_id=OLD, first_commit=commit, last_commit=commit)
    entry = SlugOwner(
        owner=NEW, assigned_at="2026", basis="threshold-exception", prior_holders=(first,)
    )
    registry = SlugRegistry().assign("forskrifter", "x", entry)
    return threshold_findings(repo, registry, load_reassignments(path), _base_of(repo))


def test_a_first_owner_commit_where_it_did_not_hold_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, shas = _threshold_migrated(tmp_path)

    found = _first_commit_findings(repo, monkeypatch, shas, shas["r4"])

    assert found == [
        f"forskrifter/x: first owner {OLD} does not hold it at {shas['r4']}",
        f"forskrifter/x: {OLD} already held it at {shas['r1']}, before {shas['r4']}",
    ]


def test_a_first_owner_commit_after_its_first_holding_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OLD held x at r2 too, but r1 already had it: r2 is not the first holding."""
    repo, shas = _threshold_migrated(tmp_path)

    found = _first_commit_findings(repo, monkeypatch, shas, shas["r2"])

    assert found == [f"forskrifter/x: {OLD} already held it at {shas['r1']}, before {shas['r2']}"]


def test_the_true_first_owner_commit_has_no_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, shas = _threshold_migrated(tmp_path)

    assert _first_commit_findings(repo, monkeypatch, shas, shas["r1"]) == []


# --- the first owner never changes ---


def test_a_first_owner_rewritten_in_the_migration_commit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry names KEEP as first owner; the approved list names OLD."""
    repo, shas = _threshold_migrated(tmp_path, first_owner=KEEP)
    list_reassignments(monkeypatch, repo, [], [fixture_exception(shas["r1"], shas["r4"])])

    _refused_everywhere(repo, f"forskrifter/x: {NEW} kept over first holder {KEEP}, unlisted")


def test_a_first_owner_rewritten_in_list_and_registry_alike_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both say KEEP, re-signed: the corpus shows OLD, not KEEP, holding x at r1."""
    repo, shas = _threshold_migrated(tmp_path, first_owner=KEEP)
    exception = {**fixture_exception(shas["r1"], shas["r4"]), "first_owner_doc_id": KEEP}
    list_reassignments(monkeypatch, repo, [], [exception])

    _refused_everywhere(repo, f"forskrifter/x: first owner {KEEP} does not hold it at {shas['r1']}")


def test_a_first_owner_rewritten_after_the_migration_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, shas = _threshold_migrated(tmp_path)
    list_reassignments(monkeypatch, repo, [], [fixture_exception(shas["r1"], shas["r4"])])
    _rewrite_first_owner(repo, KEEP)
    commit_all(repo, "sync: rewrite x")

    with pytest.raises(IdentitySchemaError, match=f"forskrifter/x: rewrites .* the entry of {NEW}"):
        verified_corpus_identity(repo)


def test_a_threshold_exception_added_after_the_migration_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later commit cannot add an unreviewed exception: it is not on the approved list."""
    repo = _migrated(tmp_path)
    allow_fixture_u1(monkeypatch, repo)
    data = _registry(repo)
    data["slugs"]["forskrifter"]["zz"] = {
        "owner": KEEP,
        "assigned_at": "2026-11-14",
        "basis": "threshold-exception",
        "prior_holders": [{"doc_id": LOST, "first_commit": ZERO, "last_commit": ZERO}],
    }
    _write_registry(repo, data)
    commit_all(repo, "sync: add zz")

    _refused_everywhere(repo, f"forskrifter/zz: {KEEP} kept over first holder {LOST}, unlisted")


# --- the packaged list ---


def test_the_packaged_list_is_pending_and_lists_nothing() -> None:
    reviewed = load_reassignments()

    assert reviewed.approved is False
    assert (reviewed.changes, reviewed.threshold) == ({}, {})
    assert reviewed.state == f"BLOCKED: pending owner approval of digest {reviewed.digest}"


def test_the_899_repairs_pass_only_as_listed() -> None:
    listed = read_list(REASSIGNMENTS_PATH)
    changes = {(r.directory, r.slug): (r.from_doc_id, r.to_doc_id) for r in listed.reassignments}
    owners = {("forskrifter", "endr-i-bilforskriften"): "sf-20260706-1512"}

    def registry(owner: str) -> SlugRegistry:
        entry = SlugOwner(owner=owner, assigned_at="2026-05-22")
        return SlugRegistry().assign("forskrifter", "endr-i-bilforskriften", entry)

    assert reassigned(owners, registry("sf-20260513-0853"), changes) == []
    assert reassigned(owners, registry("sf-20260324-0472"), changes) != []
    assert reassigned(owners, registry("sf-20260513-0853"), {}) != []


def test_a_first_holder_registry_needs_no_threshold_listing(tmp_path: Path) -> None:
    entry = SlugOwner(owner=OLD, assigned_at="2026")
    registry = SlugRegistry().assign("forskrifter", "x", entry)

    assert threshold_findings(tmp_path, registry, load_reassignments(), None) == []


def test_a_listed_first_commit_other_than_the_registry_s_is_unlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list names r2 as OLD's first holding, the registry r1: not the same entry."""
    repo, shas = _threshold_migrated(tmp_path)
    exception = _tampered(shas, first_owner_first_commit=shas["r2"])
    path = _install(monkeypatch, repo, signed(pending_list(_base_of(repo), [], [exception])))
    registry = read_registry(repo)
    assert registry is not None

    assert threshold_findings(repo, registry, load_reassignments(path), _base_of(repo)) == [
        f"forskrifter/x: {NEW} kept over first holder {OLD}, unlisted"
    ]


def test_an_earlier_holding_beyond_the_parent_is_found(tmp_path: Path) -> None:
    """x is empty at r3 (r4's parent) but OLD held it at r1: NEW at r4 is no first holding."""
    repo, shas = _threshold_migrated(tmp_path)
    claim = ThresholdException.model_validate(
        {
            **fixture_exception(shas["r4"], shas["r1"]),
            "first_owner_doc_id": NEW,
            "served_owner_doc_id": OLD,
        }
    )

    found = evidence_problems(repo, claim, shas["r4"], _base_of(repo))

    assert f"{OLD} already held it at {shas['r1']}, before {shas['r4']}" in found


def test_a_served_commit_after_the_threshold_is_found(tmp_path: Path) -> None:
    repo, shas = _threshold_migrated(tmp_path)
    claim = ThresholdException.model_validate(fixture_exception(shas["r1"], shas["r4"]))

    found = evidence_problems(repo, claim, shas["r3"], _base_of(repo))

    assert found == [
        f"served_owner_first_commit {shas['r4']} is after the threshold {shas['r3']}",
        f"the owner published at the threshold {shas['r3']} is {OLD}",
    ]
