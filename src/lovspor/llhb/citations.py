"""Deterministic statutory-citation extractor for model answers.

Turns free-text answers into structured citation candidates for the
LLHB scorer. The extractor is conservative and auditable: it recognizes
the documented syntax below and nothing else; a ``§`` construct it
cannot parse becomes an explicit unresolved claim, never a guess.

Recognized syntax (documented contract, see TOOLING.md):

* ``<act-name> § <id>`` / ``<act-name> §<id>`` — act bound *before*;
* ``§ <id> <act-name>``, ``§ <id> i <act-name>``, ``§ <id> etter
  <act-name>`` — act bound *after*;
* ``<abbrev.> § <id>`` where the abbreviation is in the frozen table;
* bare ``§ <id>`` — act resolved by the nearest act mention at or
  before the citation in the same sentence, else the nearest mention
  in the same paragraph, else no act (missing-act residue);
* ``§§ <id> og <id>`` / ``§§ <id>, <id>`` — split into individual
  citations; ``§§ <id> til <id>`` — the two endpoints only, flagged
  ``from_range`` (interior sections are never assumed);
* section ids in the corpus grammar (``lovspor.headings.SECTION_ID``):
  ``1``, ``5-12``, ``5-10a``, ``8-7 a``, ``10-4-1``, ``x-1`` … —
  matched raw, canonicalized via ``canonical_section_id``.

The spaced-letter/preposition ambiguity (``§ 12 i skatteloven`` reads
as id ``12 i``) is deliberately preserved at extraction time: the
resolver applies exactly the production ``validate_citation`` fallback
(longest read first, then the `` i`` tail strip), so extractor and MCP
agree on what was cited.
"""

import re

from pydantic import BaseModel

from lovspor.headings import SECTION_ID, canonical_section_id
from lovspor.llhb.abbreviations import (
    ABBREVIATIONS,
    ABBREVIATIONS_VERSION,
    expand_abbreviation,
)
from lovspor.llhb.names import ActNameIndex, normalize_name
from lovspor.llhb.stances import (
    STANCE_RULES_VERSION,
    Stance,
    classify_stances,
    sentence_bounds,
)

# The trailing guard closes issue #85: SECTION_ID's spaced-letter branch
# checks "not followed by [A-Za-z]", so a word whose SECOND letter is
# æ/ø/å («første», «følger», «hører») tokenizes as a standalone letter
# and gets swallowed into the id («§ 8 første» → «8 f»). Refusing a
# match that ends flush against æ/ø/å makes the regex backtrack to the
# bare number; the documented «§ 12 i skatteloven» longest-read is
# untouched because its swallowed «i» is followed by a space.
_NO_SPLIT_WORD = r"(?![æøåÆØÅ])"
_SECTION_REF = re.compile(rf"§(?P<double>§)?\s*(?P<sid>{SECTION_ID.pattern}){_NO_SPLIT_WORD}")
_MULTI_JOIN = re.compile(rf"\s*(?P<join>,|og|til)\s+(?P<sid>{SECTION_ID.pattern}){_NO_SPLIT_WORD}")
_AFTER_ACT_GAP = re.compile(r"^[\s,]*(?:i|etter)?\s*$")
_ADJACENT_GAP = re.compile(r"^\s*$")
_ABBREV_TOKEN = re.compile(
    r"(?<![\w-])(?:" + "|".join(re.escape(k[:-1]) for k in ABBREVIATIONS) + r")\.",
    re.IGNORECASE,
)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


class ActMentionRef(BaseModel, frozen=True):
    """An act reference usable for binding: a name mention or an abbreviation."""

    start: int
    end: int
    key: str  # normalized act-name key
    via_abbreviation: str | None = None


class ExtractedCitation(BaseModel):
    """One citation-shaped claim, with enough context for scoring."""

    text: str
    start: int
    end: int
    section_id: str
    section_id_raw: str
    act_key: str | None
    act_text: str | None
    act_binding: str | None  # abbreviation | before | after | sentence | paragraph
    abbreviation: str | None = None
    from_range: bool = False
    stance: Stance


class UnresolvedClaim(BaseModel):
    """A ``§`` construct the frozen syntax cannot parse — counted, not guessed."""

    text: str
    start: int
    end: int
    reason: str


class ExtractionResult(BaseModel):
    citations: list[ExtractedCitation]
    unresolved: list[UnresolvedClaim]
    abbreviations_version: str = ABBREVIATIONS_VERSION
    stance_rules_version: str = STANCE_RULES_VERSION


class _RawCitation(BaseModel):
    start: int
    end: int
    section_id_raw: str
    from_range: bool = False


def extract_citations(answer: str, index: ActNameIndex) -> ExtractionResult:
    """Extract every recognized citation from ``answer``; see module doc."""
    raw, unresolved = _scan_section_refs(answer)
    mentions = _collect_act_mentions(answer, index)
    stances = classify_stances(answer, [(c.start, c.end) for c in raw])
    citations = [
        _bind_act(answer, item, mentions, stance) for item, stance in zip(raw, stances, strict=True)
    ]
    return ExtractionResult(citations=citations, unresolved=unresolved)


def _scan_section_refs(answer: str) -> tuple[list[_RawCitation], list[UnresolvedClaim]]:
    """All ``§``/``§§`` constructs, split into parsed citations and residue."""
    raw: list[_RawCitation] = []
    unresolved: list[UnresolvedClaim] = []
    consumed_until = 0
    for match in _SECTION_REF.finditer(answer):
        if match.start() < consumed_until:
            continue
        if match.group("double"):
            consumed_until = _scan_multi(answer, match, raw, unresolved)
        else:
            raw.append(
                _RawCitation(
                    start=match.start(),
                    end=match.end(),
                    section_id_raw=match.group("sid"),
                ),
            )
            consumed_until = match.end()
    unresolved.extend(_orphan_markers(answer, raw, unresolved))
    return raw, unresolved


