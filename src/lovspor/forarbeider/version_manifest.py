"""Build, read and verify a version manifest v3 from raw evidence only (#738, FC6).

:func:`build_version_manifest` takes the saved chapter pages, the attachment
inventory if there is one, and the raw bytes of anything else the inventory
names. It extracts and classifies the pages itself (FC3), so no extraction,
label or completeness flag is ever taken from the caller; it hashes every byte
string it is given and refuses one that does not hash to its name.

An attachment counts as captured only when its bytes are given, hash to the
listed digest and are a supported format. Hashing proves the bytes, never that
the list is whole: the inventory is the caller's observation, kept as given and
never promoted to verified, whatever its reader name, its pages or its length.
An absent inventory is no inventory, and an empty one is not a zero.

:func:`parse_version_manifest` reads stored bytes back and recomputes the
version id and both axes from the content, so a hand-edited verdict is refused.
:func:`verify_version_manifest` goes further: it rebuilds the manifest from the
evidence and requires the very same bytes.

Nothing is fetched, written, signed or published here.
"""

import hashlib
from collections.abc import Mapping
from typing import Literal, NamedTuple, Self

from pydantic import Field, ValidationError, model_validator

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.document_classify import EntryLabel, classify_document
from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.document_models import (
    DocumentExtraction,
    Gap,
    Part,
    Span,
    extraction_text,
    text_digest,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.version_completeness import (
    publication_completeness,
    source_completeness,
)
from lovspor.forarbeider.version_models import (
    MANIFEST_SCHEMA_VERSION,
    AttachmentState,
    Digest,
    Frozen,
    NonBlank,
    PageProvenance,
    Provenance,
    VersionAttachment,
    VersionAttachments,
    VersionContent,
    VersionDocument,
    VersionGap,
    VersionManifest,
    VersionPart,
    VersionSpan,
    canonical_text,
)

#: Leading bytes of the formats an attachment may be captured as.
_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
_DOCUMENT_FIELDS = (
    "document_id",
    "document_type",
    "session",
    "proposition_number",
    "source_document_id",
    "source_url",
    "title",
    "ministry",
)


class AttachmentListing(Frozen):
    """One attachment as an inventory lists it: where, on which page, and its claimed digest."""

    url: NonBlank
    listed_on: Digest
    raw_sha256: Digest | None


class AttachmentInventory(Frozen):
    """What a caller says an attachment reader listed, and the pages it read.

    Observations only. The listed attachments are checked against their bytes;
    the claim that the list is whole is never believed, so the manifest records
    the inventory as unverified discovery and states no attachment count.
    """

    record_type: Literal["forarbeider_attachment_inventory"] = "forarbeider_attachment_inventory"
    reader: NonBlank
    read_from: tuple[Digest, ...] = Field(min_length=1)
    attachments: tuple[AttachmentListing, ...]

    @model_validator(mode="after")
    def _listed_on_the_basis(self) -> Self:
        if any(item.listed_on not in self.read_from for item in self.attachments):
            raise ValueError("an attachment is listed on a page outside read_from")
        return self


class Evidence(NamedTuple):
    """Everything a version is built from: pages, the inventory, raw bytes by digest."""

    pages: tuple[SourcePage, ...]
    inventory: AttachmentInventory | None
    raw: Mapping[str, bytes]


def build_version_manifest(evidence: Evidence) -> VersionManifest:
    """The manifest of the version ``evidence`` shows, both axes computed.

    Raises:
        ForarbeiderRefusedError: ``manifest_invalid`` when a byte string does
            not hash to its digest; the page refusals of FC3 extraction.
    """
    raw = _hashed(evidence)
    extraction = extract_document(evidence.pages)
    classification = classify_document(extraction)
    labels = {label.entry_id: label for label in classification.entries}
    content = _content(extraction, labels, _attachments(evidence.inventory, raw))
    source = source_completeness(content)
    return VersionManifest(
        version_id=text_digest(canonical_text(content)),
        content=content,
        provenance=Provenance(
            extraction_sha256=text_digest(extraction_text(extraction)),
            classification_sha256=text_digest(canonical_text(classification)),
            pages=tuple(_page_provenance(part) for part in extraction.parts),
        ),
        source=source,
        publication=publication_completeness(content, source),
    )


def manifest_text(manifest: VersionManifest) -> str:
    """The canonical bytes of ``manifest``: sorted keys, two-space indent, one final newline."""
    return canonical_text(manifest)


def parse_version_manifest(raw: bytes) -> VersionManifest:
    """The manifest in ``raw``, its id and both axes recomputed from its content.

    Raises:
        ForarbeiderRefusedError: ``manifest_incompatible`` for another schema
            version; ``manifest_invalid`` for anything not canonical, not the
            schema, or whose id or verdict does not follow from its content.
    """
    payload = _loaded(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ForarbeiderRefusedError(RefusalReason.MANIFEST_INCOMPATIBLE, "manifest")
    try:
        manifest = VersionManifest.model_validate(payload)
    except ValidationError as error:
        raise _invalid("manifest") from error
    if manifest_text(manifest).encode() != raw:
        raise _invalid("manifest")
    _require_consistent(manifest)
    return manifest


def verify_version_manifest(raw: bytes, evidence: Evidence) -> VersionManifest:
    """The manifest in ``raw``, when ``evidence`` rebuilds exactly those bytes.

    Raises:
        ForarbeiderRefusedError: ``manifest_invalid`` when it does not.
    """
    manifest = parse_version_manifest(raw)
    if manifest_text(build_version_manifest(evidence)).encode() != raw:
        raise _invalid("manifest not rebuilt from the evidence")
    return manifest


def _invalid(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.MANIFEST_INVALID, detail)


def _require_consistent(manifest: VersionManifest) -> None:
    content = manifest.content
    if manifest.version_id != text_digest(canonical_text(content)):
        raise _invalid("version id")
    source = source_completeness(content)
    if manifest.source != source or manifest.publication != publication_completeness(
        content, source
    ):
        raise _invalid("completeness")


def _hashed(evidence: Evidence) -> dict[str, bytes]:
    """Every byte string given, by its own digest; one under another name is refused."""
    for digest, payload in evidence.raw.items():
        if hashlib.sha256(payload).hexdigest() != digest:
            raise _invalid("evidence digest")
    pages = {hashlib.sha256(page.payload).hexdigest(): page.payload for page in evidence.pages}
    return {**evidence.raw, **pages}


def _content(
    extraction: DocumentExtraction,
    labels: Mapping[str, EntryLabel],
    attachments: VersionAttachments | None,
) -> VersionContent:
    document = extraction.model_dump(include=set(_DOCUMENT_FIELDS))
    return VersionContent(
        document=VersionDocument.model_validate(document),
        chapters_listed=extraction.chapters_listed,
        parts=tuple(_part(part, labels) for part in extraction.parts),
        document_gaps=tuple(_gap(gap, labels[gap.id]) for gap in extraction.gaps),
        attachments=attachments,
    )


def _part(part: Part, labels: Mapping[str, EntryLabel]) -> VersionPart:
    return VersionPart(
        id=part.id,
        chapter_number=part.chapter_number,
        annex=part.annex,
        source_sha256=part.source_sha256,
        coverage=part.coverage,
        entries=tuple(
            _span(entry, labels[entry.id])
            if isinstance(entry, Span)
            else _gap(entry, labels[entry.id])
            for entry in part.entries
        ),
    )


def _span(span: Span, label: EntryLabel) -> VersionSpan:
    return VersionSpan(
        id=span.id,
        kind=span.kind,
        text_sha256=span.text_sha256,
        rights=label.rights,
        privacy=label.privacy,
    )


def _gap(gap: Gap, label: EntryLabel) -> VersionGap:
    return VersionGap(
        id=gap.id,
        code=gap.code,
        scope=gap.scope,
        locator=gap.locator,
        text_sha256=None if gap.text is None else text_digest(gap.text),
        rights=label.rights,
        privacy=label.privacy,
    )


def _page_provenance(part: Part) -> PageProvenance:
    return PageProvenance(
        part_id=part.id,
        url=part.source_page_url,
        raw_sha256=part.source_sha256,
        retrieved_at=part.retrieved_at,
    )


def _attachments(
    inventory: AttachmentInventory | None, raw: Mapping[str, bytes]
) -> VersionAttachments | None:
    if inventory is None:
        return None
    return VersionAttachments(
        reader=inventory.reader,
        read_from=tuple(sorted(set(inventory.read_from))),
        attachments=tuple(
            sorted(
                (_attachment(item, raw) for item in inventory.attachments),
                key=lambda item: (item.url, item.listed_on, item.raw_sha256 or ""),
            )
        ),
    )


def _attachment(item: AttachmentListing, raw: Mapping[str, bytes]) -> VersionAttachment:
    """What the bytes show: not captured, unverified, unsupported, or captured as a format."""
    media_type = None
    if item.raw_sha256 is None:
        state = AttachmentState.NOT_CAPTURED
    elif item.raw_sha256 not in raw:
        state = AttachmentState.UNVERIFIED
    else:
        media_type = _media_type(raw[item.raw_sha256])
        state = AttachmentState.UNSUPPORTED if media_type is None else AttachmentState.CAPTURED
    return VersionAttachment(
        url=item.url,
        listed_on=item.listed_on,
        raw_sha256=item.raw_sha256,
        media_type=media_type,
        state=state,
    )


def _media_type(payload: bytes) -> str | None:
    return next((kind for lead, kind in _SIGNATURES if payload.startswith(lead)), None)
