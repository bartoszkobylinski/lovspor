"""A removed later holder keeps a distinct, recoverable identity (#871, owner decision).

The four post-threshold ``endr-i-…`` slugs (bilforskriften, tek17,
tollavgift, utrulling) have this shape: the first holder was removed and its
record lost in the threshold sync, a later document took the slug after the
threshold and has since been removed too. The plan keeps both tombstones on
the owner's slug, which is only sound if the later holder stays a document
of its own: its own manifest record and ``doc_id``, its own by-id history,
listed in the plan, and no registry entry or slug history that files its
events under the owner, or the owner's under it.
"""

import json
from pathlib import Path

import pytest

from lovspor.errors import HistoryIdentityError, SlugOwnershipError
from lovspor.history import extract_lineage
from lovspor.identity.apply import apply_plan
from lovspor.identity.history import (
    by_id_path,
    identity_history,
    write_document_history,
    write_identity_history,
)
from lovspor.identity.plan import MigrationPlan, plan_migration
from lovspor.identity.registry import FIRST_HOLDER
from lovspor.identity.report import write_report
from tests.unit.identity_git_fixtures import (
    commit_all,
    doc_text,
    init_repo,
    record,
    write_doc,
    write_manifest_json,
)

SLUG = "endr-i-tollavgift"
PATH = f"forskrifter/{SLUG}.md"
OWNER = "sf-20250101-0001"  # first holder, removed, record lost at the threshold
LATER = "sf-20260901-0002"  # took the slug after the threshold, removed since
KEEP = "sf-20250101-0003"


def _slug_history(repo: Path, doc_id: str) -> None:
    payload = {"doc_id": doc_id, "events": [], "schema_version": 1, "slug": SLUG}
    write_doc(repo, f"forskrifter/history/{SLUG}.json", json.dumps(payload) + "\n")


def _manifest(repo: Path, documents: dict[str, dict], day: str) -> None:
    write_manifest_json(repo, {KEEP: record(KEEP, "c"), **documents}, f"2026-{day}T00:00:00Z")


