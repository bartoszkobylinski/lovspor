"""FC5: every span's rights class and privacy state, from verified raw evidence (#738).

:func:`assess_document` takes the same raw evidence as the version manifest
(FC6), builds that manifest (so the record names a version id it can prove),
extracts and classifies the pages (FC3) and assesses each entry. Nothing is
taken from the caller but bytes: no label, flag or confidence.

Rights, by fixed rules over structure, stated attribution and context, in
this order (the first that applies decides):

1. a span of a part whose markup or boundary is unproved: ``unknown``;
2. a third-party marker (©, copyright, "gjengitt med tillatelse", a photo or
   illustration credit): ``third_party_work``;
3. inside a quotation, by the sentence that introduces it (the paragraph just
   before the quotation, ending in a colon):
   the ministry's own text (departementet, regjeringen, the hearing paper):
   ``official_text``; a legal source (§, lov, forskrift, direktiv):
   ``statute_text``; a named public body: ``public_statement``; another named
   party: ``third_party_quote``; a named party together with a legal source:
   ``unknown`` (conflicting). No usable introduction: in a section of
   consultation views, ``third_party_quote``; in a draft-law chapter,
   ``statute_text``; otherwise ``unknown``;
4. in a chapter titled as a draft law or decision: ``statute_text``;
5. in an annex chapter: ``unknown`` (authorship not stated);
6. a heading: ``official_text``;
7. a paragraph that opens by naming a party as the speaker of a view
   ("Advokatforeningen mener ..."): ``attributed_statement``;
8. anything else: ``official_text``, by default, never as a clearance.

A gap is always ``unknown``. The word "høring" alone changes nothing: a
consultation section is recognised only by a heading about the consultation
bodies' views, and it only refines a quotation with no introduction.

Privacy is screened apart (:mod:`lovspor.forarbeider.span_privacy`).
"""

import re
from collections.abc import Iterator
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.document_classify import EntryLabel, classify_document
from lovspor.forarbeider.document_extract import extract_document
from lovspor.forarbeider.document_models import (
    DocumentExtraction,
    Gap,
    GapCode,
    Part,
    Span,
    text_digest,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.span_assessment_models import (
    EntryAssessment,
    Evidence,
    RightsClass,
    SpanAssessment,
)
from lovspor.forarbeider.span_privacy import screen, screen_gap
from lovspor.forarbeider.version_manifest import Evidence as SourceEvidence
from lovspor.forarbeider.version_manifest import build_version_manifest
from lovspor.forarbeider.version_models import canonical_text

_UNPROVED = frozenset(
    {
        GapCode.MALFORMED_MARKUP,
        GapCode.BOUNDARY_UNCONFIRMED,
        GapCode.COVERAGE_MISMATCH,
        GapCode.HIDDEN_CONTENT,
    }
)
_MARKER = re.compile(
    r"©|\bcopyright\b|gjengitt med tillatelse|\bfoto:|\billustrasjon:", re.IGNORECASE
)
_DRAFT_LAW = re.compile(r"^(?:forslag|utkast) til (?:lov|vedtak|forskrift)", re.IGNORECASE)
_CONSULTATION = re.compile(r"høringsinstans|høringsuttalels|høringsinnspill", re.IGNORECASE)
_LEGAL = re.compile(
    r"§|\b(?:lov(?:en|a|ens)?|forskrift\w*|direktiv\w*|forordning\w*|grunnloven"
    r"|lovforslag\w*|lovutkast\w*|bestemmelsen)\b",
    re.IGNORECASE,
)
_GOVERNMENT = re.compile(
    r"\b(?:departementet|regjeringen|\w+departementet|høringsnotatet|proposisjonen)\b",
    re.IGNORECASE,
)
_PUBLIC = re.compile(
    r"kommune\b|fylkeskommune|direktorat\w*|tilsynet\b|etaten\b|verket\b|^Statens\b"
    r"|Riksadvokaten|ombud\w*|domstol\w*|lagmannsrett|tingrett|Høyesterett"
    r"|Statsforvalter\w*|universitet\w*|Sametinget|Riksrevisjonen",
)
_VERB = (
    r"(?:skriver|uttaler|mener|påpeker|viser til|anfører|bemerker|foreslår|støtter"
    r"|går imot|er enig|er uenig|anbefaler|advarer|understreker|fremhever)"
)
#: Organisation nouns written in lower case after a proper name ("Oslo kommune").
_ORGANISATION = (
    r"[a-zæøå]*(?:kommune|rett|vesen|verk|tilsyn|direktorat|distrikt|forening|forbund"
    r"|råd|ombud|universitet|institutt|etat|bank)"
)
_NAME = (
    r"[A-ZÆØÅ][\wæøåÆØÅ.-]+"
    rf"(?:\s+(?:[A-ZÆØÅ][\wæøåÆØÅ.-]+|{_ORGANISATION}|og|for|i))*"
)
_SPEAKER = re.compile(rf"\b({_NAME})\s+{_VERB}\b")
_OPENING = re.compile(rf"^({_NAME})\s+{_VERB}\b")
#: Sentence openers that are not a speaker's name.
_GENERIC = frozenset(
    {
        "Det", "Den", "De", "Dette", "Disse", "I", "Her", "Som", "Etter", "Flere", "Mange",
        "Noen", "Høringsinstansene", "En", "Et", "Ett", "Samtlige", "Blant", "Om", "For",
        "Ingen", "Andre", "Enkelte", "Også", "Videre", "Både", "Alle", "Hun", "Han",
    }
)  # fmt: skip


class _Speaker(NamedTuple):
    kind: str
    reasons: tuple[str, ...]


class _Context(NamedTuple):
    """Where a span sits: its part, the consultation heading over it, its quotation's intro."""

    part: Part
    unproved: bool
    consultation: str | None
    intro: Span | None


def assess_document(evidence: SourceEvidence) -> SpanAssessment:
    """The assessment of the version ``evidence`` shows; reproducible from those bytes alone.

    Raises:
        ForarbeiderRefusedError: the refusals of FC3 extraction and FC6.
    """
    manifest = build_version_manifest(evidence)
    extraction = extract_document(evidence.pages)
    classification = classify_document(extraction)
    labels = {label.entry_id: label for label in classification.entries}
    entries = [*_part_entries(extraction, labels), *_document_gaps(extraction, labels)]
    return SpanAssessment(
        document_id=extraction.document_id,
        version_id=manifest.version_id,
        extraction_sha256=manifest.provenance.extraction_sha256,
        classification_sha256=manifest.provenance.classification_sha256,
        source_sha256s=tuple(part.source_sha256 for part in extraction.parts),
        source_state=manifest.source.state.value,
        entries=tuple(entries),
    )


def assessment_text(assessment: SpanAssessment) -> str:
    """The canonical bytes: sorted keys, two-space indent, one final newline."""
    return canonical_text(assessment)


def parse_assessment(raw: bytes) -> SpanAssessment:
    """The assessment in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else.
    """
    try:
        assessment = SpanAssessment.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment") from error
    if assessment_text(assessment).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment")
    return assessment


def verify_assessment(raw: bytes, evidence: SourceEvidence) -> SpanAssessment:
    """The assessment in ``raw``, when ``evidence`` reproduces exactly those bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` when it does not.
    """
    assessment = parse_assessment(raw)
    if assessment_text(assess_document(evidence)).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "assessment not reproduced")
    return assessment


