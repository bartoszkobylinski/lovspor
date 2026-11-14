"""Version manifest v3: one observed state of a whole proposition (#738, FC6; §2.2, §2.3).

Private ingestion only. A ``VersionManifest`` names the version it describes by
``version_id``, the SHA-256 of its canonical ``content``: the document identity,
the raw digest of every page and attachment, the extractor and classifier
versions, the ordered parts with each entry's digest and candidates, and the
gaps. Nothing inside ``content`` names a version id, a retrieval time or a
release, so the same raw bytes and versions give the same id whenever they are
read; where and when the bytes were read is ``provenance``, outside the hash.

No text is held here, only digests: the text stays in the private extraction.

Completeness has two axes, each ``complete``, ``incomplete`` or ``unknown`` and
each computed from ``content`` alone, never typed by a person
(:mod:`lovspor.forarbeider.version_completeness`). ``publication_complete``
requires ``source_complete``; the model refuses any other pairing. Nothing here
clears anything: ``cleared`` and ``automatic_release_eligible`` have the single
value ``False``.

The corpus manifests of versions 1 and 2 (:mod:`lovspor.forarbeider.corpus_models`)
stay as they are and are still read; :mod:`lovspor.forarbeider.version_reader`
lifts their members to summaries that are never complete.
"""

import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lovspor.forarbeider.document_classify import (
    PrivacyCandidate,
    RightsCandidate,
)
from lovspor.forarbeider.document_models import (
    Coverage,
    GapCode,
    GapScope,
    SpanKind,
)
from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.models import JSON_LAYOUT, Chapter

MANIFEST_SCHEMA_VERSION = 3