def _build(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = init_repo(tmp_path / "lovverk")
    write_doc(repo, "forskrifter/c.md", doc_text(KEEP, "c", "body"))
    write_doc(repo, PATH, doc_text(OWNER, SLUG, "owner body"))
    _slug_history(repo, OWNER)
    _manifest(repo, {OWNER: record(OWNER, SLUG)}, "05-01")
    shas = {"owner_add": commit_all(repo, f"add(forskrift): {SLUG}")}
    (repo / PATH).unlink()
    _manifest(repo, {OWNER: record(OWNER, SLUG, "removed")}, "05-02")
    shas["owner_remove"] = commit_all(repo, f"remove(forskrift): {SLUG}")
    _manifest(repo, {}, "07-02")
    shas["threshold"] = commit_all(repo, "sync: update manifest, index, and history")
    write_doc(repo, PATH, doc_text(LATER, SLUG, "later body"))
    _slug_history(repo, LATER)
    _manifest(repo, {LATER: record(LATER, SLUG)}, "09-01")
    shas["later_add"] = commit_all(repo, f"add(forskrift): {SLUG}")
    (repo / PATH).unlink()
    _manifest(repo, {LATER: record(LATER, SLUG, "removed")}, "10-01")
    shas["later_remove"] = commit_all(repo, f"remove(forskrift): {SLUG}")
    return repo, shas


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    return _build(tmp_path)


def _plan(corpus: tuple[Path, dict[str, str]]) -> MigrationPlan:
    repo, shas = corpus
    return plan_migration(repo, "HEAD", shas["threshold"])


def test_each_holder_keeps_its_own_manifest_record(corpus: tuple[Path, dict[str, str]]) -> None:
    documents = _plan(corpus).planned_manifest.documents

    assert documents[OWNER].status == documents[LATER].status == "removed"
    assert documents[OWNER].slug == documents[LATER].slug == SLUG
    assert documents[OWNER].markdown_path == PATH  # the owner keeps the path (#912)
    assert documents[LATER].markdown_path == f"forskrifter/removed/{LATER}.md"
    assert (documents[OWNER].xml_hash, documents[LATER].xml_hash) == (
        f"hash-{OWNER}",
        f"hash-{LATER}",
    )
    assert documents[LATER].title == f"Title of {LATER}"
    assert documents[LATER].removed_reason is None  # its own record, not a restored one


def test_the_registry_names_the_owner_and_never_the_later_holder(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    plan = _plan(corpus)

    entry = plan.planned_registry.slugs["forskrifter"][SLUG]
    assert (entry.owner, entry.basis, entry.prior_holders) == (OWNER, FIRST_HOLDER, ())
    assert plan.renames == []  # a removed holder serves nothing, so nothing moves


def test_the_plan_reports_the_later_holder_as_a_document_of_its_own(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    plan = _plan(corpus)

    [fate] = plan.fates
    assert (fate.slug, fate.owner, fate.reuse) == (SLUG, OWNER, ["post-threshold"])
    assert [(h.doc_id, h.role, h.status, h.restored, h.renamed_to) for h in fate.holders] == [
        (OWNER, "owner", "removed", True, None),
        (LATER, "later", "removed", False, None),
    ]
    assert [(c.restored_doc_id, c.holder_at_base, c.outcome) for c in plan.collisions] == [
        (OWNER, LATER, f"no file moves: both are tombstones; the slug stays with {OWNER}")
    ]


def test_the_slug_history_naming_the_later_holder_is_flagged_for_the_owner(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    """``history/<slug>.json`` still names the later holder at the base; the plan
    lists it for the rebuild instead of letting it stand as the owner's history."""
    plan = _plan(corpus)

    assert [(m.path, m.doc_id_in_file, m.owner) for m in plan.history_mismatches] == [
        (f"forskrifter/history/{SLUG}.json", LATER, OWNER)
    ]


def test_the_report_lists_the_later_holder(
    corpus: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_report(_plan(corpus), out)

    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    [fate] = data["slug_fates"]
    assert [(h["doc_id"], h["role"]) for h in fate["holders"]] == [
        (OWNER, "owner"),
        (LATER, "later"),
    ]
    assert data["threshold_exceptions"] == []
    planned = json.loads((out / "planned/manifest.json").read_text(encoding="utf-8"))
    assert {OWNER, LATER} <= set(planned["documents"])


def test_the_two_lineages_never_share_an_event(corpus: tuple[Path, dict[str, str]]) -> None:
    repo, shas = corpus

    later = identity_history(extract_lineage(repo, PATH, LATER))
    owner = identity_history(extract_lineage(repo, PATH, OWNER))

    assert [event.commit for event in later.events] == [
        shas["later_remove"][:7],
        shas["later_add"][:7],
    ]
    later_commits = {event.commit for event in later.events}
    assert later_commits.isdisjoint({event.commit for event in owner.events})
    assert shas["owner_remove"][:7] not in later_commits


def test_each_holder_has_its_own_by_id_history(
    corpus: tuple[Path, dict[str, str]], tmp_path: Path
) -> None:
    repo, _ = corpus
    dataset = tmp_path / "forskrifter"
    later = identity_history(extract_lineage(repo, PATH, LATER))

    later_path = write_identity_history(later, dataset)

    assert later_path == by_id_path(dataset, LATER) != by_id_path(dataset, OWNER)
    assert json.loads(later_path.read_text(encoding="utf-8"))["doc_id"] == LATER
    assert not by_id_path(dataset, OWNER).exists()
    owner_file = by_id_path(dataset, OWNER)
    owner_file.write_text(later_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(HistoryIdentityError, match=LATER):
        write_identity_history(later.model_copy(update={"doc_id": OWNER}), dataset)


def test_after_the_repair_the_later_holder_never_writes_the_owners_slug_history(
    corpus: tuple[Path, dict[str, str]],
) -> None:
    repo, _ = corpus
    apply_plan(repo, _plan(corpus))
    lineage = extract_lineage(repo, PATH, LATER)
    by_id = by_id_path(repo / "forskrifter", LATER)
    repaired = by_id.read_bytes()  # the repair wrote the later holder's own by-id history

    with pytest.raises(SlugOwnershipError, match=OWNER):
        write_document_history(repo, lineage, lineage.record(SLUG), repo / "forskrifter")

    assert by_id.read_bytes() == repaired
    slug_history = json.loads((repo / f"forskrifter/history/{SLUG}.json").read_text("utf-8"))
    assert slug_history["doc_id"] == OWNER