# --- walking the parts -------------------------------------------------------------


def _part_entries(
    extraction: DocumentExtraction, labels: dict[str, EntryLabel]
) -> Iterator[EntryAssessment]:
    for part in extraction.parts:
        unproved = any(
            isinstance(e, Gap) and e.scope == "part" and e.code in _UNPROVED for e in part.entries
        )
        for entry, context in _contexts(part, unproved):
            if isinstance(entry, Span):
                yield _span(entry, context, labels[entry.id])
            else:
                yield _gap(entry, part, labels[entry.id])


def _contexts(part: Part, unproved: bool) -> Iterator[tuple[Span | Gap, _Context]]:
    """Each entry with its context: the open consultation heading and its quotation's intro."""
    headings: list[tuple[int, str | None]] = []
    intros: dict[int, Span | None] = {}
    previous: Span | Gap | None = None
    for entry in part.entries:
        if isinstance(entry, Span) and entry.kind == "heading":
            headings = [item for item in headings if item[0] < entry.level]
            headings.append((entry.level, entry.id if _CONSULTATION.search(entry.text) else None))
        group = entry.quote_group if isinstance(entry, Span) else None
        if group is not None and group not in intros:
            intros[group] = _intro(previous)
        consultation = next((item[1] for item in reversed(headings) if item[1]), None)
        intro = intros.get(group) if group is not None else None
        yield entry, _Context(part, unproved, consultation, intro)
        previous = entry


def _intro(previous: Span | Gap | None) -> Span | None:
    """The paragraph just before a quotation, when it ends in a colon."""
    if isinstance(previous, Span) and previous.quote_group is None:
        return previous if previous.text.rstrip().endswith(":") else None
    return None


# --- rights ------------------------------------------------------------------------


def _span(span: Span, context: _Context, label: EntryLabel) -> EntryAssessment:
    rights, evidence, reasons, sources = _rights(span, context)
    privacy, privacy_reasons = screen(
        span.text, quoted=span.quote_group is not None, unproved=context.unproved
    )
    return EntryAssessment(
        entry_id=span.id,
        entry="span",
        part_id=context.part.id,
        source_sha256=context.part.source_sha256,
        text_sha256=span.text_sha256,
        rights=rights,
        evidence=evidence,
        rights_reasons=reasons,
        evidence_entries=sources,
        privacy=privacy,
        privacy_reasons=privacy_reasons,
        candidate_rights=label.rights,
        candidate_privacy=label.privacy,
    )


_Verdict = tuple[RightsClass, Evidence, tuple[str, ...], tuple[str, ...]]


