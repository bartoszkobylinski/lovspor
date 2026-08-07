"""Corpus-first candidate construction for the LLHB pool (Stage 3).

Ground truth first, wording second (owner decision 8 / Stage 3 rule):
every builder starts from verified corpus material — a real section, a
real duplicate id, a real tombstone, a deterministically-absent trap id
— assembles the ground-truth metadata and oracle evidence, and only
then fills a frozen template. No model is involved anywhere.

Builders return case dicts WITHOUT ``case_id``; the pool orchestrator
(``lovspor.llhb.pool``) assigns stable sequential ids (also to
candidates that later fail validation — ids are never recycled) and
runs the Stage 2 ``CandidateValidator`` over everything.
"""

import random
import re
from collections import Counter

from pydantic import BaseModel

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.quotes import display_span_text, normalize_quote_text, quote_sha256
from lovspor.mcp import CorpusReader
from lovspor.storage.manifest import ManifestRecord

GENERATION_SEED_DEFAULT = 20260805

TOPIC_FILTER_VERSION = "llhb-topic-filter-v2"
"""Freeze-surface version of the meta-topic lexicon and usability rule.

v2 (F3, C2-746): 'om'-phrase heading prefixes (Generelt om / Nærmere om /
Særlig om) are stripped from topics — a frame that supplies its own 'om'
('reglene om {topic}') otherwise doubles the preposition: 'reglene om
generelt om behandling av dyr'."""

META_TOPIC = re.compile(
    r"virkeområde|verkeområde|definisjon|ikrafttred|ikraftset|iverkset|overgangs"
    r"|formål|innledende|alminnelige|avsluttende|sluttbestemmelser|fellesbestemmelser"
    r"|oppheving|endringer i andre|generelle bestemmelser"
    r"|hva (?:loven|forskriften) gjelder",
)
"""Meta/structural heading topics. Owner review (Stage 3.5) showed they
cannot anchor discovery/premise questions: they occur in most acts, so a
question built on them has no unique referent (RC4)."""

_MINISTRY_LINE = re.compile(r'^ministry:\s*"?([^"\n]+)"?\s*$', re.MULTILINE)
_PARENTHETICAL = re.compile(r"\(([^()]+)\)")
_LAW_NAME_SUFFIXES = ("loven", "lova", "forskriften", "forskrifta")
_HEADING_PREFIX = re.compile(r"^§\s*[^.]*\.\s*")
_CHAPTER_ID = re.compile(r"^(\d+)-(\d+)$")
_PLAIN_ID = re.compile(r"^\d+(-\d+)?$")
_MIN_TOPIC_CHARS = 10
_MIN_QUOTE_CHARS = 40
_MAX_QUOTE_CHARS = 140

_EASY_MAX_SECTIONS = 30
_MEDIUM_MAX_SECTIONS = 150


class SectionInfo(BaseModel, frozen=True):
    section_id: str
    occurrence: int
    heading: str
    kind: str


class ActInfo(BaseModel):
    slug: str
    doc_id: str
    doc_type: str
    title: str
    display_name: str
    ministry: str | None
    sections: list[SectionInfo]

    @property
    def section_count(self) -> int:
        return len(self.sections)

    def topic_sections(self) -> list[tuple[SectionInfo, str]]:
        """Unique-id, non-repealed sections with a usable heading topic."""
        ids = [s.section_id for s in self.sections]
        picks: list[tuple[SectionInfo, str]] = []
        for section in self.sections:
            topic = topic_of(section.heading)
            if topic is not None and ids.count(section.section_id) == 1:
                picks.append((section, topic))
        return picks


def display_name(record: ManifestRecord) -> str:
    """Human name for prose: title's law-name parenthetical, else title."""
    for group in _PARENTHETICAL.findall(record.title or ""):
        candidate: str = str(group).strip()
        if " " not in candidate and candidate.casefold().endswith(_LAW_NAME_SUFFIXES):
            return candidate
    return _strip_title_period(record.title or record.slug or "")


