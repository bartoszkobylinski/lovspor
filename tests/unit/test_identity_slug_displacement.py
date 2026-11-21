"""A slug handed to a new document never loses the previous holder's events.

On a legacy corpus (no ``identity.json``) a slug whose holder left the
manifest before tombstones were carried can be reassigned. Writing the new
holder's ``history/<slug>.json`` used to overwrite the only published copy
of the previous holder's events; that holder's lineage, extracted from git
with the paths and identity boundary ``extract_lineage`` uses (its removal
included), is now merged into its ``history/by-id/<doc_id>.json`` first
(append-only).
"""

import json
import logging
from pathlib import Path

import pytest

from lovspor.errors import SlugOwnershipError
from lovspor.history import (
    HistoryRecord,
    _parse_blocks,
    _run_git_log,
    classify_commit,
    extract_earlier_lineage,
    extract_lineage,
    write_history,
)
from lovspor.identity.history import (
    FILES_PER_HISTORY,
    by_id_path,
    identity_history,
    preserve_displaced,
    read_identity_history,
    write_document_history,
)
from lovspor.identity.registry import SlugOwner, SlugRegistry, replace_registry
from lovspor.identity.schema import write_declaration
from lovspor.storage.manifest import ManifestRecord
from lovspor.sync import orchestrator as orchestrator_module
from tests.unit.identity_git_fixtures import (
    commit_all,
    doc_text,
    git,
    init_repo,
    record,
    write_doc,
    write_manifest_json,
)

FIRST = "sf-20260312-0390"
SECOND = "sf-20261116-2362"
THIRD = "sf-20270101-0001"
SLUG = "endr-i-merverdiavgiftsforskriften"
MD = f"forskrifter/{SLUG}.md"


def _publish(
    repo: Path, doc_id: str, holders: dict[str, ManifestRecord] | None = None
) -> list[Path]:
    """Extract and write ``doc_id``'s history for the file at ``MD``, as a sync
    does; ``holders`` are the run's records (none: every earlier holder pruned)."""
    lineage = extract_lineage(repo, MD, doc_id)
    history = lineage.record(SLUG)
    paths = preserve_displaced(repo, history, repo / "forskrifter", holders or {})
    return [*paths, *write_document_history(repo, lineage, history, repo / "forskrifter")]


def _slug_history(repo: Path) -> dict:
    return json.loads(
        (repo / "forskrifter" / "history" / f"{SLUG}.json").read_text(encoding="utf-8")
    )


def _hold_then_remove(repo: Path, doc_id: str, *, by_id_written: bool = False) -> list[str]:
    """``doc_id`` takes the slug, gets its published history, then leaves it.

    ``by_id_written=False`` models a holder whose history was published
    before by-id files existed: its by-id file is removed again. Returns the
    commits its by-id history must hold: its removal, then its published events.
    """
    write_doc(repo, MD, doc_text(doc_id, SLUG, f"{doc_id} v1"))
    commit_all(repo, f"add(forskrift): {SLUG}")
    write_doc(repo, MD, doc_text(doc_id, SLUG, f"{doc_id} v2"))
    commit_all(repo, f"update(forskrift): {SLUG}")
    _publish(repo, doc_id)
    if not by_id_written:
        by_id_path(repo / "forskrifter", doc_id).unlink()
    commit_all(repo, "sync: update manifest, index, and history")
    published = [event["commit"] for event in _slug_history(repo)["events"]]
    (repo / MD).unlink()
    removal = commit_all(repo, f"remove(forskrift): {SLUG}")
    return [removal[:7], *published]


