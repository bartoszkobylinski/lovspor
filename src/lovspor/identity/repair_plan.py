"""Plan the repaired histories of the slugs whose history names a non-owner (#871).

For each such slug the events of its path chain are attributed commit by
commit (``lovspor.identity.repair``); nothing is copied from the history
files at the base, which are the defect. The plan then writes:

- ``history/<slug>.json`` (and its ``.md`` view), schema 1, ``doc_id`` = the
  registry owner, holding the owner's events only;
- ``history/<new-slug>.json`` (and ``.md``) for each later holder the plan
  renames, holding its events with their original ``path`` kept in by-id;
- ``history/by-id/<doc_id>.json`` for every document the chain attributes an
  event to, merged append-only into the file at the base. An event that file
  already holds and the attribution gives another document blocks the plan:
  an append-only store is never rewritten silently.

Invariant, checked and reported: every attributed event is in exactly one
document's by-id history, the one the attribution names.
"""

import json
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import IdentityMigrationError
from lovspor.history import HistoryRecord, history_json, render_history_markdown
from lovspor.identity.corpus_git import read_blob, read_blobs
from lovspor.identity.history import (
    BY_ID_DIRNAME,
    IdentityEvent,
    IdentityHistory,
    identity_history_json,
    merge_append_only,
)
from lovspor.identity.repair import PathTrace, TracedEvent, trace_path


class RepairTarget(BaseModel):
    """A slug whose history names a non-owner, its owner, and the holders the plan renames."""

    model_config = ConfigDict(frozen=True)

    directory: str
    slug: str
    owner: str
    file_doc_id: str | None
    renamed: dict[str, str]


class BaseFileEvent(BaseModel):
    """An event the slug history at the base holds, and who the attribution gives it to."""

    model_config = ConfigDict(frozen=True)

    commit: str
    type: str | None
    attributed_to: str | None


class SlugRepair(BaseModel):
    """One target, its traced path chain and the events its base file held."""

    model_config = ConfigDict(frozen=True)

    target: RepairTarget
    trace: PathTrace
    base_file_events: list[BaseFileEvent]


class HistoryConflict(BaseModel):
    """A history file at the base that holds an event the attribution gives another document."""

    model_config = ConfigDict(frozen=True)

    file: str
    commit: str
    path: str | None
    doc_in_file: str | None
    attributed_to: str | None


class HistoryRepair(BaseModel):
    """Every repaired history file, which of them change, and what blocks the repair."""

    model_config = ConfigDict(frozen=True)

    repairs: list[SlugRepair] = []
    files: dict[str, str] = {}
    changed: list[str] = []
    conflicts: list[HistoryConflict] = []
    invariant_violations: list[str] = []


def _history_path(directory: str, name: str, suffix: str = ".json") -> str:
    return f"{directory}/history/{name}{suffix}"


def _by_id_file(directory: str, doc_id: str) -> str:
    return f"{directory}/history/{BY_ID_DIRNAME}/{doc_id}.json"


