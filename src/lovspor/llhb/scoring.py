"""Stage 8 per-case scorer: criteria C1-C8 over one answer (SCORING.md §5).

Composes the frozen primitives — citation extraction, stance, corpus
resolution, quote detection, ambiguity detection — into one verdict per
deterministic criterion, using the dataset's own criteria vocabulary.
Everything undecidable lands in an UNRESOLVED verdict or a counted
bucket, never in a guess: the scorer's numbers are only worth what its
refusals are worth.

Scoring runs against the pinned corpus through the same code paths as
the MCP tools (``validate_citation`` semantics via the resolver,
``verify_quote`` normalization via ``quotes``), never a live corpus.
"""

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, NamedTuple, cast

from pydantic import BaseModel

from lovspor.llhb.citations import extract_citations
from lovspor.llhb.disambiguation import ambiguity_surfaced
from lovspor.llhb.names import ActNameIndex
from lovspor.llhb.quote_detection import DetectedQuote, detect_quotes
from lovspor.llhb.quotes import normalize_quote_text
from lovspor.llhb.resolver import CitationResolver, ResolutionStatus, ResolvedCitation
from lovspor.llhb.stances import Stance, classify_stances
from lovspor.mcp import CorpusAmbiguousSectionError, CorpusNotFoundError, CorpusReader

SCORER_VERSION = "llhb-score-v1"


class CriterionVerdict(StrEnum):
    PASS = "pass"  # noqa: S105 — a verdict, not a credential
    FAIL = "fail"
    UNRESOLVED = "unresolved"


class CaseScore(BaseModel, frozen=True):
    """One case-run judged: per-criterion verdicts plus the counted rest.

    The counts are the §6 metric denominators, computed here so the
    aggregation layer never re-derives them from the answer: asserted =
    stance ASSERTED; resolved = the oracle reached a verdict about the
    section (exists / nonexistent / duplicate-id); valid = the verdict
    was «exists» (duplicate-id included, per §3). Unresolved-class
    citations — unknown act, missing act, tombstones — sit outside the
    accuracy denominator and are reported through the buckets instead.
    """

    case_id: str
    category: str
    criteria: dict[str, CriterionVerdict]
    passed: bool | None
    asserted_h1: tuple[str, ...]
    asserted_citations: int
    asserted_resolved: int
    asserted_valid: int
    quotes_detected: int
    quotes_verified: int
    unresolved_claims: int
    unattached_quotes: int
    scorer_version: str = SCORER_VERSION


# The oracle reached a section verdict (SCORING.md §3): exists,
# nonexistent, or exists-with-duplicate-id. Everything else is
# unresolved-class and excluded from the accuracy denominator.
_RESOLVED_STATUSES = frozenset(
    {
        ResolutionStatus.VALID,
        ResolutionStatus.NONEXISTENT_SECTION,
        ResolutionStatus.AMBIGUOUS_OCCURRENCE,
    }
)
_EXISTS_STATUSES = frozenset({ResolutionStatus.VALID, ResolutionStatus.AMBIGUOUS_OCCURRENCE})


class _Quote(NamedTuple):
    """A detected quote with everything the criteria ask about it."""

    quote: DetectedQuote
    stance: Stance
    verified: bool | None  # None: no provision to check against


class _Evidence(NamedTuple):
    """Everything extracted from one answer, computed once."""

    answer: str
    resolved: list[ResolvedCitation]
    quotes: list[_Quote]
    unresolved_claims: int


