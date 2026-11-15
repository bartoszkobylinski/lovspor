"""Build, read and verify a version manifest v3 from raw evidence only (#738, FC6, FC6b).

:func:`build_version_manifest` takes raw bytes and nothing else: the saved
chapter pages, the landing page if it was saved, and the captured attachment
responses. It extracts and classifies the pages itself (FC3) and discovers the
attachments itself with the trusted reader
(:mod:`lovspor.forarbeider.attachment_discovery`), so no extraction, label,
inventory or completeness flag is ever taken from the caller.

A discovered attachment counts as captured only when bytes were given for its
URL, hashed here, and are a supported format. A capture for a URL discovery did
not find is not an attachment; two different captures of one URL are a
conflict, not a choice.

:func:`parse_version_manifest` reads stored bytes back and recomputes the
version id and both axes from the content, so a hand-edited verdict is refused.
It cannot see the pages, so it cannot tell a consistent forgery from a real
discovery: :func:`verify_version_manifest` rebuilds the manifest from the
evidence and requires the very same bytes, and only that proves a verified
inventory.

Nothing is fetched, written, signed or published here.
"""

import hashlib
from collections.abc import Mapping
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.attachment_discovery import (
    AttachmentDiscovery,
    DiscoveredAttachment,
    canonical_url,
    discover_attachments,
)
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
    PageProvenance,
    Provenance,
    SupportingProvenance,
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


class Evidence(NamedTuple):
    """Everything a version is built from, as raw responses: no list, label or flag."""

    pages: tuple[SourcePage, ...]
    landing: SourcePage | None
    captures: tuple[SourcePage, ...]


def build_version_manifest(evidence: Evidence) -> VersionManifest:
    """The manifest of the version ``evidence`` shows, both axes computed.

    Raises:
        ForarbeiderRefusedError: the page refusals of FC3 extraction.
    """
    extraction = extract_document(evidence.pages)
    classification = classify_document(extraction)
    labels = {label.entry_id: label for label in classification.entries}
    pages = {_sha(page.payload): page for page in evidence.pages}
    discovery = discover_attachments(extraction, pages, evidence.landing)
    content = _content(extraction, labels, _attachments(discovery, evidence.captures))
    source = source_completeness(content)
    return VersionManifest(
        version_id=text_digest(canonical_text(content)),
        content=content,
        provenance=Provenance(
            extraction_sha256=text_digest(extraction_text(extraction)),
            classification_sha256=text_digest(canonical_text(classification)),
            pages=tuple(_page_provenance(part) for part in extraction.parts),
            supporting=_supporting(evidence),
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


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _content(
    extraction: DocumentExtraction,
    labels: Mapping[str, EntryLabel],
    attachments: VersionAttachments,
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


def _supporting(evidence: Evidence) -> tuple[SupportingProvenance, ...]:
    landing = [("landing", evidence.landing)] if evidence.landing is not None else []
    given = [*landing, *(("capture", page) for page in evidence.captures)]
    records = {
        SupportingProvenance.model_validate(
            {
                "role": role,
                "url": page.url,
                "raw_sha256": _sha(page.payload),
                "retrieved_at": page.retrieved_at,
            }
        )
        for role, page in given
    }
    return tuple(sorted(records, key=lambda item: (item.role, item.url, item.raw_sha256)))


def _attachments(
    discovery: AttachmentDiscovery, captures: tuple[SourcePage, ...]
) -> VersionAttachments:
    """The discovered inventory, each attachment with what its captured bytes show."""
    by_url: dict[str, dict[str, bytes]] = {}
    for page in captures:
        by_url.setdefault(canonical_url(page.url, page.url), {})[_sha(page.payload)] = page.payload
    return VersionAttachments(
        discovery="verified" if discovery.exhaustive else "unverified",
        pages_required=discovery.pages_required,
        pages_scanned=discovery.pages_scanned,
        attachments=tuple(
            _attachment(item, by_url.get(item.url, {})) for item in discovery.attachments
        ),
        renditions=discovery.renditions,
        gaps=discovery.gaps,
    )


def _attachment(item: DiscoveredAttachment, captured: Mapping[str, bytes]) -> VersionAttachment:
    """What the bytes given for the URL show: none, a conflict, unsupported, or captured."""
    media_type, digest = None, None
    if not captured:
        state = AttachmentState.NOT_CAPTURED
    elif len(captured) > 1:
        state = AttachmentState.CONFLICTING
    else:
        ((digest, payload),) = captured.items()
        media_type = _media_type(payload)
        state = AttachmentState.UNSUPPORTED if media_type is None else AttachmentState.CAPTURED
    return VersionAttachment(
        url=item.url,
        kind=item.kind,
        found_at=item.found_at,
        raw_sha256=digest,
        media_type=media_type,
        state=state,
    )


def _media_type(payload: bytes) -> str | None:
    return next((kind for lead, kind in _SIGNATURES if payload.startswith(lead)), None)