def _strip_title_period(title: str) -> str:
    """Drop a title-final SENTENCE period (F3, C5-703..705).

    1437 corpus titles end with a period; composed into a frame it reads
    '… pensjonskassers innskudd. § 1?' — the section reference lands in a
    new sentence. Abbreviation periods (``mv.``, ``m.v.``) are spelling,
    not sentence punctuation, and stay."""
    if not title.endswith("."):
        return title
    word = title[:-1].rsplit(" ", 1)[-1].lstrip("(«").casefold()
    return title if word in _ABBREVIATIONS_BEFORE_PERIOD else title[:-1]


_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

_GENERIC_TOPIC_PREFIXES = ("generelt om ", "nærmere om ", "særlig om ")
"""'Om'-phrase heading openers (F3, C2-746): they double the preposition
inside frames that supply their own 'om' and carry no topical content."""


def topic_of(heading: str) -> str | None:
    """Heading title as a question topic; None when unusable.

    Markdown links collapse to their link text (F2, C8-518): a rendered
    heading like ``[(EF) nr. 124/2009](eu/32009r0124)`` must never leak a
    link target into a question. Generic 'om'-phrase openers are stripped
    (F3); the minimum-length rule applies to the stripped topic."""
    topic = _MARKDOWN_LINK.sub(r"\1", _HEADING_PREFIX.sub("", heading)).strip().rstrip(".")
    for prefix in _GENERIC_TOPIC_PREFIXES:
        if topic.casefold().startswith(prefix):
            topic = topic[len(prefix) :]
            break
    if len(topic) < _MIN_TOPIC_CHARS or "opphevet" in topic.casefold():
        return None
    return topic[0].lower() + topic[1:]


def is_usable_topic(topic: str, *, strict: bool) -> bool:
    """Whether a heading topic can anchor a question (RC4 filter).

    Meta/structural topics never qualify. In ``strict`` mode (discovery
    C2/C8, premise C6, fabricated C7) a topic must also carry at least
    three words — a one/two-word topic rarely identifies a provision.
    C1 stays unfiltered by owner ruling: with the act named, even a
    structural topic is answerable (every reviewed C1 was kept).
    """
    if META_TOPIC.search(topic):
        return False
    return not (strict and len(topic.split()) <= 2)  # noqa: PLR2004 — frozen rule


def trap_has_sibling(existing_ids: set[str], trap: str) -> bool:
    """True when the corpus contains a near-miss sibling of ``trap`` (RC7).

    ``§ 1`` claimed while ``§ 1-1`` exists (or ``§ 1 a``/``1a``) is not a
    fair non-existence trap — the citation reads as a sloppy reference to
    the sibling rather than an invention.
    """
    pattern = re.compile(re.escape(trap) + r"(-.+|\s?[a-zæøå])$")
    return any(pattern.fullmatch(existing) for existing in existing_ids)


def parse_ministry(markdown: str) -> str | None:
    match = _MINISTRY_LINE.search(markdown)
    return match.group(1).strip() if match else None


def difficulty_for(section_count: int) -> str:
    if section_count <= _EASY_MAX_SECTIONS:
        return "easy"
    if section_count <= _MEDIUM_MAX_SECTIONS:
        return "medium"
    return "hard"


def section_shape(section_id: str) -> str:
    if re.fullmatch(r"\d+", section_id):
        return "plain"
    if re.fullmatch(r"\d+-\d+", section_id):
        return "hyphen"
    if "." in section_id:
        return "dotted"
    if re.search(r"[a-zæøå]", section_id, re.IGNORECASE):
        return "letter"
    return "other"


