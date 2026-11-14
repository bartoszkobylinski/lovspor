"""Append-only history keyed by ``doc_id`` (#871, ADR-0026 draft point 3, guard 5c).

``<dataset>/history/by-id/<doc_id>.json`` holds one document's events and
nothing else, each with the path the file had at it. It never changes
identity (the file named for a ``doc_id`` only ever holds that ``doc_id``)
and never loses an event: an event the prior file holds and a new extraction
does not is kept. ``history/<slug>.json`` stays as it is for its existing
readers (MCP, Politik: schema 1, unchanged), with one added promise: it is
written only for the slug's registered owner, so its ``doc_id`` is the
slug's permanent owner.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import HistoryIdentityError, ParseError, SlugOwnershipError
from lovspor.history import HistoryEvent, HistoryRecord, Lineage, write_history
from lovspor.identity.registry import LegacyCorpus, corpus_identity

BY_ID_DIRNAME = "by-id"
IDENTITY_HISTORY_SCHEMA_VERSION = 1
#: ``history/<slug>.json``, ``history/<slug>.md`` and ``history/by-id/<doc_id>.json``.
FILES_PER_HISTORY = 3
_SAFE_DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class IdentityEvent(HistoryEvent):
    """A history event with the path the document's file had at it."""

    path: str


class IdentityHistory(BaseModel):
    """One document's events, newest first; append-only, identity fixed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = IDENTITY_HISTORY_SCHEMA_VERSION
    doc_id: str
    events: list[IdentityEvent]

    @field_validator("doc_id")
    @classmethod
    def _doc_id_is_a_safe_filename(cls, value: str) -> str:
        """The id names a file under ``by-id/``; refuse anything that could escape it."""
        if not _SAFE_DOC_ID.match(value) or ".." in value:
            raise ValueError(f"doc_id is not a safe file name: {value!r}")
        return value


def identity_history(lineage: Lineage) -> IdentityHistory:
    """The by-id record of a freshly extracted lineage."""
    events = [IdentityEvent(**s.event.model_dump(), path=s.path) for s in lineage.steps]
    return IdentityHistory(doc_id=lineage.doc_id, events=events)


def _event_key(event: IdentityEvent) -> tuple[str, str, str]:
    return event.commit, event.type, event.path


def merge_append_only(prior: IdentityHistory | None, new: IdentityHistory) -> IdentityHistory:
    """``new`` plus every event of ``prior`` it lacks; refuses an identity change.

    An event already written is never rewritten: where ``new`` re-extracts an
    event of ``prior`` (same commit, type and path), the written one is kept.
    With nothing to add or keep, ``new`` is returned as extracted. Otherwise
    the list is re-sorted newest first by date (a stable sort, so events of
    one date keep their order).
    """
    if prior is None:
        return new
    if prior.doc_id != new.doc_id:
        raise HistoryIdentityError(
            f"history of {prior.doc_id} would be rewritten as {new.doc_id} — "
            "a per-document history never changes identity (#871)",
        )
    written = {_event_key(event): event for event in prior.events}
    events = [written.get(_event_key(event), event) for event in new.events]
    known = {_event_key(event) for event in new.events}
    kept = [event for event in prior.events if _event_key(event) not in known]
    if not kept and events == new.events:
        return new
    merged = sorted([*events, *kept], key=lambda event: event.date, reverse=True)
    return new.model_copy(update={"events": merged})


def by_id_path(dataset_dir: Path, doc_id: str) -> Path:
    return dataset_dir / "history" / BY_ID_DIRNAME / f"{doc_id}.json"


def read_identity_history(path: Path) -> IdentityHistory | None:
    """The by-id record at ``path``, or ``None`` when there is none yet."""
    if not path.exists():
        return None
    try:
        return IdentityHistory.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ParseError(f"{path}: unreadable per-document history: {exc}") from exc


def identity_history_json(history: IdentityHistory) -> str:
    """By-id JSON as written: sorted keys, two-space indent, final newline."""
    data = history.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_identity_history(history: IdentityHistory, dataset_dir: Path) -> Path:
    """Merge ``history`` into its by-id file (append-only) and write it."""
    path = by_id_path(dataset_dir, history.doc_id)
    merged = merge_append_only(read_identity_history(path), history)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, identity_history_json(merged))
    return path


def _ensure_slug_owner(repo: Path, directory: str, record: HistoryRecord) -> None:
    """Refuse a ``history/<slug>.json`` that would name a document other than the owner.

    A legacy corpus (identity schema 0) has no owners to check; a corpus that
    declares the enforced schema without a readable registry is refused.
    """
    identity = corpus_identity(repo)
    if isinstance(identity, LegacyCorpus):
        return
    owner = identity.registry.owner_of(directory, record.slug)
    if owner is not None and owner != record.doc_id:
        raise SlugOwnershipError(
            f"{directory}/history/{record.slug}.json belongs to {owner}; refusing to "
            f"write {record.doc_id}'s history there (#871)",
        )


def write_document_history(
    repo: Path,
    lineage: Lineage,
    record: HistoryRecord,
    dataset_dir: Path,
) -> list[Path]:
    """Write a document's slug history and its by-id history; the paths to stage.

    ``record`` is ``lineage.record(slug)``. ``FILES_PER_HISTORY`` paths are
    returned: the slug's JSON and Markdown and the by-id JSON.
    """
    _ensure_slug_owner(repo, dataset_dir.name, record)
    json_path, md_path = write_history(record, dataset_dir)
    return [json_path, md_path, write_identity_history(identity_history(lineage), dataset_dir)]
