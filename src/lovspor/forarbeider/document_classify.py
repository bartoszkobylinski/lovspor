"""Rights and privacy candidates for every entry of an extraction (#738, FC3; architecture §3.4).

Kept apart from extraction: it reads a finished ``DocumentExtraction``, names
it by the digest of its written text, and changes nothing in it. Every label is
a prompt for assessment, never a legal conclusion: nothing here clears a span,
and ``DocumentClassification`` cannot state a clearance or release eligibility
(both fields have the single value ``False``). Policy-driven clearance is FC5
and FC7, on top of these labels.

Rights, from structure and fixed markers only, never from guessing who speaks:

- a gap, or a span of a part whose markup or boundary is unproved: ``unresolved``;
- a span of an annex chapter, or one stating a third-party marker:
  ``possible_third_party``;
- a span inside a quotation: ``quotation_requires_assessment``;
- any other span: ``official_text_candidate``.

Privacy: a span or gap text matching a personal-data pattern or a
special-category term is ``privacy_review_required``; a gap with no text is
``unresolved``; otherwise ``no_detector_hit``, which is detector silence and
not proof. A generic mention of ``høring`` is no signal at all.
"""

import hashlib
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from lovspor.forarbeider.document_models import (
    DocumentExtraction,
    Gap,
    GapCode,
    Part,
    Span,
    extraction_text,
)

CLASSIFIER_VERSION = "span-candidates/1"

_Text = Annotated[str, Field(pattern=r"\S")]

_PERSONAL_DATA = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+"
    r"|(?:\+47|\btlf\.?|\btelefon)[:\s]*\d(?:\s?\d){7}"
    r"|(?<!\d)\d{6}\s?\d{5}(?!\d)",
    re.IGNORECASE,
)
_SPECIAL_CATEGORY = re.compile(
    r"helseopplysning|diagnose|straffbar|straffedom|etnisk|religiøs|seksuell"
    r"|fagforening|biometri",
    re.IGNORECASE,
)
_THIRD_PARTY_MARKER = re.compile(r"©|copyright|gjengitt med tillatelse", re.IGNORECASE)
#: Part-scope gaps that leave the part's spans without a proved place in the source.
_UNPROVED_PART = frozenset(
    {
        GapCode.MALFORMED_MARKUP,
        GapCode.BOUNDARY_UNCONFIRMED,
        GapCode.COVERAGE_MISMATCH,
        GapCode.HIDDEN_CONTENT,
    }
)


class RightsCandidate(StrEnum):
    """What a span may be, for a later assessment. No value is a clearance."""

    OFFICIAL_TEXT_CANDIDATE = "official_text_candidate"
    QUOTATION_REQUIRES_ASSESSMENT = "quotation_requires_assessment"
    POSSIBLE_THIRD_PARTY = "possible_third_party"
    UNRESOLVED = "unresolved"


class PrivacyCandidate(StrEnum):
    """What the detectors said. ``no_detector_hit`` is silence, not proof."""

    NO_DETECTOR_HIT = "no_detector_hit"
    PRIVACY_REVIEW_REQUIRED = "privacy_review_required"
    UNRESOLVED = "unresolved"


class EntryLabel(BaseModel):
    """The candidates for one span or gap, with the signals that produced them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: _Text
    rights: RightsCandidate
    privacy: PrivacyCandidate
    signals: tuple[str, ...]


class DocumentClassification(BaseModel):
    """Labels for every entry of one extraction, in its order. Never a clearance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeid_span_classification"] = "forarbeid_span_classification"
    classifier_version: Literal["span-candidates/1"] = "span-candidates/1"
    document_id: _Text
    extraction_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cleared: Literal[False] = False
    automatic_release_eligible: Literal[False] = False
    entries: tuple[EntryLabel, ...]


def classify_document(extraction: DocumentExtraction) -> DocumentClassification:
    """Candidates for every span and gap of ``extraction``, depending on it alone."""
    labels = [label for part in extraction.parts for label in _part_labels(part)]
    labels += [_gap_label(gap) for gap in extraction.gaps]
    digest = hashlib.sha256(extraction_text(extraction).encode()).hexdigest()
    return DocumentClassification(
        document_id=extraction.document_id, extraction_sha256=digest, entries=tuple(labels)
    )


def _part_labels(part: Part) -> list[EntryLabel]:
    unproved = any(
        isinstance(e, Gap) and e.scope == "part" and e.code in _UNPROVED_PART for e in part.entries
    )
    return [
        _span_label(entry, unproved=unproved, annex=part.annex)
        if isinstance(entry, Span)
        else _gap_label(entry)
        for entry in part.entries
    ]


def _span_label(span: Span, *, unproved: bool, annex: bool) -> EntryLabel:
    signals: list[str] = []
    if unproved:
        signals.append("part_unproved")
    if annex:
        signals.append("annex_part")
    if _THIRD_PARTY_MARKER.search(span.text):
        signals.append("third_party_marker")
    if span.quote_group is not None:
        signals.append("quotation")
    privacy, found = _privacy(span.text)
    return EntryLabel(
        entry_id=span.id, rights=_rights(signals), privacy=privacy, signals=(*signals, *found)
    )


def _rights(signals: list[str]) -> RightsCandidate:
    if "part_unproved" in signals:
        return RightsCandidate.UNRESOLVED
    if "annex_part" in signals or "third_party_marker" in signals:
        return RightsCandidate.POSSIBLE_THIRD_PARTY
    if "quotation" in signals:
        return RightsCandidate.QUOTATION_REQUIRES_ASSESSMENT
    return RightsCandidate.OFFICIAL_TEXT_CANDIDATE


def _gap_label(gap: Gap) -> EntryLabel:
    found: list[str] = []
    if gap.text is None:
        privacy = PrivacyCandidate.UNRESOLVED
    else:
        privacy, found = _privacy(gap.text)
    return EntryLabel(
        entry_id=gap.id,
        rights=RightsCandidate.UNRESOLVED,
        privacy=privacy,
        signals=(f"gap:{gap.code.value}", *found),
    )


def _privacy(text: str) -> tuple[PrivacyCandidate, list[str]]:
    found = [
        name
        for name, pattern in (
            ("personal_data_pattern", _PERSONAL_DATA),
            ("special_category_term", _SPECIAL_CATEGORY),
        )
        if pattern.search(text)
    ]
    state = PrivacyCandidate.PRIVACY_REVIEW_REQUIRED if found else PrivacyCandidate.NO_DETECTOR_HIT
    return state, found
