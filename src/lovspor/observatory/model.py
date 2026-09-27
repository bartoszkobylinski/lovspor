"""Capture model for the observatory (ADR-0010 §2, §3).

An observation event is one of three record kinds, discriminated by ``kind``:

* ``ArtifactObservation`` — a fetch that returned content;
* ``FetchFailure`` — a fetch that did not. A failure is an observation, not a
  gap: it records that the endpoint was *not* retrievable at that time, which
  is itself evidence and which Design Principle §15 requires to stay visible;
* ``Tombstone`` — an appended record that a stored blob was removed.

Two more kinds correct a record without rewriting it (ADR-0015): a
``RecordTombstone`` retracts one filed record's claim, and a
``RefiledObservation`` files the same observation again with the corrected
field and the correction's provenance. Neither is an observation, and neither
touches a blob; :mod:`lovspor.observatory.corrections` says how readers apply
them.

Raw bytes are not carried in the record. The record holds their SHA-256 and
the blob store holds the bytes (:mod:`lovspor.observatory.log`), so the same
bytes observed twice cost one blob and two records, and a record stays small
enough to keep the log readable as a text file.

Legal fields — document class, legal identifiers, adoption or entry-into-force
dates, relationships — are absent by design rather than nullable-and-empty.
ADR-0010 defers classification and parsing entirely, so nothing could populate
them honestly today; they belong to the derived layer that Design Principle
§11 keeps separate from source capture.

Every model here forbids unknown fields. These records are persisted evidence:
dropping a field a newer writer added would silently discard part of what was
observed, and the log's own contract is to fail on a shape it does not
understand rather than to read past it. That is the opposite trade from the
corpus manifest, which tolerates unknown keys so older readers keep working —
there the reader is a third party, here it is this engine reading its own
archive.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lovspor.observatory.fields import NonBlankStr

AuthorityType = Literal["kommune", "fylkeskommune"]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def require_utc(value: datetime) -> datetime:
    """Reject a naive or non-UTC timestamp.

    ``ObservedAt`` is an axis, not a convenience field: a naive timestamp is
    ambiguous the moment the runner's timezone changes, and a local-offset one
    silently reorders records that a later reader will read as a sequence.
    Both are refused rather than coerced — coercion would invent a moment.
    """
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware UTC, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"timestamp must be UTC, got offset {value.utcoffset()}")
    return value


class RetrievalProvenance(BaseModel):
    """How an observation was obtained, including the politeness in force.

    ADR-0010 §2 makes retrieval provenance mandatory, and §4 makes the crawl
    constraints part of what is recorded rather than part of a config file
    nobody reads later: an audit asking whether a source was fetched politely
    must be answerable from the record itself, not from the deployment that
    happened to be running that day.

    ``channel`` and ``discovery_method`` are different questions and both are
    required. The channel is *how the bytes arrived* — ``http``,
    ``bulk_dataset``, ``file_api``; the discovery method is *how the URL was
    found* — a sitemap, a feed, a link on a listing page. A record that
    conflates them cannot answer either.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    adapter: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    discovery_method: str = Field(min_length=1)
    user_agent: str = Field(min_length=1)
    rate_limit_seconds: float = Field(gt=0)
    #: The hops between the URL that was asked for and the one that answered,
    #: in order, empty when nothing redirected. The observation is filed under
    #: the *requested* URL — that is the question the archive is asked — so
    #: without this the record could not say the bytes came from somewhere
    #: else, and eight URLs redirecting to one login page would be
    #: indistinguishable from eight pages that happen to be identical
    #: (issue #211).
    redirect_chain: tuple[str, ...] = ()


class _ObservationBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    observed_at: datetime
    provenance: RetrievalProvenance

    @field_validator("observed_at")
    @classmethod
    def _utc_observed_at(cls, value: datetime) -> datetime:
        return require_utc(value)


class ArtifactObservation(_ObservationBase):
    """Specific bytes were retrievable from a recorded endpoint at a time.

    That is the whole claim (ADR-0010 §3). It is not a claim that the bytes
    are a regulation, that they were legally published, or that they are
    current — every one of those is a classification decision this ADR defers.
    """

    kind: Literal["artifact"] = "artifact"
    sha256: str = Field(pattern=_SHA256_PATTERN)
    content_type: str = Field(min_length=1)
    http_status: int
    http_headers: dict[str, str] = Field(default_factory=dict)


class FetchFailure(_ObservationBase):
    """A fetch that returned no content, recorded as an event in its own right.

    ``outcome`` names the failure class as the fetcher saw it (a timeout, a
    connection error, an HTTP error); ``http_status`` is present only when the
    server answered at all. Carrying no bytes, a failure has no SHA-256 —
    which is why the two kinds are separate types rather than one type with
    optional bytes that callers would have to remember to check.

    **A followed redirect is filed here too, and it is not a failure.** The hop
    returned no bytes, which is what this type means, but the fetcher went
    straight on to ask the target and the document arrives under its own
    record. Three quarters of the archive's ``fetch_failure`` records are such
    hops, so counting this ``kind`` is how a reader gets a failure rate four
    times the real one (issue #188). Whether an outcome lost a document is
    :func:`~lovspor.observatory.outcomes.lost_the_document`, and it is that
    function rather than this type that any count should ask — the name is
    kept because the records already written mean what they meant, and a
    rename would split the archive into two vocabularies at a commit boundary
    without making one record truer.
    """

    kind: Literal["fetch_failure"] = "fetch_failure"
    outcome: str = Field(min_length=1)
    http_status: int | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)


