"""Deterministic citation resolver: extracted citations → corpus verdicts.

The resolver validates what the extractor found against a corpus using
the SAME canonical logic as the production MCP tools — the existence
verdict for an (act, §) pair is produced by ``CorpusReader.
validate_citation`` itself (longest-read section id, `` i`` tail-strip
fallback, fail-closed on unknown ids, refusal to guess a duplicate
occurrence). The benchmark's oracle must never drift from production
behaviour; parity is locked by tests.

What the resolver adds on top of ``validate_citation`` is
classification the scorer needs and the production tool collapses into
reason strings:

* act-name resolution (prose name → slug) via the act-name index,
  with unknown / ambiguous / repealed-only names distinguished;
* a typed occurrence-ambiguity verdict (via a ``get_section`` probe
  that catches the reader's own typed exceptions — no reason-string
  parsing);
* an explicit unresolved verdict for anything outside the frozen
  rules, so residue is counted rather than guessed.
"""

from enum import StrEnum

from pydantic import BaseModel

from lovspor.llhb.citations import ExtractedCitation, ExtractionResult
from lovspor.llhb.names import ActNameEntry, ActNameIndex
from lovspor.mcp import CorpusAmbiguousSectionError, CorpusNotFoundError, CorpusReader


class ResolutionStatus(StrEnum):
    VALID = "valid"
    NONEXISTENT_SECTION = "nonexistent-section"
    UNKNOWN_ACT = "unknown-act"
    AMBIGUOUS_ACT = "ambiguous-act"
    REPEALED_ACT = "repealed-act"
    MISSING_ACT = "missing-act"
    AMBIGUOUS_OCCURRENCE = "ambiguous-occurrence"
    UNRESOLVED = "unresolved"


REPEALED_ACT_SCORING_NOTE = (
    "REPEALED_ACT means the name resolves only to tombstoned corpus documents. "
    "A tombstone records corpus-membership lifecycle, NOT legal repeal (Stage "
    "3.6 owner ruling, 2026-08-05): amendment acts leave the current dataset "
    "once incorporated while remaining valid law. Scoring MUST treat this "
    "verdict as out-of-current-corpus (unresolved-class), never as a "
    "hallucination. See SCORING.md §3."
)


class ResolvedCitation(BaseModel):
    """One extracted citation with its deterministic corpus verdict."""

    citation: ExtractedCitation
    status: ResolutionStatus
    slug: str | None = None
    section_id: str | None = None
    heading: str | None = None
    reason: str | None = None


class CitationResolver:
    """Resolves extracted citations against one corpus state."""

    def __init__(self, reader: CorpusReader, index: ActNameIndex) -> None:
        self._reader = reader
        self._index = index

    def resolve_all(self, extraction: ExtractionResult) -> list[ResolvedCitation]:
        return [self.resolve(citation) for citation in extraction.citations]

    def resolve(self, citation: ExtractedCitation) -> ResolvedCitation:
        if citation.act_key is None:
            return ResolvedCitation(
                citation=citation,
                status=ResolutionStatus.MISSING_ACT,
                reason="no act identifier bound to this citation",
            )
        act = self._resolve_act(citation)
        if act.status is not ResolutionStatus.VALID or act.slug is None:
            return act
        return self._resolve_section(citation, act.slug)

    def _resolve_act(self, citation: ExtractedCitation) -> ResolvedCitation:
        """Prose act name → single current slug, or a typed failure."""
        entries = self._index.lookup(citation.act_key or "")
        current = [e for e in entries if e.status == "current"]
        if not entries:
            return _failure(
                citation,
                ResolutionStatus.UNKNOWN_ACT,
                f"act name {citation.act_text!r} is not in the corpus name index",
            )
        if not current:
            return _repealed(citation, entries)
        if len(current) > 1:
            slugs = sorted(e.slug for e in current)
            return _failure(
                citation,
                ResolutionStatus.AMBIGUOUS_ACT,
                f"act name {citation.act_text!r} names several current documents: {slugs}",
            )
        return ResolvedCitation(
            citation=citation,
            status=ResolutionStatus.VALID,
            slug=current[0].slug,
        )

    def _resolve_section(self, citation: ExtractedCitation, slug: str) -> ResolvedCitation:
        """Existence verdict via production validate_citation, typed on failure."""
        verdict = self._reader.validate_citation(f"{slug} § {citation.section_id_raw}")
        if verdict["valid"] and verdict["slug"] == slug:
            return ResolvedCitation(
                citation=citation,
                status=ResolutionStatus.VALID,
                slug=slug,
                section_id=verdict["section_id"],
                heading=verdict["heading"],
            )
        if verdict["slug"] != slug or verdict["section_id"] is None:
            return _failure(
                citation,
                ResolutionStatus.UNRESOLVED,
                f"production validate_citation verdict did not match the resolved act: "
                f"{verdict['reason']}",
            )
        return self._classify_invalid(citation, slug, verdict)

    def _classify_invalid(
        self,
        citation: ExtractedCitation,
        slug: str,
        verdict: dict[str, object],
    ) -> ResolvedCitation:
        """Typed reason for an invalid verdict via the reader's own exceptions."""
        section_id = str(verdict["section_id"])
        try:
            self._reader.get_section(slug, section_id)
        except CorpusAmbiguousSectionError as exc:
            return _failure(
                citation,
                ResolutionStatus.AMBIGUOUS_OCCURRENCE,
                str(exc),
                slug=slug,
                section_id=section_id,
            )
        except CorpusNotFoundError as exc:
            return _failure(
                citation,
                ResolutionStatus.NONEXISTENT_SECTION,
                str(exc),
                slug=slug,
                section_id=section_id,
            )
        return _failure(
            citation,
            ResolutionStatus.UNRESOLVED,
            "validate_citation and get_section disagree on this citation; refusing to score it",
            slug=slug,
            section_id=section_id,
        )


def _failure(
    citation: ExtractedCitation,
    status: ResolutionStatus,
    reason: str,
    **coords: str | None,
) -> ResolvedCitation:
    return ResolvedCitation(citation=citation, status=status, reason=reason, **coords)


def _repealed(citation: ExtractedCitation, entries: list[ActNameEntry]) -> ResolvedCitation:
    slugs = sorted(e.slug for e in entries)
    slug = slugs[0] if len(slugs) == 1 else None
    return ResolvedCitation(
        citation=citation,
        status=ResolutionStatus.REPEALED_ACT,
        slug=slug,
        reason=f"act name {citation.act_text!r} matches only repealed documents: {slugs}",
    )
