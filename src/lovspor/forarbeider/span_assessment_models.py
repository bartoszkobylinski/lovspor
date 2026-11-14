"""The FC5 span assessment record: rights class and privacy state per entry (#738; §3.4, §3.5).

Private ingestion only. An assessment states, for every span and gap of one
version, which rights class the source structure and its stated attribution
support, and what the privacy detectors found, each with the rule, the
reasons and the entries that are its evidence. It is a prompt for the policy
(FC7) and for review, never a legal opinion, a clearance or a release
decision: ``cleared``, ``automatic_release_eligible`` and ``legal_opinion``
have the single value ``False``.

Confidence is never a probability. ``evidence`` names what the class rests on
(markup, a stated attribution, a textual marker, the chapter or section
context, the default for unmarked government prose), or that the signals
were insufficient or conflicting, in which case the class is ``unknown``.

No matched personal data is stored: a privacy reason names the detector, not
what it matched.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lovspor.forarbeider.document_classify import PrivacyCandidate, RightsCandidate

ASSESSMENT_VERSION = "span-assessment/1"

_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Text = Annotated[str, Field(pattern=r"\S")]


class RightsClass(StrEnum):
    """The architecture's span rights classes (§3.4), plus reported speech."""

    OFFICIAL_TEXT = "official_text"
    STATUTE_TEXT = "statute_text"
    PUBLIC_STATEMENT = "public_statement"
    THIRD_PARTY_QUOTE = "third_party_quote"
    ATTRIBUTED_STATEMENT = "attributed_statement"
    THIRD_PARTY_WORK = "third_party_work"
    UNKNOWN = "unknown"


class Evidence(StrEnum):
    """What a rights class rests on. Not a probability, and never a clearance."""

    STRUCTURE = "structure"
    STRUCTURE_AND_ATTRIBUTION = "structure_and_attribution"
    STRUCTURE_AND_CONTEXT = "structure_and_context"
    STATED_ATTRIBUTION = "stated_attribution"
    TEXTUAL_MARKER = "textual_marker"
    CHAPTER_CONTEXT = "chapter_context"
    DEFAULT_RULE = "default_rule"
    INSUFFICIENT = "insufficient"
    CONFLICTING = "conflicting"


class PrivacyState(StrEnum):
    """What the detectors found. ``no_detector_hit`` is silence, not proof."""

    NO_DETECTOR_HIT = "no_detector_hit"
    REVIEW_REQUIRED = "review_required"
    UNRESOLVED = "unresolved"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


_UNSETTLED = frozenset({Evidence.INSUFFICIENT, Evidence.CONFLICTING})


class EntryAssessment(_Frozen):
    """One span or gap: its class and privacy state, with reasons and evidence entries.

    ``evidence_entries`` are the ids of the entries the class rests on besides
    this one (the sentence introducing a quotation, the heading that opens a
    consultation section). ``candidate_*`` are the FC3 labels, kept for
    comparison only.
    """

    entry_id: _Text
    entry: Literal["span", "gap"]
    part_id: str | None
    source_sha256: _Digest | None
    text_sha256: _Digest | None
    rights: RightsClass
    evidence: Evidence
    rights_reasons: tuple[str, ...] = Field(min_length=1)
    evidence_entries: tuple[str, ...]
    privacy: PrivacyState
    privacy_reasons: tuple[str, ...]
    candidate_rights: RightsCandidate
    candidate_privacy: PrivacyCandidate

    @model_validator(mode="after")
    def _unknown_exactly_when_unsettled(self) -> Self:
        if (self.rights is RightsClass.UNKNOWN) != (self.evidence in _UNSETTLED):
            raise ValueError("a class is unknown exactly when its evidence is not settled")
        if (self.privacy is PrivacyState.NO_DETECTOR_HIT) == bool(self.privacy_reasons):
            raise ValueError("a privacy state other than no_detector_hit names its reasons")
        return self


class SpanAssessment(_Frozen):
    """Every entry of one version, assessed; linked to the version and its raw pages."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeid_span_assessment"] = "forarbeid_span_assessment"
    assessment_version: Literal["span-assessment/1"] = "span-assessment/1"
    release_state: Literal["private_ingestion"] = "private_ingestion"
    document_id: _Text
    version_id: _Digest
    extraction_sha256: _Digest
    classification_sha256: _Digest
    source_sha256s: tuple[_Digest, ...]
    source_state: Literal["source_complete", "source_incomplete", "unknown"]
    cleared: Literal[False] = False
    automatic_release_eligible: Literal[False] = False
    legal_opinion: Literal[False] = False
    entries: tuple[EntryAssessment, ...]

    @model_validator(mode="after")
    def _once_each_from_these_pages(self) -> Self:
        ids = [item.entry_id for item in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("an entry is assessed twice")
        pages = set(self.source_sha256s)
        if any(item.source_sha256 not in pages | {None} for item in self.entries):
            raise ValueError("an entry names a page outside the version")
        return self
