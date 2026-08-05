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

from pydantic import BaseModel

from lovspor.errors import LovsporError
from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.quotes import normalize_quote_text, quote_sha256
from lovspor.mcp import CorpusReader
from lovspor.storage.manifest import ManifestRecord

GENERATION_SEED_DEFAULT = 20260805

TOPIC_FILTER_VERSION = "llhb-topic-filter-v1"
"""Freeze-surface version of the meta-topic lexicon and usability rule."""

META_TOPIC = re.compile(
    r"virkeområde|verkeområde|definisjon|ikrafttred|ikraftset|iverkset|overgangs"
    r"|formål|innledende|alminnelige|avsluttende|sluttbestemmelser|fellesbestemmelser"
    r"|oppheving|endringer i andre|generelle bestemmelser",
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
    return record.title or record.slug or ""


def topic_of(heading: str) -> str | None:
    """Heading title as a question topic; None when unusable."""
    topic = _HEADING_PREFIX.sub("", heading).strip().rstrip(".")
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


def quote_span(reader: CorpusReader, slug: str, section_id: str) -> tuple[int, int, str] | None:
    """Deterministic (start, end, text) span in the normalized section body.

    Starts after the heading sentence and ends at a SENTENCE boundary
    (v2, RC6): a quote cut mid-sentence reads as corrupted and lets a
    model deny it without consulting the source. Returns None when the
    section has no complete sentence of quotable length.
    """
    section = reader.get_section(slug, section_id)
    normalized = normalize_quote_text(str(section["body"]))
    dot = normalized.find(". ")
    start = dot + 2 if dot != -1 else 0
    window = normalized[start : start + _MAX_QUOTE_CHARS]
    boundary = window.rfind(". ")
    if boundary == -1 and normalized.endswith(".") and len(normalized) - start <= _MAX_QUOTE_CHARS:
        boundary = len(normalized) - start - 1  # last sentence of the section
    if boundary < _MIN_QUOTE_CHARS:
        return None
    end = start + boundary + 1  # include the closing period
    while start < end and normalized[start] == " ":
        start += 1
    # The hash is computed over normalized[start:end] EXACTLY as the
    # materializer will slice it — the span is trimmed, never a text copy.
    text = normalized[start:end]
    if len(text) < _MIN_QUOTE_CHARS:
        return None
    return start, end, text


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


def fabricated_quote_for(topic: str) -> str:
    """A legal-sounding sentence that exists in no statute (template-built)."""
    return (
        f"Retten til {topic} gjelder ubetinget og kan ikke fravikes ved avtale, "
        f"uansett omstendighetene."
    )


def quote_ref_fields(
    slug: str,
    section_id: str,
    span: tuple[int, int, str],
) -> dict[str, object]:
    start, end, text = span
    return {
        "slug": slug,
        "section_id": section_id,
        "occurrence": None,
        "char_span": [start, end],
        "sha256_normalized": quote_sha256(text),
    }