def _take_slug(repo: Path, doc_id: str) -> None:
    write_doc(repo, MD, doc_text(doc_id, SLUG, f"{doc_id} v1"))
    commit_all(repo, f"add(forskrift): {SLUG}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return init_repo(tmp_path / "lovverk")


def test_the_previous_holders_events_move_to_its_by_id_file(repo: Path) -> None:
    published = _hold_then_remove(repo, FIRST)
    _take_slug(repo, SECOND)

    paths = _publish(repo, SECOND)

    preserved = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert preserved is not None
    assert [event.commit for event in preserved.events] == published
    assert {event.path for event in preserved.events} == {MD}
    assert _slug_history(repo)["doc_id"] == SECOND
    assert paths[0] == by_id_path(repo / "forskrifter", FIRST)
    assert len(paths) == FILES_PER_HISTORY + 1


def test_preserved_events_are_the_holders_own_extraction_and_its_removal(repo: Path) -> None:
    """Below its removal, the by-id history is exactly the holder's own extraction."""
    write_doc(repo, MD, doc_text(FIRST, SLUG, "v1"))
    commit_all(repo, f"add(forskrift): {SLUG}")
    write_doc(repo, MD, doc_text(FIRST, SLUG, "v2"))
    commit_all(repo, f"update(forskrift): {SLUG}")
    expected = identity_history(extract_lineage(repo, MD, FIRST)).events
    _publish(repo, FIRST)
    by_id_path(repo / "forskrifter", FIRST).unlink()
    (repo / MD).unlink()
    commit_all(repo, f"remove(forskrift): {SLUG}")
    _take_slug(repo, SECOND)

    _publish(repo, SECOND)

    preserved = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert preserved is not None
    assert preserved.events[0].type == "removed"
    assert preserved.events[1:] == expected


def test_a_rewrite_for_the_same_holder_writes_nothing_extra(repo: Path) -> None:
    _take_slug(repo, FIRST)
    _publish(repo, FIRST)

    paths = _publish(repo, FIRST)

    assert len(paths) == FILES_PER_HISTORY
    assert sorted(p.name for p in (repo / "forskrifter" / "history" / "by-id").iterdir()) == [
        f"{FIRST}.json"
    ]


def test_preserving_is_idempotent(repo: Path) -> None:
    _hold_then_remove(repo, FIRST)
    _take_slug(repo, SECOND)
    _publish(repo, SECOND)
    before = by_id_path(repo / "forskrifter", FIRST).read_text(encoding="utf-8")

    paths = _publish(repo, SECOND)

    assert len(paths) == FILES_PER_HISTORY
    assert by_id_path(repo / "forskrifter", FIRST).read_text(encoding="utf-8") == before


def test_an_existing_by_id_file_is_merged_never_rewritten(repo: Path) -> None:
    _hold_then_remove(repo, FIRST, by_id_written=True)
    written = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert written is not None
    _take_slug(repo, SECOND)

    _publish(repo, SECOND)

    merged = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert merged is not None
    assert [e for e in merged.events if e in written.events] == written.events
    assert [e.type for e in merged.events if e not in written.events] == ["removed"]


def test_every_earlier_holder_of_a_slug_keeps_its_events(repo: Path) -> None:
    first = _hold_then_remove(repo, FIRST)
    _take_slug(repo, SECOND)
    _publish(repo, SECOND)
    commit_all(repo, "sync: update manifest, index, and history")
    write_doc(repo, MD, doc_text(SECOND, SLUG, f"{SECOND} v2"))
    commit_all(repo, f"update(forskrift): {SLUG}")
    _publish(repo, SECOND)
    by_id_path(repo / "forskrifter", SECOND).unlink()
    commit_all(repo, "sync: update manifest, index, and history")
    second_published = [event["commit"] for event in _slug_history(repo)["events"]]
    (repo / MD).unlink()
    second = [commit_all(repo, f"remove(forskrift): {SLUG}")[:7], *second_published]
    _take_slug(repo, THIRD)

    _publish(repo, THIRD)

    for doc_id, events in ((FIRST, first), (SECOND, second)):
        kept = read_identity_history(by_id_path(repo / "forskrifter", doc_id))
        assert kept is not None
        assert [event.commit for event in kept.events] == events
    assert _slug_history(repo)["doc_id"] == THIRD


def test_a_rename_moves_the_preserved_path_back(repo: Path) -> None:
    write_doc(repo, "forskrifter/gammel.md", doc_text(FIRST, "gammel", "v1"))
    add = commit_all(repo, "add(forskrift): gammel")
    git(repo, "mv", "forskrifter/gammel.md", MD)
    rename = commit_all(repo, f"rename(forskrift): {SLUG}")
    _publish(repo, FIRST)
    by_id_path(repo / "forskrifter", FIRST).unlink()
    (repo / MD).unlink()
    removal = commit_all(repo, f"remove(forskrift): {SLUG}")
    _take_slug(repo, SECOND)

    _publish(repo, SECOND)

    preserved = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert preserved is not None
    assert [(e.commit, e.path) for e in preserved.events] == [
        (removal[:7], MD),
        (rename[:7], MD),
        (add[:7], "forskrifter/gammel.md"),
    ]


def test_an_unreadable_slug_history_is_logged_and_the_write_goes_on(
    repo: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    history_dir = repo / "forskrifter" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / f"{SLUG}.json").write_text("{not json", encoding="utf-8")
    _take_slug(repo, SECOND)

    with caplog.at_level(logging.WARNING, logger="lovspor.identity.history"):
        paths = _publish(repo, SECOND)

    assert len(paths) == FILES_PER_HISTORY
    assert _slug_history(repo)["doc_id"] == SECOND
    assert "unreadable" in caplog.text


def test_an_enforced_corpus_still_refuses_before_anything_is_written(repo: Path) -> None:
    _hold_then_remove(repo, FIRST)
    owner = SlugOwner(owner=FIRST, assigned_at="2026-06-18T00:00:00+00:00")
    replace_registry(SlugRegistry().assign("forskrifter", SLUG, owner), repo)
    write_declaration(repo)
    _take_slug(repo, SECOND)

    with pytest.raises(SlugOwnershipError, match=FIRST):
        _publish(repo, SECOND)

    assert not by_id_path(repo / "forskrifter", FIRST).exists()
    assert _slug_history(repo)["doc_id"] == FIRST


def _record(doc_id: str, status: str = "current") -> ManifestRecord:
    return ManifestRecord(
        doc_type="forskrift",
        xml_hash=f"hash-{doc_id}",
        markdown_path=MD,
        source_dataset="gjeldende-sentrale-forskrifter",
        last_seen="2026-06-01T00:00:00Z",
        status=status,
        slug=SLUG,
        title=f"Title of {doc_id}",
    )


def test_the_sync_history_step_stages_the_preserved_file_and_leaves_records_alone(
    repo: Path,
) -> None:
    """Manifest continuity: the step changes no record but the target's history fields."""
    published = _hold_then_remove(repo, FIRST)
    _take_slug(repo, SECOND)
    records = {SECOND: _record(SECOND)}

    updated, paths = orchestrator_module._generate_and_apply_history(repo, records, [SECOND])

    assert set(updated) == {SECOND}
    assert by_id_path(repo / "forskrifter", FIRST) in paths
    preserved = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert preserved is not None
    assert [event.commit for event in preserved.events] == published


def test_a_tombstoned_holder_is_not_displaced_by_the_history_step(repo: Path) -> None:
    """A tombstone is skipped as before; its slug history is untouched."""
    _hold_then_remove(repo, FIRST)
    before = _slug_history(repo)
    records = {FIRST: _record(FIRST, status="removed")}

    updated, paths = orchestrator_module._generate_and_apply_history(repo, records, [FIRST])

    assert updated == records
    assert paths == []
    assert _slug_history(repo) == before


def test_an_earlier_holders_events_in_the_slug_file_are_not_attributed(repo: Path) -> None:
    """A slug file written before the identity boundary also held the holder
    before it; only the displaced holder's own events go to its by-id file."""
    _hold_then_remove(repo, FIRST)
    first_events = HistoryRecord.model_validate(_slug_history(repo)).events
    _take_slug(repo, SECOND)
    own = extract_lineage(repo, MD, SECOND).record(SLUG)
    inherited = own.model_copy(update={"events": [*own.events, *first_events]})
    write_history(inherited, repo / "forskrifter")
    commit_all(repo, "sync: update manifest, index, and history")
    (repo / MD).unlink()
    removal = commit_all(repo, f"remove(forskrift): {SLUG}")
    _take_slug(repo, THIRD)

    _publish(repo, THIRD)

    kept = read_identity_history(by_id_path(repo / "forskrifter", SECOND))
    assert kept is not None
    assert [event.commit for event in kept.events] == [
        removal[:7],
        *[e.commit for e in own.events],
    ]
    assert not by_id_path(repo / "forskrifter", FIRST).exists()


def test_a_slug_file_holding_only_another_documents_events_writes_no_by_id(repo: Path) -> None:
    _hold_then_remove(repo, FIRST)
    mislabelled = HistoryRecord.model_validate(_slug_history(repo)).model_copy(
        update={"doc_id": SECOND}
    )
    write_history(mislabelled, repo / "forskrifter")
    commit_all(repo, "sync: update manifest, index, and history")
    _take_slug(repo, THIRD)

    paths = _publish(repo, THIRD)

    assert len(paths) == FILES_PER_HISTORY
    assert not by_id_path(repo / "forskrifter", SECOND).exists()


OLD_PATH = "forskrifter/gammel.md"
BODY = "\n".join(f"line {i}" for i in range(40))


def _move_under_update_subject(repo: Path) -> None:
    """FIRST moves to the slug in a commit whose subject is not a rename."""
    write_doc(repo, OLD_PATH, doc_text(FIRST, "gammel", BODY))
    commit_all(repo, "add(forskrift): gammel")
    git(repo, "mv", OLD_PATH, MD)
    write_doc(repo, MD, doc_text(FIRST, SLUG, BODY + "\nchanged"))
    commit_all(repo, f"update(forskrift): {SLUG}")


def test_a_move_under_an_update_subject_keeps_the_lineage_path(repo: Path) -> None:
    _move_under_update_subject(repo)
    expected = identity_history(extract_lineage(repo, MD, FIRST)).events
    assert [e.path for e in expected] == [MD, OLD_PATH]
    _publish(repo, FIRST)
    by_id_path(repo / "forskrifter", FIRST).unlink()
    (repo / MD).unlink()
    commit_all(repo, f"remove(forskrift): {SLUG}")
    _take_slug(repo, SECOND)

    _publish(repo, SECOND)

    kept = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert kept is not None
    assert kept.events[1:] == expected


def test_a_foreign_document_behind_an_untracked_move_is_not_attributed(repo: Path) -> None:
    """An old-style slug file holds THIRD's events too: THIRD held gammel.md first."""
    write_doc(repo, OLD_PATH, doc_text(THIRD, "gammel", BODY + "\nc"))
    commit_all(repo, "add(forskrift): gammel")
    (repo / OLD_PATH).unlink()
    commit_all(repo, "remove(forskrift): gammel")
    _move_under_update_subject(repo)
    events = [
        event
        for sha, day, subject, stats in _parse_blocks(_run_git_log(repo, MD))
        if (event := classify_commit(sha, day, subject, stats)) is not None
    ]
    write_history(HistoryRecord(slug=SLUG, doc_id=FIRST, events=events), repo / "forskrifter")
    commit_all(repo, "sync: update manifest, index, and history")
    (repo / MD).unlink()
    commit_all(repo, f"remove(forskrift): {SLUG}")
    _take_slug(repo, SECOND)

    _publish(repo, SECOND)

    kept = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert kept is not None
    assert "remove(forskrift): gammel" not in [e.subject for e in kept.events]
    assert "add(forskrift): gammel" in [e.subject for e in kept.events]


def _moved_holder() -> dict[str, ManifestRecord]:
    """The run's records: FIRST current at ny.md."""
    return {FIRST: ManifestRecord.model_validate(record(FIRST, "ny"))}


def test_a_holder_moved_to_another_slug_is_left_to_its_own_sync(repo: Path) -> None:
    """The previous holder moved and is current: its own history is not duplicated."""
    write_doc(repo, MD, doc_text(FIRST, SLUG, "v1"))
    commit_all(repo, f"add(forskrift): {SLUG}")
    _publish(repo, FIRST)
    by_id_path(repo / "forskrifter", FIRST).unlink()
    git(repo, "mv", MD, "forskrifter/ny.md")
    commit_all(repo, "rename(forskrift): ny")
    commit_all(repo, "sync: update manifest, index, and history")
    _take_slug(repo, SECOND)

    paths = _publish(repo, SECOND, _moved_holder())

    assert len(paths) == FILES_PER_HISTORY
    assert not by_id_path(repo / "forskrifter", FIRST).exists()
    own = extract_lineage(repo, "forskrifter/ny.md", FIRST)
    write_document_history(repo, own, own.record("ny"), repo / "forskrifter")
    kept = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert kept is not None
    commits = [event.commit for event in kept.events]
    assert len(commits) == len(set(commits))


def test_a_moved_holder_keeps_the_events_its_own_lineage_cannot_reach(repo: Path) -> None:
    """A move git does not see as a rename: the holder's own lineage starts at
    its new path, so only its older events are preserved, each once."""
    write_doc(repo, MD, doc_text(FIRST, SLUG, BODY))
    add = commit_all(repo, f"add(forskrift): {SLUG}")
    _publish(repo, FIRST)
    by_id_path(repo / "forskrifter", FIRST).unlink()
    (repo / MD).unlink()
    write_doc(repo, "forskrifter/ny.md", doc_text(FIRST, "ny", "entirely rewritten"))
    move = commit_all(repo, "update(forskrift): ny")
    commit_all(repo, "sync: update manifest, index, and history")
    _take_slug(repo, SECOND)

    _publish(repo, SECOND, _moved_holder())

    kept = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert kept is not None
    assert [event.commit for event in kept.events] == [add[:7]]
    own = extract_lineage(repo, "forskrifter/ny.md", FIRST)
    assert [step.event.commit for step in own.steps] == [move[:7]]
    write_document_history(repo, own, own.record("ny"), repo / "forskrifter")
    merged = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert merged is not None
    assert sorted(event.commit for event in merged.events) == sorted([add[:7], move[:7]])


NEW_MD = "forskrifter/ny.md"


def _move_then_leave_in_the_run(repo: Path, *, by_id_written: bool) -> list[str]:
    """FIRST holds the slug, moves to ny.md and is published current there;
    then, in one run, it leaves upstream and SECOND gets the slug it left.

    ``manifest.json`` on disk is the prior manifest, still naming FIRST
    current at ny.md, as when the sync writes history. Returns FIRST's
    commits newest first: its removal, its move, its addition.
    """
    write_doc(repo, MD, doc_text(FIRST, SLUG, "v1"))
    add = commit_all(repo, f"add(forskrift): {SLUG}")
    _publish(repo, FIRST)
    git(repo, "mv", MD, NEW_MD)
    move = commit_all(repo, "rename(forskrift): ny")
    own = extract_lineage(repo, NEW_MD, FIRST)
    write_document_history(repo, own, own.record("ny"), repo / "forskrifter")
    if not by_id_written:
        by_id_path(repo / "forskrifter", FIRST).unlink()
    write_manifest_json(repo, {FIRST: record(FIRST, "ny")}, "2026-06-02T00:00:00Z")
    commit_all(repo, "sync: update manifest, index, and history")
    (repo / NEW_MD).unlink()
    removal = commit_all(repo, "remove(forskrift): ny")
    _take_slug(repo, SECOND)
    return [removal[:7], move[:7], add[:7]]


def _run_records() -> dict[str, ManifestRecord]:
    """This run's records: FIRST tombstoned at ny.md, SECOND current at the slug."""
    return {
        FIRST: ManifestRecord.model_validate(record(FIRST, "ny", status="removed")),
        SECOND: _record(SECOND),
    }


@pytest.mark.parametrize("by_id_written", [False, True])
def test_a_holder_removed_in_the_same_run_keeps_its_whole_history_in_by_id(
    repo: Path, by_id_written: bool
) -> None:
    """The run's records, not the prior manifest on disk, decide: FIRST is
    removed in this run, so no sync of its own writes its by-id file again."""
    commits = _move_then_leave_in_the_run(repo, by_id_written=by_id_written)

    updated, paths = orchestrator_module._generate_and_apply_history(repo, _run_records(), [SECOND])

    assert by_id_path(repo / "forskrifter", FIRST) in paths
    kept = read_identity_history(by_id_path(repo / "forskrifter", FIRST))
    assert kept is not None
    assert [event.commit for event in kept.events] == commits
    assert identity_history(extract_lineage(repo, NEW_MD, FIRST)).events == kept.events
    assert updated[FIRST] == _run_records()[FIRST]
    assert _slug_history(repo)["doc_id"] == SECOND


def test_a_holder_back_for_a_second_stretch_is_walked_from_its_newest(repo: Path) -> None:
    """X, then Y, then X again, then Z: X's earlier stretch is behind Y's boundary."""
    write_doc(repo, MD, doc_text(FIRST, SLUG, "first stretch"))
    commit_all(repo, f"add(forskrift): {SLUG}")
    write_doc(repo, MD, doc_text(SECOND, SLUG, "between"))
    commit_all(repo, f"update(forskrift): {SLUG}")
    write_doc(repo, MD, doc_text(FIRST, SLUG, "second stretch"))
    back = commit_all(repo, f"update(forskrift): {SLUG}")
    write_doc(repo, MD, doc_text(THIRD, SLUG, "last"))
    commit_all(repo, f"update(forskrift): {SLUG}")

    lineage = extract_earlier_lineage(repo, MD, FIRST)

    assert [step.event.commit for step in lineage.steps] == [back[:7]]