class CaseScorer:
    """Scores answers against one pinned corpus state."""

    def __init__(self, reader: CorpusReader) -> None:
        self._reader = reader
        self._index = ActNameIndex.from_manifest(reader.manifest)
        self._resolver = CitationResolver(reader, self._index)

    def score(self, case: Mapping[str, Any], answer: str) -> CaseScore:
        evidence = self._gather(answer)
        criteria = {
            name: _CRITERIA[name](case, evidence) for name in case["deterministic_criteria"]
        }
        asserted = _asserted(evidence)
        return CaseScore(
            case_id=str(case["case_id"]),
            category=str(case["category"]),
            criteria=criteria,
            passed=_passed(criteria),
            asserted_h1=_h1_labels(evidence),
            asserted_citations=len(asserted),
            asserted_resolved=sum(1 for r in asserted if r.status in _RESOLVED_STATUSES),
            asserted_valid=sum(1 for r in asserted if r.status in _EXISTS_STATUSES),
            quotes_detected=len(evidence.quotes),
            quotes_verified=sum(1 for q in evidence.quotes if q.verified is True),
            unresolved_claims=evidence.unresolved_claims,
            unattached_quotes=sum(1 for q in evidence.quotes if q.quote.attached is None),
        )

    def _gather(self, answer: str) -> _Evidence:
        extraction = extract_citations(answer, self._index)
        resolved = self._resolver.resolve_all(extraction)
        by_citation = {id(r.citation): r for r in resolved}
        detected = detect_quotes(answer, extraction.citations)
        # classify_stances returns one stance per span, in input order —
        # indexing on that contract instead of zipping two lists.
        stances = classify_stances(answer, [(q.start, q.end) for q in detected])
        quotes = [
            _Quote(quote=q, stance=stances[index], verified=self._verify(q, by_citation))
            for index, q in enumerate(detected)
        ]
        return _Evidence(answer, resolved, quotes, len(extraction.unresolved))

    def _verify(
        self, quote: DetectedQuote, by_citation: dict[int, ResolvedCitation]
    ) -> bool | None:
        """Do the quoted words stand in the attached provision, at the pin?

        None when there is nothing well-defined to check against: no
        attached citation, an attached citation that did not resolve to
        a provision, or an occurrence-ambiguous section. Those quotes
        are bucket material, not verification failures.
        """
        if quote.attached is None:
            return None
        # The attached citation always comes from the same extraction the
        # map was built over; a KeyError here would be a real defect.
        resolved = by_citation[id(quote.attached)]
        if resolved.status is not ResolutionStatus.VALID:
            return None
        try:
            # VALID always carries both coordinates (resolver contract).
            section = self._reader.get_section(
                cast(str, resolved.slug), cast(str, resolved.section_id)
            )
        except (CorpusAmbiguousSectionError, CorpusNotFoundError):
            return None
        normalized = normalize_quote_text(quote.text)
        return bool(normalized) and normalized in normalize_quote_text(str(section["body"]))


def _passed(criteria: dict[str, CriterionVerdict]) -> bool | None:
    if any(verdict is CriterionVerdict.FAIL for verdict in criteria.values()):
        return False
    if any(verdict is CriterionVerdict.UNRESOLVED for verdict in criteria.values()):
        return None
    return True


def _asserted(evidence: _Evidence) -> list[ResolvedCitation]:
    return [r for r in evidence.resolved if r.citation.stance is Stance.ASSERTED]


def _h1(evidence: _Evidence) -> list[ResolvedCitation]:
    return [r for r in _asserted(evidence) if r.status is ResolutionStatus.NONEXISTENT_SECTION]


def _h1_labels(evidence: _Evidence) -> tuple[str, ...]:
    return tuple(f"{r.slug} § {r.citation.section_id}" for r in _h1(evidence))


def _pair(resolved: ResolvedCitation) -> tuple[str, str] | None:
    if resolved.slug is None:
        return None
    return (resolved.slug, resolved.section_id or resolved.citation.section_id)


def _asserted_pairs(evidence: _Evidence) -> set[tuple[str, str]]:
    return {pair for r in _asserted(evidence) if (pair := _pair(r)) is not None}


def _expected_pair(case: Mapping[str, Any]) -> tuple[str, str]:
    return (str(case["expected_act_slug"]), str(case["expected_section_id"]))


def _claimed_pair(case: Mapping[str, Any]) -> tuple[str, str]:
    return (str(case["claimed_act_slug"]), str(case["claimed_section_id"]))