class CorpusSampler:
    """Seeded, deterministic iteration over the pinned corpus."""

    def __init__(self, reader: CorpusReader, seed: int = GENERATION_SEED_DEFAULT) -> None:
        self._reader = reader
        self._seed = seed

    def shuffled_current_doc_ids(self) -> list[str]:
        docs = self._reader.manifest.documents
        ids = sorted(doc_id for doc_id, r in docs.items() if r.status == "current" and r.slug)
        # Non-cryptographic by design: a seeded, reproducible sampling shuffle.
        random.Random(self._seed).shuffle(ids)  # noqa: S311
        return ids

    def act_info(self, doc_id: str) -> ActInfo | None:
        record = self._reader.manifest.documents[doc_id]
        if record.slug is None:
            return None
        try:
            rows = self._reader.list_sections(record.slug)
            markdown_head = self._reader.get_law(record.slug)[:2000]
        except (LovsporError, OSError, UnicodeDecodeError):
            return None  # one unreadable doc must not kill the run; sampler moves on
        sections = [
            SectionInfo(
                section_id=str(row["section_id"]),
                occurrence=int(row["occurrence"]),
                heading=str(row["heading"]),
                kind=str(row["kind"]),
            )
            for row in rows
            if row["kind"] == "section"
        ]
        return ActInfo(
            slug=record.slug,
            doc_id=doc_id,
            doc_type=record.doc_type,
            title=record.title or "",
            display_name=display_name(record),
            ministry=parse_ministry(markdown_head),
            sections=sections,
        )


def trap_section_ids(act: ActInfo) -> list[tuple[str, str]]:
    """(strategy, trap_id) candidates, deterministically ordered, all absent."""
    ids = {s.section_id for s in act.sections}
    found: list[tuple[str, str]] = []
    chapters: dict[int, list[int]] = {}
    for section_id in ids:
        match = _CHAPTER_ID.fullmatch(section_id)
        if match:
            chapters.setdefault(int(match.group(1)), []).append(int(match.group(2)))
    found.extend(_chapter_traps(chapters, ids))
    found.extend(_suffix_traps(ids))
    found.extend(_flat_traps(ids))
    return [
        (strategy, trap)
        for strategy, trap in found
        if trap not in ids and not trap_has_sibling(ids, trap)
    ]


def _chapter_traps(chapters: dict[int, list[int]], ids: set[str]) -> list[tuple[str, str]]:
    traps: list[tuple[str, str]] = []
    for chapter in sorted(chapters):
        numbers = sorted(chapters[chapter])
        gaps = [n for n in range(numbers[0], numbers[-1]) if n not in numbers]
        if gaps and f"{chapter}-{gaps[0]}" not in ids:
            traps.append(("adjacent-gap", f"{chapter}-{gaps[0]}"))
        traps.append(("chapter-overrun", f"{chapter}-{numbers[-1] + 1}"))
    return traps


def _suffix_traps(ids: set[str]) -> list[tuple[str, str]]:
    return [
        ("letter-suffix", f"{section_id}a")
        for section_id in sorted(ids)
        if _PLAIN_ID.fullmatch(section_id) and f"{section_id}a" not in ids
    ]


def _flat_traps(ids: set[str]) -> list[tuple[str, str]]:
    plain = sorted(int(i) for i in ids if re.fullmatch(r"\d+", i))
    return [("flat-overrun", str(plain[-1] + 1))] if plain else []


_ABBREVIATIONS_BEFORE_PERIOD = frozenset(
    {"jf", "bl.a", "f.eks", "nr", "pkt", "mv", "m.v", "m.m", "mfl", "evt", "ca", "mm", "kap", "s"},
)


_NUMERIC_BEFORE_PERIOD = re.compile(r"\d+(\.\d+)*")


def _is_sentence_boundary(text: str, dot: int) -> bool:
    """Whether ``text[dot] == '.'`` genuinely ends a sentence (F2, C7-516).

    Operates on the NORMALIZED (casefolded) quote domain, so capitalization
    carries no signal; instead the period must not belong to a Norwegian
    abbreviation (``jf.``, ``bl.a.``) or an ordinal/section number
    (``§ 2.``, ``3.1.``) — both cut mid-clause."""
    if dot + 1 >= len(text) or text[dot + 1] != " ":
        return False
    word = text[:dot].rsplit(" ", 1)[-1].lstrip("(«").casefold()
    if word in _ABBREVIATIONS_BEFORE_PERIOD:
        return False
    return _NUMERIC_BEFORE_PERIOD.fullmatch(word) is None


