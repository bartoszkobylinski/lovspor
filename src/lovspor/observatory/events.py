"""Operator decision history for the source registry (issue #166).

``sources.json`` is presented as the record of who cleared what, but it cannot
be: every command loads the whole registry, replaces a record and rewrites the
file. It is a **current-state** registry, and a state file cannot answer "what
was withdrawn, when, and on whose word" — the previous answer is gone the
moment the next one is written.

So the history lives beside it, append-only, in the same shape as the
observation log: one JSON object per line, locked and fsynced on append,
never rewritten. Three artifacts, three jobs::

    sources.json         what is true now
    source-events.jsonl  what an operator decided, and why
    observations.jsonl   what the servers actually did

An event carries the SHA-256 of the record as it stood *before* the change,
so a later reader can prove which clearance was withdrawn rather than infer
it from a timestamp. The fingerprint is taken over the same canonical JSON
:func:`~lovspor.observatory.registry.write_registry` emits, so it can be
recomputed from an archived registry without knowing how this module works.
"""

import fcntl
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from lovspor.errors import LogIntegrityError, ParseError
from lovspor.observatory.registry import SourceRecord
from lovspor.observatory.storage import ObservatoryRoot

SOURCE_EVENTS_FILENAME = "source-events.jsonl"


class SourceDomainReplaced(BaseModel):
    """An operator moved an authority to a different domain.

    ``from_domain`` and ``to_domain`` rather than ``from`` and ``to`` because
    ``from`` is a Python keyword; nothing else about the shape is disguised.

    ``reason`` and ``changed_by`` are mandatory and free text for the reason
    ``AccessPolicyCheck.reviewed_by`` is: this is a human decision about
    traffic to someone else's server, and an unattributed one is not evidence
    that anybody made it. Neither is ever synthesised — a caller that cannot
    say who decided has to ask, not guess.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event: Literal["source_domain_replaced"] = "source_domain_replaced"
    authority_id: str = Field(min_length=1)
    from_domain: str = Field(min_length=1)
    to_domain: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    changed_at: AwareDatetime
    changed_by: str = Field(min_length=1)
    #: The record as it stood before this event, by content. A timestamp says
    #: when; this says *which*, and survives the registry being rewritten.
    previous_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


#: Every event shape this log holds. A union of one today, written as a name
#: rather than inlined because the file is append-only: a reader added later
#: has to be able to see that today's events were the only kind there were.
SourceEvent = SourceDomainReplaced


def record_fingerprint(record: SourceRecord) -> str:
    """Identify a source record by its content.

    Canonicalised exactly as ``write_registry`` writes it — sorted keys, no
    ASCII escaping — so the fingerprint in an event can be recomputed from an
    archived ``sources.json`` by anyone with a SHA-256 implementation, without
    reading this module or running this engine.
    """
    canonical = json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_events_path(root: ObservatoryRoot) -> Path:
    """Where the decision history lives inside a validated observatory root.

    Beside the registry it explains, and inside the ADR-0010 §5 boundary for
    the same reason: it holds who cleared what, which belongs with the archive
    rather than in a published repository.
    """
    return root.path / SOURCE_EVENTS_FILENAME


def append_source_event(root: ObservatoryRoot, event: SourceEvent) -> None:
    """Append one decision, locked and fsynced before returning.

    Locked because an operator at a terminal and a scheduled job are two
    writers, and fsynced because the archive lives on storage that can go away
    mid-write (ADR-0010 §5). A torn line here costs the answer to which
    clearance was withdrawn — the question this file exists for.
    """
    path = source_events_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(event.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_source_events(path: Path) -> list[SourceEvent]:
    """Every recorded decision, oldest first, or a refusal naming the bad line.

    Strict like the observation log's evidence reader and unlike its audit:
    this file is short, an operator writes to it deliberately, and there is no
    interrupted-crawl case that would leave a torn tail worth tolerating. A
    line that will not parse is damage, and skipping it would shrink the
    history silently.

    Raises:
        LogIntegrityError: a line does not parse as an event.
    """
    if not path.exists():
        return []
    events: list[SourceEvent] = []
    for number, line in enumerate(path.read_bytes().split(b"\n"), start=1):
        if not line.strip():
            continue
        try:
            events.append(SourceDomainReplaced.model_validate_json(line))
        except ValidationError as exc:
            raise LogIntegrityError(f"{path}:{number}: unreadable source event: {exc}") from exc
    return events


def events_for(path: Path, authority_id: str) -> list[SourceEvent]:
    """One authority's decision history, oldest first.

    Raises:
        LogIntegrityError: the log does not read to the end.
    """
    return [event for event in read_source_events(path) if event.authority_id == authority_id]


def domain_replacement(
    *,
    record: SourceRecord,
    to_domain: str,
    reason: str,
    changed_at: datetime,
    changed_by: str,
) -> SourceDomainReplaced:
    """The event describing this replacement, fingerprinting the record first.

    Built from the record as it stands, before anything is changed: the
    fingerprint has to identify the clearance being withdrawn, and a record
    read after the write would identify the one that replaced it.

    Raises:
        ParseError: the event does not validate — an empty reason or an
            unattributed change, both of which are operator input rather than
            engine state and must not reach the file.
    """
    try:
        return SourceDomainReplaced(
            authority_id=record.authority_id,
            from_domain=record.canonical_domain,
            to_domain=to_domain,
            reason=reason,
            changed_at=changed_at,
            changed_by=changed_by,
            previous_record_sha256=record_fingerprint(record),
        )
    except ValidationError as exc:
        raise ParseError(f"refusing to record this replacement: {exc}") from exc