def _base_file_events(
    repo: Path, commit: str, target: RepairTarget, trace: PathTrace
) -> list[BaseFileEvent]:
    """The base slug file's events, each with the document the attribution gives it."""
    blob = read_blob(repo, commit, _history_path(target.directory, target.slug))
    try:
        data = json.loads((blob or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    events = data.get("events") if isinstance(data, dict) else None
    owners = {e.event.commit: e.doc_id for e in trace.events}
    return [
        BaseFileEvent(
            commit=str(e.get("commit")),
            type=e.get("type"),
            attributed_to=owners.get(str(e.get("commit"))),
        )
        for e in (events if isinstance(events, list) else [])
        if isinstance(e, dict)
    ]


def _slug_repair(repo: Path, commit: str, target: RepairTarget) -> SlugRepair:
    trace = trace_path(repo, commit, f"{target.directory}/{target.slug}.md")
    events = _base_file_events(repo, commit, target, trace)
    return SlugRepair(target=target, trace=trace, base_file_events=events)


def _records(repair: SlugRepair) -> list[HistoryRecord]:
    """The owner's slug history and one for each renamed holder, each its own events."""
    names = {repair.target.owner: repair.target.slug, **repair.target.renamed}
    return [
        HistoryRecord(
            slug=slug,
            doc_id=doc_id,
            events=[e.event for e in repair.trace.events if e.doc_id == doc_id],
        )
        for doc_id, slug in names.items()
    ]


def _slug_files(repair: SlugRepair) -> dict[str, str]:
    files: dict[str, str] = {}
    for record in _records(repair):
        files[_history_path(repair.target.directory, record.slug)] = history_json(record)
        files[_history_path(repair.target.directory, record.slug, ".md")] = render_history_markdown(
            record
        )
    return files


def _attribution(repairs: list[SlugRepair]) -> dict[tuple[str, str, str], str]:
    """``(directory, short commit, path) -> doc_id`` for every attributed event."""
    return {
        (r.target.directory, e.event.commit, e.path): e.doc_id
        for r in repairs
        for e in r.trace.events
    }


def _grouped(repairs: list[SlugRepair]) -> dict[tuple[str, str], list[TracedEvent]]:
    """Every attributed event, by ``(directory, doc_id)``, newest first per chain."""
    groups: dict[tuple[str, str], list[TracedEvent]] = {}
    for repair in repairs:
        for event in repair.trace.events:
            groups.setdefault((repair.target.directory, event.doc_id), []).append(event)
    return dict(sorted(groups.items()))


def _identity_history(doc_id: str, events: list[TracedEvent]) -> IdentityHistory:
    ordered = sorted(events, key=lambda e: e.event.date, reverse=True)
    try:
        return IdentityHistory(
            doc_id=doc_id,
            events=[IdentityEvent(**e.event.model_dump(), path=e.path) for e in ordered],
        )
    except ValidationError as exc:
        raise IdentityMigrationError(f"cannot hold the history of {doc_id!r}: {exc}") from exc


def _prior(blob: bytes | None, where: str) -> IdentityHistory | None:
    if blob is None:
        return None
    try:
        return IdentityHistory.model_validate(json.loads(blob.decode("utf-8")))
    except (UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise IdentityMigrationError(f"unreadable per-document history {where}: {exc}") from exc


def _prior_conflicts(
    where: str, prior: IdentityHistory, directory: str, owners: dict[tuple[str, str, str], str]
) -> list[HistoryConflict]:
    """Events of a by-id file at the base that the attribution gives another document."""
    return [
        HistoryConflict(
            file=where,
            commit=e.commit,
            path=e.path,
            doc_in_file=prior.doc_id,
            attributed_to=owners[(directory, e.commit, e.path)],
        )
        for e in prior.events
        if owners.get((directory, e.commit, e.path), prior.doc_id) != prior.doc_id
    ]


class _ByIdPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    histories: dict[str, IdentityHistory]
    conflicts: list[HistoryConflict]


def _by_id_plan(repo: Path, commit: str, repairs: list[SlugRepair]) -> _ByIdPlan:
    """The merged by-id history of every attributed document, and the conflicts at the base."""
    groups = _grouped(repairs)
    paths = [_by_id_file(directory, doc_id) for directory, doc_id in groups]
    blobs = read_blobs(repo, commit, paths)
    owners = _attribution(repairs)
    histories: dict[str, IdentityHistory] = {}
    conflicts: list[HistoryConflict] = []
    for path, ((directory, doc_id), events) in zip(paths, groups.items(), strict=True):
        prior = _prior(blobs[path], f"{commit}:{path}")
        new = _identity_history(doc_id, events)
        if prior is not None:
            conflicts += _prior_conflicts(path, prior, directory, owners)
        histories[path] = merge_append_only(prior, new)
    return _ByIdPlan(histories=histories, conflicts=conflicts)


def _invariant(repairs: list[SlugRepair], histories: dict[str, IdentityHistory]) -> list[str]:
    """Every attributed event in exactly one planned by-id history: its document's."""
    holders: dict[tuple[str, str, str], set[str]] = {}
    for path, history in histories.items():
        directory = PurePosixPath(path).parts[0]
        for event in history.events:
            holders.setdefault((directory, event.commit, event.path), set()).add(history.doc_id)
    return [
        f"{directory}: {commit} at {path} is in {sorted(holders.get(key, set()))}, "
        f"attributed to {doc_id}"
        for key, doc_id in _attribution(repairs).items()
        for directory, commit, path in [key]
        if holders.get(key, set()) != {doc_id}
    ]


def _renamed_conflicts(repo: Path, commit: str, repairs: list[SlugRepair]) -> list[HistoryConflict]:
    """A renamed holder's new slug history that the base already gives another document."""
    conflicts: list[HistoryConflict] = []
    for repair in repairs:
        for doc_id, slug in sorted(repair.target.renamed.items()):
            path = _history_path(repair.target.directory, slug)
            blob = read_blob(repo, commit, path)
            named = _named_doc(blob)
            if blob is not None and named != doc_id:
                conflicts.append(
                    HistoryConflict(
                        file=path, commit="", path=None, doc_in_file=named, attributed_to=doc_id
                    )
                )
    return conflicts


def _named_doc(blob: bytes | None) -> str | None:
    try:
        data = json.loads((blob or b"").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    value = data.get("doc_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def _changed(repo: Path, commit: str, files: dict[str, str]) -> list[str]:
    blobs = read_blobs(repo, commit, list(files))
    return sorted(path for path, text in files.items() if blobs[path] != text.encode("utf-8"))


def plan_history_repair(repo: Path, commit: str, targets: list[RepairTarget]) -> HistoryRepair:
    """The repaired slug and by-id histories of ``targets`` at ``commit``; reads, never writes."""
    repairs = [_slug_repair(repo, commit, target) for target in targets]
    by_id = _by_id_plan(repo, commit, repairs)
    files = {path: identity_history_json(h) for path, h in by_id.histories.items()}
    for repair in repairs:
        files.update(_slug_files(repair))
    files = dict(sorted(files.items()))
    return HistoryRepair(
        repairs=repairs,
        files=files,
        changed=_changed(repo, commit, files),
        conflicts=[*by_id.conflicts, *_renamed_conflicts(repo, commit, repairs)],
        invariant_violations=_invariant(repairs, by_id.histories),
    )