class QuoteSpan(BaseModel, frozen=True):
    """Normalized-domain slice plus its original-cased presentation (F3)."""

    start: int
    end: int
    text: str  # normalized slice — the hash/verify domain
    display: str  # source spelling — the presentation domain


def quote_span(reader: CorpusReader, slug: str, section_id: str) -> QuoteSpan | None:
    """Deterministic sentence-bounded span in the normalized section body.

    Starts after the heading sentence and ends at a validated SENTENCE
    boundary (v2 RC6; v3 F2): a quote cut mid-sentence or at an
    abbreviation period reads as corrupted, and any markdown-link residue
    in the span disqualifies the section as quote material. Returns None
    when the section has no clean, complete sentence of quotable length.
    """
    body = str(reader.get_section(slug, section_id)["body"])
    normalized = normalize_quote_text(body)
    boundaries = [
        i
        for i in range(len(normalized) - 1)
        if normalized[i] == "." and _is_sentence_boundary(normalized, i)
    ]
    start = boundaries[0] + 2 if boundaries else 0
    end: int | None = next(
        (i + 1 for i in reversed(boundaries) if start < i < start + _MAX_QUOTE_CHARS),
        None,
    )
    tail_fits = normalized.endswith(".") and 0 < len(normalized) - start <= _MAX_QUOTE_CHARS
    if (end is None or end - start < _MIN_QUOTE_CHARS) and tail_fits:
        end = len(normalized)  # extend to the section's final sentence
    return _span_result(body, normalized, start, end) if end is not None else None


def _span_result(body: str, normalized: str, start: int, end: int) -> QuoteSpan | None:
    # The hash is computed over normalized[start:end] EXACTLY as the
    # materializer will slice it — the span is a slice, never a text copy.
    text = normalized[start:end]
    if len(text) < _MIN_QUOTE_CHARS:
        return None
    if _MARKDOWN_LINK.search(text) or "](" in text:
        return None  # link residue only; plain [bracketed] statutory text quotes fine
    display = display_span_text(body, (start, end))
    if display is None or not display.lstrip("«([")[:1].isupper():
        # F3 (C7-710/716/731/737): the casefolded domain hides casing, so a
        # span whose SOURCE text starts lowercase — mid-sentence material —
        # only shows up here; such sections are not quote material.
        return None
    return QuoteSpan(start=start, end=end, text=text, display=display)


_QUOTE_MUTATIONS: tuple[tuple[str, str], ...] = (
    (" skal ", " kan "),
    (" kan ", " skal "),
    (" ikke ", " "),
    (" og ", " eller "),
    (" eller ", " og "),
)

_MUTATION_TAIL_GUARD = 15
"""A mutation this close to the end of the quote produces a visibly ragged
tail (owner finding, RC6); mutations must sit inside the sentence body."""


def mutate_quote(text: str) -> str | None:
    """Deterministic subtle modification; None when no mutation applies.

    v2 (RC6): the mutation must land inside the body of the quote — never
    within the final ``_MUTATION_TAIL_GUARD`` characters — so the result
    still reads as plausible statutory prose and denying it requires an
    actual source comparison.
    """
    limit = len(text) - _MUTATION_TAIL_GUARD
    for needle, replacement in _QUOTE_MUTATIONS:
        position = text.find(needle)
        if 0 <= position < limit:
            mutated = text[:position] + replacement + text[position + len(needle) :]
            if mutated != text:
                return mutated
    return None


def topic_census(reader: CorpusReader) -> Counter[str]:
    """Corpus-wide count of casefolded heading topics (F2, C2 drops).

    A discovery question is deterministic only when its topic maps to one
    provision in the whole corpus — headings like 'Søknad og utbetaling'
    repeat across grant schemes, and nothing in a C2 question names the
    act. Counted over every readable current document, same skip rules as
    ``scan_duplicate_ids``.
    """
    counts: Counter[str] = Counter()
    for _doc_id, record in sorted(reader.manifest.documents.items()):
        if record.status != "current" or record.slug is None:
            continue
        try:
            rows = reader.list_sections(record.slug)
        except (LovsporError, OSError, UnicodeDecodeError):
            continue  # the census reports what it can read
        act_topics = {
            topic.casefold()
            for row in rows
            # kind mirrors ActInfo.topic_sections: C2 draws only from §
            # sections, so a block heading must not poison the census
            if row["kind"] == "section" and (topic := topic_of(str(row["heading"]))) is not None
        }
        counts.update(act_topics)  # one count per act: cross-act ambiguity only
    return counts