def _scan_multi(
    answer: str,
    match: re.Match[str],
    raw: list[_RawCitation],
    unresolved: list[UnresolvedClaim],
) -> int:
    """Parse one ``§§`` group into member citations; return consumed end."""
    ids: list[tuple[str, int, int]] = [(match.group("sid"), match.start(), match.end())]
    joins: list[str] = []
    pos = match.end()
    while joined := _MULTI_JOIN.match(answer, pos):
        joins.append(joined.group("join"))
        ids.append((joined.group("sid"), joined.start("sid"), joined.end("sid")))
        pos = joined.end()
    range_endpoints = 2
    if "til" in joins and (len(ids) != range_endpoints or joins != ["til"]):
        unresolved.append(
            UnresolvedClaim(
                text=answer[match.start() : pos],
                start=match.start(),
                end=pos,
                reason="unsupported §§ range/conjunction mix",
            ),
        )
        return pos
    is_range = joins == ["til"]
    for sid, id_start, id_end in ids:
        raw.append(
            _RawCitation(
                start=match.start() if id_start == match.start() else id_start,
                end=id_end,
                section_id_raw=sid,
                from_range=is_range,
            ),
        )
    return pos


def _orphan_markers(
    answer: str,
    raw: list[_RawCitation],
    unresolved: list[UnresolvedClaim],
) -> list[UnresolvedClaim]:
    """``§`` characters no parse consumed — e.g. ``§`` with no id after it."""
    covered = [(c.start, c.end) for c in raw] + [(u.start, u.end) for u in unresolved]
    orphans: list[UnresolvedClaim] = []
    for offset, char in enumerate(answer):
        if char != "§" or any(s <= offset < e for s, e in covered):
            continue
        orphans.append(
            UnresolvedClaim(
                text=answer[offset : min(len(answer), offset + 12)],
                start=offset,
                end=offset + 1,
                reason="§ marker with no parseable section id",
            ),
        )
    return orphans


def _collect_act_mentions(answer: str, index: ActNameIndex) -> list[ActMentionRef]:
    """Act-name mentions plus frozen-table abbreviation mentions, sorted."""
    mentions = [ActMentionRef(start=m.start, end=m.end, key=m.key) for m in index.scan(answer)]
    for match in _ABBREV_TOKEN.finditer(answer):
        expanded = expand_abbreviation(match.group(0))
        if expanded is not None:
            mentions.append(
                ActMentionRef(
                    start=match.start(),
                    end=match.end(),
                    key=normalize_name(expanded),
                    via_abbreviation=match.group(0),
                ),
            )
    return sorted(mentions, key=lambda m: (m.start, -m.end))


def _bind_act(
    answer: str,
    item: _RawCitation,
    mentions: list[ActMentionRef],
    stance: Stance,
) -> ExtractedCitation:
    """Apply the documented binding precedence to one raw citation."""
    bound = (
        _bind_adjacent_before(answer, item, mentions)
        or _bind_adjacent_after(answer, item, mentions)
        or _bind_scope(answer, item, mentions)
    )
    mention: ActMentionRef | None = bound[0] if bound else None
    binding: str | None = bound[1] if bound else None
    return ExtractedCitation(
        text=answer[item.start : item.end],
        start=item.start,
        end=item.end,
        section_id=canonical_section_id(item.section_id_raw),
        section_id_raw=item.section_id_raw,
        act_key=mention.key if mention else None,
        act_text=answer[mention.start : mention.end] if mention else None,
        act_binding=binding,
        abbreviation=mention.via_abbreviation if mention else None,
        from_range=item.from_range,
        stance=stance,
    )


def _bind_adjacent_before(
    answer: str,
    item: _RawCitation,
    mentions: list[ActMentionRef],
) -> tuple[ActMentionRef, str] | None:
    for mention in mentions:
        if mention.end <= item.start and _ADJACENT_GAP.match(answer[mention.end : item.start]):
            kind = "abbreviation" if mention.via_abbreviation else "before"
            return mention, kind
    return None


def _bind_adjacent_after(
    answer: str,
    item: _RawCitation,
    mentions: list[ActMentionRef],
) -> tuple[ActMentionRef, str] | None:
    for mention in mentions:
        if mention.start >= item.end and _AFTER_ACT_GAP.match(answer[item.end : mention.start]):
            return mention, "after"
    return None


def _bind_scope(
    answer: str,
    item: _RawCitation,
    mentions: list[ActMentionRef],
) -> tuple[ActMentionRef, str] | None:
    """Nearest mention at-or-before in the sentence, else nearest in paragraph."""
    s_start, s_end = sentence_bounds(answer, item.start)
    in_sentence = [m for m in mentions if s_start <= m.start and m.end <= s_end]
    before = [m for m in in_sentence if m.end <= item.start]
    if before:
        return max(before, key=lambda m: m.end), "sentence"
    p_start, p_end = _paragraph_bounds(answer, item.start)
    in_paragraph = [m for m in mentions if p_start <= m.start and m.end <= p_end]
    if in_paragraph:
        return min(in_paragraph, key=lambda m: _distance(item, m)), "paragraph"
    return None


def _paragraph_bounds(text: str, pos: int) -> tuple[int, int]:
    start = 0
    end = len(text)
    for match in _PARAGRAPH_BREAK.finditer(text):
        if match.end() <= pos:
            start = match.end()
        elif match.start() >= pos:
            end = match.start()
            break
    return start, end


def _distance(item: _RawCitation, mention: ActMentionRef) -> int:
    if mention.end <= item.start:
        return item.start - mention.end
    return mention.start - item.end