class Tombstone(BaseModel):
    """A stored blob was removed, recorded by appending rather than deleting.

    ADR-0010 §7 permits removing raw bytes on legal, privacy or comparable
    grounds, but never by rewriting history: the original observation and its
    hash stay in the log and this record explains the gap. A blob missing
    *without* a tombstone is indistinguishable from silent mutation, and
    :func:`lovspor.observatory.log.verify_snapshot` reports it as such.

    ``basis`` and ``authorised_by`` are free text on purpose. Which grounds a
    tombstone must distinguish, and who may authorise each, is an open
    question in ADR-0010; a closed vocabulary here would answer it by
    implementation instead of by decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tombstone"] = "tombstone"
    sha256: str = Field(pattern=_SHA256_PATTERN)
    removed_at: datetime
    basis: NonBlankStr
    authorised_by: NonBlankStr

    @field_validator("removed_at")
    @classmethod
    def _utc_removed_at(cls, value: datetime) -> datetime:
        return require_utc(value)


#: The only field ADR-0015 lets a correction change. Widening it is a decision,
#: not an edit: every other field is evidence of the fetch itself.
CorrectableField = Literal["authority_id"]

Observation = Annotated[ArtifactObservation | FetchFailure, Field(discriminator="kind")]


class RecordTombstone(BaseModel):
    """One filed record's claim no longer stands as filed (ADR-0015 §3).

    Not the blob :class:`Tombstone`: that one means *these bytes were removed*
    and is enforced as such. This one removes nothing — the original line stays
    in the log byte for byte, and its blob stays on disk. ``retracts`` names
    the original by :func:`record_key`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["record_tombstone"] = "record_tombstone"
    retracts: str = Field(pattern=_SHA256_PATTERN)
    correction_id: NonBlankStr
    reason: NonBlankStr
    corrected_by: NonBlankStr
    corrected_at: datetime

    @field_validator("corrected_at")
    @classmethod
    def _utc_corrected_at(cls, value: datetime) -> datetime:
        return require_utc(value)


class Correction(BaseModel):
    """Who corrected which record, when, why, and what the field said before."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supersedes: str = Field(pattern=_SHA256_PATTERN)
    correction_id: NonBlankStr
    corrected_fields: tuple[CorrectableField, ...] = Field(min_length=1)
    previous_values: dict[CorrectableField, NonBlankStr]
    reason: NonBlankStr
    corrected_by: NonBlankStr
    corrected_at: datetime

    @field_validator("corrected_at")
    @classmethod
    def _utc_corrected_at(cls, value: datetime) -> datetime:
        return require_utc(value)

    @model_validator(mode="after")
    def _previous_values_match(self) -> "Correction":
        if set(self.previous_values) != set(self.corrected_fields):
            raise ValueError("previous_values must name exactly the corrected_fields")
        return self


class RefiledObservation(BaseModel):
    """The corrected claim of an earlier record, filed with its provenance (§4).

    ``observation`` is the original with only ``corrected_fields`` changed —
    same ``observed_at``, URL, hash and retrieval provenance, because the fetch
    happened once. It is a distinct kind, not a late ``artifact``, so nothing
    reads it as a fetch made at ``corrected_at``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["refiled_observation"] = "refiled_observation"
    observation: Observation
    correction: Correction


ObservationRecord = Annotated[
    ArtifactObservation | FetchFailure | Tombstone | RecordTombstone | RefiledObservation,
    Field(discriminator="kind"),
]


def record_to_json_line(record: ObservationRecord) -> str:
    """Serialise one record to a single deterministic JSON line.

    Keys sorted, no insignificant whitespace, non-ASCII preserved literally
    (Norwegian text is the common case), no trailing newline — the log writer
    adds the separator. Same record in, byte-identical line out: the log is
    evidence, so two runs that observed the same thing must not differ in
    their serialisation of it.
    """
    data = record.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def record_key(line: bytes) -> str:
    """The stable key of a record: SHA-256 of its log line as stored (ADR-0015 §2).

    The line's own bytes without its terminator, never a re-serialisation of
    the parsed record: records written before a field existed re-serialise
    with it added, and a key computed that way would name no line in the log.
    The terminator is not part of the claim, so ``\n`` and ``\r\n`` name the
    same line; nothing else is normalised. The writer only ever emits ``\n``.
    """
    if line.endswith(b"\n"):
        line = line[:-1].removesuffix(b"\r")
    return hashlib.sha256(line).hexdigest()