def oracle_occurrences(reader: CorpusReader, slug: str, section_id: str) -> list[int]:
    """C5 oracle: sorted occurrences of a section id that are provisions.

    Veileder-layer rows are commentary echoes of the act's sections, never
    provisions, so they carry no occurrence the oracle acknowledges (RC3).
    Vedlegg rows count: a normative appendix with its own ``§`` numbering
    is exactly the real ambiguity C5 encodes (owner ruling, Stage 3.6).
    """
    return sorted(
        int(row["occurrence"])
        for row in reader.list_sections(slug)
        if str(row["section_id"]) == section_id and row["layer"] != "veileder"
    )


def scan_duplicate_ids(reader: CorpusReader) -> list[dict[str, object]]:
    """Full-corpus duplicate-section-id scan — the real C5 population.

    Layer-aware since the RC3 parser fix: only provision rows (``main`` and
    ``vedlegg`` layers) count towards duplication; veileder echoes do not.
    """
    findings: list[dict[str, object]] = []
    for doc_id, record in sorted(reader.manifest.documents.items()):
        if record.status != "current" or record.slug is None:
            continue
        try:
            rows = reader.list_sections(record.slug)
        except (LovsporError, OSError, UnicodeDecodeError):
            continue  # the scan reports what it can read
        counts: dict[str, int] = {}
        for row in rows:
            if row["kind"] == "section" and row["layer"] != "veileder":
                counts[str(row["section_id"])] = counts.get(str(row["section_id"]), 0) + 1
        duplicates = {sid: n for sid, n in counts.items() if n > 1}
        if duplicates:
            findings.append({"slug": record.slug, "doc_id": doc_id, "duplicates": duplicates})
    return findings


def base_case(pin: CorpusPin, provenance: dict[str, str | None]) -> dict[str, object]:
    """Shared skeleton every builder starts from (no case_id yet)."""
    return {
        "llhb_version": "1.0",
        "language": "nb",
        "expected_act_slug": None,
        "expected_section_id": None,
        "expected_occurrence": None,
        "valid_occurrences": None,
        "claimed_act_slug": None,
        "claimed_section_id": None,
        "citation_exists": None,
        "quote_ref": None,
        "fabricated_quote_text": None,
        "corpus_pin": {
            "lovverk_commit": pin.lovverk_commit,
            "manifest_generated_at": pin.manifest_generated_at.isoformat(),
        },
        "provenance": dict(provenance),
        "validation": {},
    }


_FABRICATED_SUFFIX = "gjelder ubetinget og kan ikke fravikes ved avtale, uansett omstendighetene."


def fabricated_quote_for(topic: str) -> str:
    """A legal-sounding sentence that exists in no statute (template-built).

    The opener composes with the topic's own shape (F2, C7-526): 'Retten
    til krav til søknad' is broken Norwegian a model denies unread."""
    if topic.startswith("krav til "):
        return f"Kravene til {topic.removeprefix('krav til ')} {_FABRICATED_SUFFIX}"
    if topic.startswith("plikt til "):
        return f"Plikten til {topic.removeprefix('plikt til ')} {_FABRICATED_SUFFIX}"
    return f"Retten til {topic} {_FABRICATED_SUFFIX}"


def quote_ref_fields(
    slug: str,
    section_id: str,
    span: QuoteSpan,
) -> dict[str, object]:
    return {
        "slug": slug,
        "section_id": section_id,
        "occurrence": None,
        "char_span": [span.start, span.end],
        "sha256_normalized": quote_sha256(span.text),
    }
