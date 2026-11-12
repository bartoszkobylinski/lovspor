"""Append-only per-document history keyed by doc_id (#871, ADR-0023 point 3)."""

import json
from datetime import date
from pathlib import Path

import pytest

from lovspor.errors import HistoryIdentityError, ParseError, SlugOwnershipError
from lovspor.history import HistoryEvent, Lineage, LineageStep, extract_lineage
from lovspor.identity.history import (
    FILES_PER_HISTORY,
    IdentityEvent,
    IdentityHistory,
    by_id_path,
    identity_history,
    merge_append_only,
    read_identity_history,
    write_document_history,
    write_identity_history,
)
from lovspor.identity.registry import SlugOwner, SlugRegistry, replace_registry
from tests.unit.identity_git_fixtures import commit_all, doc_text, git, init_repo, write_doc

OLD = "sf-20260612-1085"
NEW = "sf-20260911-1792"
SLUG = "endr-i-byggteknisk-forskrift-tek17"


def _event(commit: str, day: int, path: str = "forskrifter/x.md") -> IdentityEvent:
    return IdentityEvent(
        date=date(2026, 9, day),
        commit=commit,
        type="updated",
        subject=f"update(forskrift): {commit}",
        path=path,
    )


def _history(*events: IdentityEvent, doc_id: str = NEW) -> IdentityHistory:
    return IdentityHistory(doc_id=doc_id, events=list(events))


def _lineage(doc_id: str = NEW) -> Lineage:
    event = HistoryEvent(date=date(2026, 9, 17), commit="02c0a52", type="added", subject="add")
    return Lineage(doc_id=doc_id, steps=[LineageStep(event=event, path=f"forskrifter/{SLUG}.md")])


def test_merge_with_no_prior_is_the_extraction() -> None:
    new = _history(_event("b", 2))

    assert merge_append_only(None, new) == new


def test_merge_keeps_an_event_the_extraction_lost() -> None:
    prior = _history(_event("b", 2), _event("a", 1))
    new = _history(_event("c", 3), _event("b", 2))

    merged = merge_append_only(prior, new)

    assert [event.commit for event in merged.events] == ["c", "b", "a"]


def test_merge_without_lost_events_returns_the_extraction_unchanged() -> None:
    new = _history(_event("c", 3), _event("b", 2))

    assert merge_append_only(_history(_event("b", 2)), new) is new


def test_the_same_commit_at_another_path_is_another_event() -> None:
    prior = _history(_event("b", 2, path="forskrifter/old.md"))

    merged = merge_append_only(prior, _history(_event("b", 2)))

    assert len(merged.events) == 2


def test_an_event_already_written_is_never_rewritten() -> None:
    written = _event("b", 2)
    reextracted = written.model_copy(update={"subject": "a different subject"})

    merged = merge_append_only(_history(written), _history(_event("c", 3), reextracted))

    assert merged.events == [_event("c", 3), written]


def test_an_identity_change_is_refused() -> None:
    with pytest.raises(HistoryIdentityError, match=OLD):
        merge_append_only(_history(doc_id=OLD), _history(doc_id=NEW))


@pytest.mark.parametrize("doc_id", ["../x", "a/b", "", ".hidden", "x..y"])
def test_an_unsafe_doc_id_is_refused(doc_id: str) -> None:
    with pytest.raises(ValueError, match="safe file name"):
        IdentityHistory(doc_id=doc_id, events=[])


def test_identity_history_carries_each_events_path() -> None:
    history = identity_history(_lineage())

    assert history.doc_id == NEW
    assert history.events[0].path == f"forskrifter/{SLUG}.md"
    assert history.events[0].commit == "02c0a52"


def test_write_merges_into_the_file_on_disk(tmp_path: Path) -> None:
    dataset = tmp_path / "forskrifter"
    write_identity_history(_history(_event("a", 1)), dataset)

    path = write_identity_history(_history(_event("b", 2)), dataset)

    assert path == dataset / "history" / "by-id" / f"{NEW}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [event["commit"] for event in data["events"]] == ["b", "a"]
    assert data["doc_id"] == NEW
    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_an_unreadable_by_id_file_is_a_parse_error(tmp_path: Path) -> None:
    path = by_id_path(tmp_path, NEW)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ParseError, match="per-document history"):
        read_identity_history(path)


def test_write_document_history_writes_slug_and_by_id_files(tmp_path: Path) -> None:
    dataset = tmp_path / "forskrifter"
    lineage = _lineage()

    paths = write_document_history(tmp_path, lineage, lineage.record(SLUG), dataset)

    assert len(paths) == FILES_PER_HISTORY
    assert paths == [
        dataset / "history" / f"{SLUG}.json",
        dataset / "history" / f"{SLUG}.md",
        dataset / "history" / "by-id" / f"{NEW}.json",
    ]
    assert json.loads(paths[0].read_text(encoding="utf-8"))["doc_id"] == NEW


def test_slug_history_is_written_only_for_the_slugs_owner(tmp_path: Path) -> None:
    owner = SlugOwner(owner=OLD, assigned_at="2026-06-18T00:00:00+00:00")
    replace_registry(SlugRegistry().assign("forskrifter", SLUG, owner), tmp_path)
    lineage = _lineage()

    with pytest.raises(SlugOwnershipError, match=OLD):
        write_document_history(tmp_path, lineage, lineage.record(SLUG), tmp_path / "forskrifter")

    assert not (tmp_path / "forskrifter" / "history").exists()


def test_extracted_lineage_never_crosses_into_the_previous_holder(tmp_path: Path) -> None:
    """The tek17 shape end to end: the new holder's by-id file holds its own
    events only, the old holder's deletion excluded."""
    repo = init_repo(tmp_path / "lovverk")
    path = f"forskrifter/{SLUG}.md"
    write_doc(repo, path, doc_text(OLD, SLUG, "old"))
    commit_all(repo, f"add(forskrift): {SLUG}")
    (repo / path).unlink()
    old_remove = commit_all(repo, f"remove(forskrift): {SLUG}")
    write_doc(repo, path, doc_text(NEW, SLUG, "new"))
    new_add = commit_all(repo, f"add(forskrift): {SLUG}")

    history = identity_history(extract_lineage(repo, path, NEW))

    assert [event.commit for event in history.events] == [new_add[:7]]
    assert history.events[0].path == path
    assert git(repo, "rev-parse", "--short=7", old_remove) not in {e.commit for e in history.events}


def test_each_event_keeps_the_path_it_had_across_a_rename(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "lovverk")
    write_doc(repo, "forskrifter/gammel.md", doc_text(NEW, "gammel", "body"))
    add = commit_all(repo, "add(forskrift): gammel")
    git(repo, "mv", "forskrifter/gammel.md", "forskrifter/ny.md")
    rename = commit_all(repo, "rename(forskrift): ny")

    history = identity_history(extract_lineage(repo, "forskrifter/ny.md", NEW))

    assert [(e.commit, e.path) for e in history.events] == [
        (rename[:7], "forskrifter/ny.md"),
        (add[:7], "forskrifter/gammel.md"),
    ]