def _rights(span: Span, context: _Context) -> _Verdict:
    """The first rule in the module docstring that applies to ``span``."""
    if context.unproved:
        return RightsClass.UNKNOWN, Evidence.INSUFFICIENT, ("part_unproved",), ()
    if _MARKER.search(span.text):
        return RightsClass.THIRD_PARTY_WORK, Evidence.TEXTUAL_MARKER, ("third_party_marker",), ()
    if span.quote_group is not None:
        return _quotation(context)
    return _prose(span, context)


def _quotation(context: _Context) -> _Verdict:
    speaker = _speaker(context.intro.text) if context.intro else None
    if speaker is not None and context.intro is not None:
        return _attributed_quotation(speaker, context.intro.id)
    if context.consultation is not None:
        reasons = ("quotation", "consultation_section")
        return (
            RightsClass.THIRD_PARTY_QUOTE,
            Evidence.STRUCTURE_AND_CONTEXT,
            reasons,
            (context.consultation,),
        )
    if _DRAFT_LAW.search(context.part.title):
        reasons = ("quotation", "draft_law_chapter")
        return RightsClass.STATUTE_TEXT, Evidence.STRUCTURE_AND_CONTEXT, reasons, ()
    return RightsClass.UNKNOWN, Evidence.INSUFFICIENT, ("quotation_without_attribution",), ()


_QUOTED = {
    "own": (RightsClass.OFFICIAL_TEXT, "quoted_own_government_text"),
    "legal": (RightsClass.STATUTE_TEXT, "quoted_legal_source"),
    "public": (RightsClass.PUBLIC_STATEMENT, "attributed_to_public_body"),
    "party": (RightsClass.THIRD_PARTY_QUOTE, "attributed_to_named_party"),
}


def _attributed_quotation(speaker: _Speaker, intro_id: str) -> _Verdict:
    if speaker.kind == "conflict":
        reasons = ("quotation", *speaker.reasons)
        return RightsClass.UNKNOWN, Evidence.CONFLICTING, reasons, (intro_id,)
    rights, reason = _QUOTED[speaker.kind]
    return rights, Evidence.STRUCTURE_AND_ATTRIBUTION, ("quotation", reason), (intro_id,)


def _speaker(intro: str) -> _Speaker | None:
    """Who an introducing sentence says is quoted: a named subject first, then the source."""
    named = _named(_SPEAKER, intro)
    if named is not None and _LEGAL.search(intro) and named.kind == "party":
        return _Speaker("conflict", ("named_party_and_legal_source",))
    if named is not None:
        return named
    if _GOVERNMENT.search(intro):
        return _Speaker("own", ())
    return _Speaker("legal", ()) if _LEGAL.search(intro) else None


def _named(pattern: re.Pattern[str], text: str) -> _Speaker | None:
    """The speaker a sentence names before a verb of saying, by kind; generic openers skipped."""
    for match in pattern.finditer(text):
        subject = match.group(1)
        if subject.split()[0] in _GENERIC:
            continue
        if _GOVERNMENT.search(subject):
            return _Speaker("own", ())
        return _Speaker("public" if _PUBLIC.search(subject) else "party", ())
    return None


def _prose(span: Span, context: _Context) -> _Verdict:
    if _DRAFT_LAW.search(context.part.title):
        return RightsClass.STATUTE_TEXT, Evidence.CHAPTER_CONTEXT, ("draft_law_chapter",), ()
    if context.part.annex:
        return RightsClass.UNKNOWN, Evidence.INSUFFICIENT, ("annex_part",), ()
    if span.kind == "heading":
        return RightsClass.OFFICIAL_TEXT, Evidence.STRUCTURE, ("heading",), ()
    speaker = _named(_OPENING, span.text) if span.kind == "paragraph" else None
    if speaker is not None and speaker.kind in {"public", "party"}:
        reason = "speaker_public_body" if speaker.kind == "public" else "speaker_named_party"
        context_reason = ("consultation_section",) if context.consultation else ()
        return (
            RightsClass.ATTRIBUTED_STATEMENT,
            Evidence.STATED_ATTRIBUTION,
            ("reported_statement", reason, *context_reason),
            tuple(filter(None, (context.consultation,))),
        )
    return RightsClass.OFFICIAL_TEXT, Evidence.DEFAULT_RULE, ("government_prose",), ()


# --- gaps --------------------------------------------------------------------------


def _gap(gap: Gap, part: Part | None, label: EntryLabel) -> EntryAssessment:
    privacy, reasons = screen_gap(gap.text, gap.code.value)
    return EntryAssessment(
        entry_id=gap.id,
        entry="gap",
        part_id=part.id if part else None,
        source_sha256=part.source_sha256 if part else None,
        text_sha256=None if gap.text is None else text_digest(gap.text),
        rights=RightsClass.UNKNOWN,
        evidence=Evidence.INSUFFICIENT,
        rights_reasons=(f"extraction_gap:{gap.code.value}",),
        evidence_entries=(),
        privacy=privacy,
        privacy_reasons=reasons,
        candidate_rights=label.rights,
        candidate_privacy=label.privacy,
    )


def _document_gaps(
    extraction: DocumentExtraction, labels: dict[str, EntryLabel]
) -> Iterator[EntryAssessment]:
    for gap in extraction.gaps:
        yield _gap(gap, None, labels[gap.id])