NonBlank = Annotated[str, Field(pattern=r"\S")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- hashed content ----------------------------------------------------------------


class VersionDocument(Frozen):
    """The document's stable identity: the same across every version."""

    document_id: NonBlank
    document_type: DocumentType
    session: str = Field(pattern=r"^\d{4}-\d{4}$")
    proposition_number: int = Field(ge=1)
    source_document_id: int = Field(ge=1)
    source_url: NonBlank
    title: NonBlank
    ministry: NonBlank | None


class VersionSpan(Frozen):
    """A span by digest, with the candidates FC3 gave it (never a clearance)."""

    entry: Literal["span"] = "span"
    id: NonBlank
    kind: SpanKind
    text_sha256: Digest
    rights: RightsCandidate
    privacy: PrivacyCandidate


class VersionGap(Frozen):
    """A gap by code and place; its text, when it held any, by digest only."""

    entry: Literal["gap"] = "gap"
    id: NonBlank
    code: GapCode
    scope: GapScope
    locator: str | None
    text_sha256: Digest | None
    rights: RightsCandidate
    privacy: PrivacyCandidate


VersionEntry = Annotated[VersionSpan | VersionGap, Field(discriminator="entry")]


class VersionPart(Frozen):
    """One chapter page: its raw digest, its coverage and its entries in source order."""

    id: NonBlank
    chapter_number: int = Field(ge=1)
    annex: bool
    source_sha256: Digest
    coverage: Coverage
    entries: tuple[VersionEntry, ...]


class AttachmentState(StrEnum):
    """What the raw evidence shows of one listed attachment."""

    CAPTURED = "captured"
    NOT_CAPTURED = "not_captured"
    UNVERIFIED = "unverified"
    UNSUPPORTED = "unsupported"


class VersionAttachment(Frozen):
    """One listed attachment: where it was listed, its raw digest, what its bytes are."""

    url: NonBlank
    listed_on: Digest
    raw_sha256: Digest | None
    media_type: str | None
    state: AttachmentState


class VersionAttachments(Frozen):
    """An attachment inventory as the caller observed it, never proof that it is whole.

    The listed attachments, their URLs, digests and capture states are kept as
    observations. Nothing given here can show that discovery was exhaustive: a
    reader name is a label, page digests show which bytes were read, not that
    nothing was missed, and an empty list is not a zero. ``discovery`` is
    therefore always ``unverified``, so no attachment count is ever stated.
    A trusted, versioned discovery reader (FC6b) is the only thing that may add
    another value.
    """

    reader: NonBlank
    read_from: tuple[Digest, ...] = Field(min_length=1)
    discovery: Literal["unverified"] = "unverified"
    attachments: tuple[VersionAttachment, ...]


class VersionContent(Frozen):
    """Everything the version id covers, in a fixed order. No version id, time or release."""

    document: VersionDocument
    extractor_version: Literal["regjeringen-span/1"] = "regjeringen-span/1"
    classifier_version: Literal["span-candidates/1"] = "span-candidates/1"
    chapters_listed: tuple[Chapter, ...]
    parts: tuple[VersionPart, ...]
    document_gaps: tuple[VersionGap, ...]
    attachments: VersionAttachments | None


# --- outside the hash --------------------------------------------------------------


class PageProvenance(Frozen):
    """Where and when one part's page was read."""

    part_id: NonBlank
    url: NonBlank
    raw_sha256: Digest
    retrieved_at: AwareDatetime


class Provenance(Frozen):
    """The private records this version was built from, by digest."""

    extraction_sha256: Digest
    classification_sha256: Digest
    pages: tuple[PageProvenance, ...]


class Effect(StrEnum):
    """What a reason does to its axis: confirm it incomplete, or leave it unknown."""

    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class Reason(Frozen):
    """Why an axis is not complete, and where: the part and entry, when it has one."""

    code: NonBlank
    effect: Effect
    part_id: str | None = None
    entry_id: str | None = None
    detail: str | None = None


class SourceState(StrEnum):
    SOURCE_COMPLETE = "source_complete"
    SOURCE_INCOMPLETE = "source_incomplete"
    UNKNOWN = "unknown"


class PublicationState(StrEnum):
    PUBLICATION_COMPLETE = "publication_complete"
    PUBLICATION_INCOMPLETE = "publication_incomplete"
    UNKNOWN = "unknown"


def state_of(reasons: tuple[Reason, ...]) -> Effect | None:
    """Incomplete when any reason confirms it; unknown when any is open; else none."""
    effects = {reason.effect for reason in reasons}
    if Effect.INCOMPLETE in effects:
        return Effect.INCOMPLETE
    return Effect.UNKNOWN if effects else None


SOURCE_STATES = {
    None: SourceState.SOURCE_COMPLETE,
    Effect.INCOMPLETE: SourceState.SOURCE_INCOMPLETE,
    Effect.UNKNOWN: SourceState.UNKNOWN,
}
PUBLICATION_STATES = {
    None: PublicationState.PUBLICATION_COMPLETE,
    Effect.INCOMPLETE: PublicationState.PUBLICATION_INCOMPLETE,
    Effect.UNKNOWN: PublicationState.UNKNOWN,
}


class SourceCompleteness(Frozen):
    """Did we capture and faithfully extract everything the source publishes?

    A count is ``None`` when its input is missing: unknown, never zero.
    """

    state: SourceState
    parts_expected: int | None = Field(ge=0)
    parts_extracted: int = Field(ge=0)
    attachments_expected: int | None = Field(ge=0)
    attachments_captured: int | None = Field(ge=0)
    extraction_gaps: int = Field(ge=0)
    reasons: tuple[Reason, ...]

    @model_validator(mode="after")
    def _state_follows_reasons(self) -> Self:
        if self.state is not SOURCE_STATES[state_of(self.reasons)]:
            raise ValueError("source state does not follow from its reasons")
        return self


class PublicationCompleteness(Frozen):
    """Of what was extracted, how much may be published? Computed apart from the source axis."""

    state: PublicationState
    spans_total: int = Field(ge=0)
    spans_publishable: int = Field(ge=0)
    release_gaps: int = Field(ge=0)
    attachments_publishable: int | None = Field(ge=0)
    reasons: tuple[Reason, ...]

    @model_validator(mode="after")
    def _state_follows_reasons(self) -> Self:
        if self.state is not PUBLICATION_STATES[state_of(self.reasons)]:
            raise ValueError("publication state does not follow from its reasons")
        return self


class VersionManifest(Frozen):
    """One version of one proposition, its two completeness axes, and nothing cleared."""

    schema_version: Literal[3] = 3
    record_type: Literal["forarbeider_version_manifest"] = "forarbeider_version_manifest"
    release_state: Literal["private_ingestion"] = "private_ingestion"
    version_id: Digest
    content: VersionContent
    provenance: Provenance
    source: SourceCompleteness
    publication: PublicationCompleteness
    cleared: Literal[False] = False
    automatic_release_eligible: Literal[False] = False

    @model_validator(mode="after")
    def _publication_needs_source(self) -> Self:
        published = self.publication.state is PublicationState.PUBLICATION_COMPLETE
        if published and self.source.state is not SourceState.SOURCE_COMPLETE:
            raise ValueError("publication_complete requires source_complete")
        if published and self.publication.release_gaps:
            raise ValueError("publication_complete requires zero release gaps")
        _require_unverified_discovery_states_no_count(self.source)
        return self


def _require_unverified_discovery_states_no_count(source: SourceCompleteness) -> None:
    """No attachment discovery is trusted yet: no count, and so never source_complete."""
    counted = (source.attachments_expected, source.attachments_captured) != (None, None)
    if counted or source.state is SourceState.SOURCE_COMPLETE:
        raise ValueError("attachment discovery is unverified: no count, no source_complete")


def canonical_text(model: BaseModel) -> str:
    """Sorted keys, two-space indent, UTF-8, one final newline."""
    return json.dumps(model.model_dump(mode="json"), **JSON_LAYOUT) + "\n"
