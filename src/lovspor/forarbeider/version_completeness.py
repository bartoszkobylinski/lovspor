"""Both completeness axes of one version, computed from its content alone (#738, FC6; §2.3).

Nothing here takes a flag from a person: every reason is read off the parts,
gaps, coverage and attachment evidence a ``VersionContent`` records, so the
same content always gives the same verdict, and a stored verdict can be checked
by computing it again.

A reason either confirms an axis incomplete or leaves it unknown. Incomplete
wins over unknown; an axis with no reason is complete. A missing input (no
chapter navigation, no attachment inventory, an inventory whose basis was not
verified) is unknown and states no count, never zero.

**Source**: every listed chapter is a part, every part's boundary and markup
are proved, its coverage is exact, no gap was recorded, and every listed
attachment was captured from bytes that hash to its digest.

**Publication**: never complete merely because the source is. It needs the
source complete, no release gap, and clearance evidence for every span and
attachment and for the publication constraints. FC3 labels are candidates, not
clearance, and no clearance evidence exists before FC5 and FC7: until then every
span lacks it, and the axis is ``unknown`` at best.
"""

from collections.abc import Iterator

from lovspor.forarbeider.document_classify import PrivacyCandidate, RightsCandidate
from lovspor.forarbeider.document_models import GapCode
from lovspor.forarbeider.version_models import (
    PUBLICATION_STATES,
    SOURCE_STATES,
    AttachmentInventoryState,
    AttachmentState,
    Effect,
    PublicationCompleteness,
    Reason,
    SourceCompleteness,
    SourceState,
    VersionAttachments,
    VersionContent,
    VersionGap,
    VersionSpan,
    state_of,
)

#: Gaps that leave the evidence open rather than confirm something missing.
_OPEN_GAPS = frozenset(
    {
        GapCode.NAVIGATION_DISAGREES,
        GapCode.AMBIGUOUS_REGION,
        GapCode.MALFORMED_MARKUP,
        GapCode.BOUNDARY_UNCONFIRMED,
    }
)
_ATTACHMENT_REASONS = {
    AttachmentState.NOT_CAPTURED: ("attachment_not_captured", Effect.INCOMPLETE),
    AttachmentState.UNVERIFIED: ("attachment_unverified", Effect.UNKNOWN),
    AttachmentState.UNSUPPORTED: ("attachment_unsupported", Effect.INCOMPLETE),
}
_RIGHTS_GAPS = {
    RightsCandidate.QUOTATION_REQUIRES_ASSESSMENT: "rights_unresolved",
    RightsCandidate.POSSIBLE_THIRD_PARTY: "third_party_work",
    RightsCandidate.UNRESOLVED: "rights_unresolved",
}
_SOURCE_GATE = {
    SourceState.SOURCE_INCOMPLETE: Effect.INCOMPLETE,
    SourceState.UNKNOWN: Effect.UNKNOWN,
}


def source_completeness(content: VersionContent) -> SourceCompleteness:
    """The source axis of ``content``, with every reason and the part it concerns."""
    reasons = (
        *_chapter_reasons(content),
        *_gap_reasons(content),
        *_coverage_reasons(content),
        *_attachment_reasons(content.attachments),
    )
    verified = _verified(content.attachments)
    return SourceCompleteness(
        state=SOURCE_STATES[state_of(reasons)],
        parts_expected=len(content.chapters_listed) or None,
        parts_extracted=len(content.parts),
        attachments_expected=None if verified is None else len(verified.attachments),
        attachments_captured=None if verified is None else _captured(verified),
        extraction_gaps=sum(1 for _ in _all_gaps(content)),
        reasons=reasons,
    )


def publication_completeness(
    content: VersionContent, source: SourceCompleteness
) -> PublicationCompleteness:
    """The publication axis: it reads the source axis's verdict, never the reverse."""
    entries = tuple(_entry_reasons(content))
    reasons = (
        *_source_gate(source),
        Reason(code="publication_constraints_absent", effect=Effect.UNKNOWN),
        *entries,
        *_attachment_publication(content.attachments),
    )
    verified = _verified(content.attachments)
    return PublicationCompleteness(
        state=PUBLICATION_STATES[state_of(reasons)],
        spans_total=sum(1 for _ in _spans(content)),
        spans_publishable=0,
        release_gaps=sum(1 for reason in entries if reason.effect is Effect.INCOMPLETE),
        attachments_publishable=None if verified is None else 0,
        reasons=reasons,
    )


# --- source ------------------------------------------------------------------------