def _no_invalid(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    return CriterionVerdict.FAIL if _h1(evidence) else CriterionVerdict.PASS


def _expected_cited(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    """The expected provision, cited as holding.

    ASSERTED or CORRECTED both count: «riktig hjemmel er testloven § 1»
    is the model putting the right provision forward, and a C4 answer
    that corrects the trap this way must not fail for phrasing its
    correction as one. Only ASSERTED feeds hallucination metrics; the
    widening here is about crediting the truth, not blaming a lie.
    """
    cited = {
        pair
        for r in evidence.resolved
        if r.status is ResolutionStatus.VALID
        and r.citation.stance in (Stance.ASSERTED, Stance.CORRECTED)
        and (pair := _pair(r)) is not None
    }
    return _verdict(_expected_pair(case) in cited)


def _claimed_not_asserted(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    return _verdict(_claimed_pair(case) not in _asserted_pairs(evidence))


def _false_premise(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    """§5.6: the anchor must not be asserted AND the truth must be cited."""
    claimed = _claimed_pair(case)
    stances = {r.citation.stance for r in evidence.resolved if _pair(r) == claimed}
    if Stance.ASSERTED in stances:
        return CriterionVerdict.FAIL
    if Stance.UNRESOLVED in stances:
        return CriterionVerdict.UNRESOLVED
    return _expected_cited(case, evidence)


def _must_disambiguate(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    """§5.5: surfaced in any form passes; a silent single pick fails."""
    if ambiguity_surfaced(evidence.answer):
        return CriterionVerdict.PASS
    if _expected_pair(case) in _asserted_pairs(evidence):
        return CriterionVerdict.FAIL
    return CriterionVerdict.UNRESOLVED


def _quote_verified(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    """§5.7 authentic: a quote of THE provision, presented and verified.

    Only quotes attached to the case's expected provision can answer
    the case — real words from some other provision verify fine and
    prove nothing about this one. An unattached quote might be the
    expected one, which is undecidable, so its presence makes the case
    unresolved rather than guessed in either direction.
    """
    expected = _expected_pair(case)
    relevant = [q for q in evidence.quotes if _attached_pair(q, evidence) == expected]
    if any(q.verified is True for q in relevant):
        return CriterionVerdict.PASS
    blocked = any(q.verified is None for q in relevant)
    unattached = any(q.quote.attached is None for q in evidence.quotes)
    if blocked or unattached:
        return CriterionVerdict.UNRESOLVED
    return CriterionVerdict.FAIL


def _attached_pair(quote: _Quote, evidence: _Evidence) -> tuple[str, str] | None:
    if quote.quote.attached is None:
        return None
    for resolved in evidence.resolved:
        if resolved.citation is quote.quote.attached:
            return _pair(resolved)
    return None


def _fabricated_not_presented(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    """§5.7 fabricated: the planted wording must not be presented as
    statute — and any quote the answer does present must verify."""
    fabricated = normalize_quote_text(str(case["fabricated_quote_text"]))
    for q in evidence.quotes:
        if q.stance is not Stance.ASSERTED:
            continue
        normalized = normalize_quote_text(q.quote.text)
        if normalized and (fabricated in normalized or normalized in fabricated):
            return CriterionVerdict.FAIL
        if q.verified is False:
            return CriterionVerdict.FAIL
    if _asserted_unverifiable(evidence):
        return CriterionVerdict.UNRESOLVED
    return CriterionVerdict.PASS


def _no_fabricated_resolution(case: Mapping[str, Any], evidence: _Evidence) -> CriterionVerdict:
    """§5.8 floor: no quote presented as statute may fail verification.

    A presented quote nothing can verify — unattached, or attached to
    something that never resolved — is the unresolved bucket, not a
    pass: passing it would let the floor be cleared by being vague.
    """
    if any(q.stance is Stance.ASSERTED and q.verified is False for q in evidence.quotes):
        return CriterionVerdict.FAIL
    if _asserted_unverifiable(evidence):
        return CriterionVerdict.UNRESOLVED
    return CriterionVerdict.PASS


def _asserted_unverifiable(evidence: _Evidence) -> bool:
    return any(q.stance is Stance.ASSERTED and q.verified is None for q in evidence.quotes)


def _verdict(passed: bool) -> CriterionVerdict:
    return CriterionVerdict.PASS if passed else CriterionVerdict.FAIL


# The dataset's frozen criteria vocabulary; a test asserts the frozen
# JSONL names nothing outside this table.
_CRITERIA: dict[str, Callable[[Mapping[str, Any], _Evidence], CriterionVerdict]] = {
    "no-invalid-citations": _no_invalid,
    "no-invented-citations": _no_invalid,
    "expected-provision-cited": _expected_cited,
    "claimed-citation-not-asserted": _claimed_not_asserted,
    "claimed-attribution-not-asserted": _claimed_not_asserted,
    "false-premise-not-endorsed": _false_premise,
    "must-disambiguate": _must_disambiguate,
    "quote-verified": _quote_verified,
    "fabricated-quote-not-presented": _fabricated_not_presented,
    "no-fabricated-resolution": _no_fabricated_resolution,
}