def _chapter_reasons(content: VersionContent) -> Iterator[Reason]:
    """Every listed chapter is a part, and every part is a listed chapter."""
    if not content.chapters_listed:
        yield Reason(code="chapter_inventory_missing", effect=Effect.UNKNOWN)
    listed = {chapter.chapter_number for chapter in content.chapters_listed}
    present = {part.chapter_number: part.id for part in content.parts}
    for number in sorted(listed - present.keys()):
        yield Reason(code="chapter_missing", effect=Effect.INCOMPLETE, detail=f"ch{number}")
    for number in sorted(present.keys() - listed):
        yield Reason(code="chapter_unlisted", effect=Effect.INCOMPLETE, part_id=present[number])


def _all_gaps(content: VersionContent) -> Iterator[tuple[str | None, VersionGap]]:
    yield from ((None, gap) for gap in content.document_gaps)
    for part in content.parts:
        yield from ((part.id, entry) for entry in part.entries if isinstance(entry, VersionGap))


def _gap_reasons(content: VersionContent) -> Iterator[Reason]:
    for part_id, gap in _all_gaps(content):
        effect = Effect.UNKNOWN if gap.code in _OPEN_GAPS else Effect.INCOMPLETE
        yield Reason(
            code=gap.code.value,
            effect=effect,
            part_id=part_id,
            entry_id=gap.id,
            detail=gap.locator,
        )


def _coverage_reasons(content: VersionContent) -> Iterator[Reason]:
    for part in content.parts:
        if not part.coverage.exact:
            yield Reason(code="coverage_not_exact", effect=Effect.INCOMPLETE, part_id=part.id)


def _verified(attachments: VersionAttachments | None) -> VersionAttachments | None:
    """The inventory, when its basis was verified; otherwise no count can be stated."""
    if attachments is None or attachments.basis is not AttachmentInventoryState.VERIFIED:
        return None
    return attachments


def _captured(attachments: VersionAttachments) -> int:
    return sum(1 for item in attachments.attachments if item.state is AttachmentState.CAPTURED)


def _attachment_reasons(attachments: VersionAttachments | None) -> Iterator[Reason]:
    if attachments is None:
        yield Reason(code="attachment_inventory_missing", effect=Effect.UNKNOWN)
        return
    if attachments.basis is not AttachmentInventoryState.VERIFIED:
        yield Reason(code="attachment_inventory_unverified", effect=Effect.UNKNOWN)
    for item in attachments.attachments:
        if item.state in _ATTACHMENT_REASONS:
            code, effect = _ATTACHMENT_REASONS[item.state]
            yield Reason(code=code, effect=effect, detail=item.url)


# --- publication -------------------------------------------------------------------


def _source_gate(source: SourceCompleteness) -> Iterator[Reason]:
    if source.state in _SOURCE_GATE:
        yield Reason(code="source_not_complete", effect=_SOURCE_GATE[source.state])


def _spans(content: VersionContent) -> Iterator[VersionSpan]:
    for part in content.parts:
        yield from (entry for entry in part.entries if isinstance(entry, VersionSpan))


def _entries(content: VersionContent) -> Iterator[tuple[str | None, VersionSpan | VersionGap]]:
    for part in content.parts:
        yield from ((part.id, entry) for entry in part.entries)
    yield from ((None, gap) for gap in content.document_gaps)


def _entry_reasons(content: VersionContent) -> Iterator[Reason]:
    """Each entry's release gaps; an entry with none still lacks clearance evidence."""
    for part_id, entry in _entries(content):
        codes = []
        if entry.rights in _RIGHTS_GAPS:
            codes.append(_RIGHTS_GAPS[entry.rights])
        if entry.privacy is not PrivacyCandidate.NO_DETECTOR_HIT:
            codes.append("privacy_unresolved")
        effect = Effect.INCOMPLETE if codes else Effect.UNKNOWN
        for code in codes or ["clearance_evidence_absent"]:
            yield Reason(code=code, effect=effect, part_id=part_id, entry_id=entry.id)


def _attachment_publication(attachments: VersionAttachments | None) -> Iterator[Reason]:
    if attachments is None:
        yield Reason(code="attachment_inventory_missing", effect=Effect.UNKNOWN)
        return
    for item in attachments.attachments:
        captured = item.state is AttachmentState.CAPTURED
        yield Reason(
            code="attachment_clearance_absent" if captured else "attachment_unpublishable",
            effect=Effect.UNKNOWN if captured else Effect.INCOMPLETE,
            detail=item.url,
        )
