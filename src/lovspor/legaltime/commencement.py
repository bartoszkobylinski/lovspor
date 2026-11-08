"""When an announced amendment took legal effect, proven or left open.

Evidence, all of it from the publicData archives: the amending law's own
``dateInForce`` header, its commencement clause (``Loven trer i kraft
straks.`` with any ``likevel`` exception), and the commencement decisions
(``Ikraftsetting av lov …``, ``Delt ikrafttredelse …``) whose title cites the
law, folded in publication order. No precedence rule is asserted between
them: sources that agree are all cited, sources that disagree leave the
operation ``unresolved``. A date the evidence does not state is never derived:
the operation stays ``pending`` (delegated, no decision found) or
``unresolved`` (evidence present but outside the grammar or contradictory).
"""

import re
from datetime import date, datetime
from hashlib import sha256
from typing import Literal

from pydantic import Field

from lovspor.legaltime.document import Document, Frozen, Node, list_statements
from lovspor.legaltime.instructions import _INTRO, Operation, classify
from lovspor.legaltime.scopes import (
    _PART_LIST,
    _RESERVATION,
    _ROMAN,
    _SEC,
    _STRAKS,
    _named_in,
    _part_labels,
    _partial_scopes,
    _scopes_for,
)
from lovspor.legaltime.text import (
    act_refid,
    parse_date,
    section_key,
    section_key_of,
    split_sentences,
)
from lovspor.legaltime.units import ChapterOf

TimingStatus = Literal["dated", "pending", "unresolved"]
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PENDING_WORDS = re.compile(
    r"(?:Kongen|Departementet)\s+(?:bestemmer|fastset|fastsett|fastsetter|avgjer)", re.I
)
_PENDING_HEADER = re.compile(
    r"(?:Kongen|Departementet)\s+(?:bestemmer|fastset|fastsett|fastsetter|avgjer)\.?", re.I
)
_NUMBERED = re.compile(r"^\(\d+\)\s*")
_DELEGATES = re.compile(r"^(?:Kongen|Departementet)\b", re.I)
_CONDITIONAL = re.compile(
    r"^(?:Når|Frå\s+den\s+tid|Fra\s+den\s+tid|Fra\s+det\s+tidspunkt|Frå\s+det\s+tidspunktet)\b.*"
    r"\b(?:trer\s+i\s+kraft|tek\s+til\s+å\s+gjelde|tar\s+til\s+å\s+gjelde|gjelder|gjeld)$",
    re.I,
)
CONDITIONAL = "commencement conditional on an event outside the archive"
_DECISION_TITLE = re.compile(
    r"^(?:(?:Forskrift|Vedtak)\s+om\s+)?(?:Delt\s+|Delvis\s+|Meddelelse\s+om\s+)?"
    r"(?:Ikraftsetting|Ikraftsetjing|Ikrafttredelse|Ikrafttreden|"
    r"Ikraftsetjinga|Iverksetjing|Iverksetting|Ikraftsettelse)\b",
    re.I,
)
_PARTIAL_TITLE = re.compile(r"^(?:(?:Forskrift|Vedtak)\s+om\s+)?(?:Delt|Delvis)\b", re.I)
_VERB = (
    r"(?:trer\s+i\s+kraft|gjeld\s+frå|gjelder\s+fra|tek\s+til\s+å\s+gjelde|tar\s+til\s+å\s+gjelde)"
)
_LAW_VERB = rf"(?:{_VERB}|(?:blir|vert)\s+sett\s+i\s+verk|trer\s+(?:ellers|elles)\s+i\s+kraft)"
_DEFAULT_CLAUSE = re.compile(
    rf"^(?:For\s+øvrig\s+|Elles\s+|Ellers\s+)?(?:(?:Denne\s+)?(?:Loven|Lova|Endringslova|Endringsloven)"
    rf"(?:\s+her)?\s+{_LAW_VERB}"
    rf"|(?:gjelder\s+loven|gjeld\s+lova)\s+(?:fra|frå)|trer\s+(?:loven|lova)\s+i\s+kraft"
    rf"|(?:tek|tar)\s+(?:lova|loven)\s+til\s+å\s+gjelde)\b",
    re.I,
)
_PART_VERB = (
    r"(?:trer\s+(?:likevel\s+)?i\s+kraft|gjeld(?:er)?\s+fr[aå]|(?:blir|vert)\s+sett\s+i\s+verk"
    r"|(?:tek|tar)\s+til\s+å\s+gjelde)"
)
_PART_CLAUSE = re.compile(
    rf"^(?:(?:Endringene|Endringane|Endringen|Endringa)\s+(?:under|i)\s+|(?:Loven|Lova|Lovens|Lovas|Romertall)\s+)?"
    rf"(?:del\s+)?{_PART_LIST}(?:\s+om\s+endring\s+av\s+[^,]*?)?\s+{_PART_VERB}\s+(?P<when>.+?)\.?$",
    re.I,
)
_SECNUM = r"\d+(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"
_SECTIONS_UNDER_PART = re.compile(
    rf"^(?:Endringen|Endringene|Endringane|Endringa|Opphevingen|Opphevinga|Opphevelsen)\s+(?:av|i)\s+\S+\s+"
    rf"§§?\s*(?P<secs>{_SECNUM}(?:\s*,\s*(?:§\s*)?{_SECNUM})*(?:\s+og\s+(?:§\s*)?{_SECNUM})?)\s+under\s+del\s+"
    rf"(?P<part>{_ROMAN})\s+{_PART_VERB}\s+(?P<when>.+?)\.?$",
    re.I,
)
_CITED = re.compile(
    r"\b[\wæøåÆØÅ-]*(?:lov|lova|loven|lovvedtak|forskrift)\s+(?:av\s+)?\d{1,2}\.\s*\w+\s+\d{4}(?:\s+nr\.\s*\d+)?",
    re.I,
)  # a cited act or regulation by date, also under its short title (``straffeloven 22. mai 1902``)
_COMMENCEMENT_PHRASE = re.compile(rf"{_VERB}|\bi\s+kraft\b|\bvirkning\s+fr[aå]\b", re.I)
_EXCEPTION_NEW_SECTION = re.compile(
    r"^Ny\s+(?P<sec>§\s*\d+(?:[A-Z]|\s[A-Z](?=-))?(?:-\d+)?(?:\s*[a-zæøå](?![a-zæøå]))?)(?:\s+i\s+\S+)?\s+trer\s+likevel\s+i\s+kraft\b",
    re.I,
)
_EXCEPTION_NAMED_SECTION = re.compile(
    rf"^(?:Endringene|Endringane|Endringa|Endringen)\s+i\s+\S+\s+(?P<sec>{_SEC})\s+i\s+del\s+"
    r"(?P<part>[IVXLC]+)\s+i\s+(?:loven|lova)(?:\s+her)?\s+trer\s+(?:likevel\s+)?i\s+kraft\s+(?P<when>.+)$",
    re.I,
)
_EXCEPTION_PART = re.compile(
    r"^(?:Endringen|Endringene|Endringane|Endringa)\s+(?:av|i)\s+.*?\bi\s+del\s+(?P<part>[IVXLC]+)\s+"
    r"i\s+(?:loven|lova)\s+her\s+trer\s+(?:likevel\s+)?i\s+kraft\s+(?P<when>.+)$",
    re.I,
)
_INFORMATIONAL = re.compile(
    r"^(?:Kongen\s+kan|Departementet\s+kan|De\s+enkelte|Dei\s+enkelte|Fra\s+den\s+tid|"
    r"Frå\s+den\s+tid|Fra\s+samme\s+tid|Frå\s+same\s+tid|Er\s|Dersom\s|Hvis\s|Når\s|Fristen\s|"
    r"Delt\s+ikraft|Delvis\s+ikraft|"
    r"(?:De|Dei)\s+(?:ulike|enkelte|forskjellige)\s+\S+(?:\s+i\s+lov\w*)?\s+kan\s|Det\s+kan\s+fastsett|"
    r"(?:Bestemmelsene|Føresegnene|Reglene|Reglane)\s+kan\s|"
    r"Forskrifter\s+(?:gitt|gjevne|fastsatt|fastsette)\s)",
    re.I,
)
_REFERENCE = re.compile(
    r"\b(?:etter\s+at|når|før|innen|fra\s+det\s+tidspunkt(?:et)?|på\s+det\s+tidspunkt(?:et)?|frå\s+det\s+tidspunktet)"
    r"\s+(?:denne\s+)?(?:loven|lova|lov|lovendringen|lovendringa|lovendringene|endringsloven|endringslova)"
    r"(?:\s+her)?\s+(?:har\s+)?(?:trer|trådte|trådt|tek|tok|teke|tar|tok)\s+(?:i\s+kraft|til\s+å\s+gjelde)",
    re.I,
)  # a reference to the commencement, not a statement of it
_TRANSITIONAL = re.compile(r"\bgjeld(?:er)?\s+(?:bare|berre|også|òg|først|fyrst|for)\b", re.I)
_CONTINUES = re.compile(
    r"\b(?:held|holder|heldt)\s+fram\s+å\s+gjelde\b|\bfortsetter\s+å\s+gjelde\b", re.I
)


class Decisions(Frozen):
    """The commencement decisions in reach, with the act's chapter map for placing scopes."""

    documents: tuple[Document, ...]
    chapter_of: dict[str, str] = Field(default_factory=dict)  # section key → chapter of the act


DecisionsLike = tuple[Document, ...] | Decisions


def _decisions(decisions: DecisionsLike) -> Decisions:
    return decisions if isinstance(decisions, Decisions) else Decisions(documents=decisions)


class Proof(Frozen):
    """Where a timing was read: the member, and the sha256 of the sentence it came from.

    The sentence itself stays in the archive; the report carries its digest, so the
    proof is checkable against the member without moving announced text into Git.
    """

    refid: str
    member: str
    sha256: str
    quote_sha256: str


class Timing(Frozen):
    status: TimingStatus
    effective: date | None = None
    known_from: date | None = None
    retroactive: bool = False
    reason: str = ""
    proofs: tuple[Proof, ...] = ()

    @property
    def dated_on(self) -> date | None:
        """The effective date when the timing is proven, else None."""
        return self.effective if self.status == "dated" else None


def _proof(document: Document, quote: str) -> Proof:
    return Proof(
        refid=document.header.refid, member=document.member, sha256=document.sha256,
        quote_sha256=sha256(quote.encode("utf-8")).hexdigest(),
    )  # fmt: skip


def _known_from(document: Document) -> date | None:
    published = document.header.published_at
    return published.date() if published else None


def _dated(document: Document, when: date, quote: str) -> Timing:
    known = _known_from(document)
    return Timing(
        status="dated",
        effective=when,
        known_from=known,
        retroactive=known is not None and when < known,
        proofs=(_proof(document, quote),),
    )


def _pending(document: Document, quote: str) -> Timing:
    return Timing(
        status="pending", known_from=_known_from(document), proofs=(_proof(document, quote),)
    )


def _unresolved(document: Document, reason: str, quote: str) -> Timing:
    return Timing(status="unresolved", reason=reason, proofs=(_proof(document, quote),))


def header_timing(document: Document) -> Timing:
    """The timing the ``dateInForce`` header alone supports, for every operation."""
    raw = document.header.date_in_force
    if _ISO.match(raw):
        return _dated(document, date.fromisoformat(raw), f"dateInForce: {raw}")
    if _PENDING_HEADER.fullmatch(raw.strip()):
        return _pending(document, f"dateInForce: {raw}")
    return _unresolved(document, "dateInForce outside the grammar", f"dateInForce: {raw}")


def _sentences(clauses: tuple[str, ...]) -> list[str]:
    """Clause sentences with a leading ``(1)`` ledd number removed."""
    return [_NUMBERED.sub("", s) for clause in clauses for s in split_sentences(clause)]


def _header_date(document: Document) -> date | None:
    first = re.search(r"\d{4}-\d{2}-\d{2}", document.header.date_in_force)
    return date.fromisoformat(first.group(0)) if first else None


def _dated_or_pending(document: Document, sentence: str) -> Timing:
    """A default clause's own date: ``straks`` means the header's date, else the date it names."""
    if _PENDING_WORDS.search(sentence):
        return _pending(document, sentence)
    when = _clause_date(document, sentence)
    if when is None:
        return _unresolved(document, "commencement clause outside the grammar", sentence)
    return _dated(document, when, sentence)


def _clause_date(document: Document, sentence: str) -> date | None:
    """``straks`` is the header's date and an explicit date is itself; both at once is neither."""
    explicit = parse_date(_CITED.sub("", sentence))
    if _STRAKS.search(sentence):
        return None if explicit is not None else _header_date(document)
    return explicit


def _default_timing(document: Document, sentence: str) -> Timing | None:
    if _DEFAULT_CLAUSE.match(sentence) is None:
        return None
    if _RESERVATION.search(sentence):
        return _unresolved(document, "default clause carries a reservation", sentence)
    return _dated_or_pending(document, sentence)


def _exception_scope(sentence: str) -> tuple[str, str] | None:
    """``("section", "§99a")``, ``("part", "V")`` or ``("part-section", "I/§5")``, else None."""
    new_section = _EXCEPTION_NEW_SECTION.match(sentence)
    if new_section is not None:
        return ("section", section_key_of(new_section.group("sec")))
    named = _EXCEPTION_NAMED_SECTION.match(sentence)
    if named is not None:
        return ("part-section", f"{named.group('part').upper()}/{section_key(named.group('sec'))}")
    part = _EXCEPTION_PART.match(sentence)
    return ("part", part.group("part").upper()) if part else None


def _informational(sentence: str) -> bool:
    """Transitional or conditional prose that states no commencement of its own.

    A sentence that names a date but no commencement phrase is not informational:
    it is a commencement statement the grammar does not know, and must refuse.
    """
    sentence = _NUMBERED.sub("", sentence)
    without_citations = _CITED.sub("", sentence)
    if _INFORMATIONAL.match(sentence):
        return True
    if _DELEGATES.match(sentence) and _COMMENCEMENT_PHRASE.search(sentence):
        return False  # "Kongen bestemmer når loven trer i kraft": a delegation, however phrased
    if _PENDING_WORDS.search(sentence) and _COMMENCEMENT_PHRASE.search(sentence):
        return False
    if _CONTINUES.search(sentence) or _TRANSITIONAL.search(sentence):
        return parse_date(without_citations) is None
    stated = _REFERENCE.sub("", sentence)  # what is left once the references are gone
    return _COMMENCEMENT_PHRASE.search(stated) is None and parse_date(without_citations) is None


def _exception_timing(document: Document, sentence: str) -> Timing:
    if _PENDING_WORDS.search(sentence):
        return _pending(document, sentence)
    when = _clause_date(document, sentence)
    if when is not None:
        return _dated(document, when, sentence)
    return _unresolved(document, "exception outside the grammar", sentence)


def _clause_scopes(sentence: str) -> list[tuple[str, str | None]] | None:
    """The part or part-section keys one clause sentence dates, or None when it names none."""
    parts = _PART_CLAUSE.match(sentence)
    if parts is not None:
        return [("part", label) for label in _part_labels(parts.group("parts"))]
    under = _SECTIONS_UNDER_PART.match(sentence)
    if under is not None:
        part = under.group("part").upper()
        keys = [section_key_of(f"§ {m}") for m in re.findall(_SECNUM, under.group("secs"))]
        return [("part-section", f"{part}/{key}") for key in keys]
    scope = _exception_scope(sentence)
    return [scope] if scope is not None else None


class _ClauseReading:
    def __init__(self) -> None:
        self.default: Timing | None = None
        self.exceptions: dict[tuple[str, str | None], Timing] = {}
        self.failure: Timing | None = None

    def read(self, document: Document, sentence: str) -> None:
        if _INTRO.match(sentence):
            return  # an amendment intro sharing the commencement provision, not a clause
        if classify(sentence)[0] != "unsupported":
            return  # an amendment instruction there is not a clause either
        found = _default_timing(document, sentence)
        if found is not None and self.default is not None:
            self.failure = _unresolved(document, "two default commencement clauses", sentence)
        elif found is not None:
            self.default = found
        elif not _informational(sentence):
            scopes = _clause_scopes(sentence)
            if scopes is None:
                self.failure = _unresolved(
                    document, "commencement sentence outside the grammar", sentence
                )
            else:
                timing = _exception_timing(document, sentence)
                self.exceptions.update(dict.fromkeys(scopes, timing))


def clause_timings(
    document: Document, clauses: tuple[str, ...], operations: tuple[Operation, ...]
) -> dict[str, Timing]:
    """Per-operation timing from the law's own clause; header timing when there is none."""
    reading = _ClauseReading()
    for sentence in _sentences(clauses):
        reading.read(document, sentence)
    if reading.failure is not None:
        return dict.fromkeys((op.id for op in operations), reading.failure)
    return _assign(operations, reading.default or header_timing(document), reading.exceptions)


def _scoped(
    operation: Operation, exceptions: dict[tuple[str, str | None], Timing]
) -> Timing | None:
    section = ("section", operation.address.section)
    if operation.kind == "insert_section" and section in exceptions:
        return exceptions[section]
    located = ("part-section", f"{operation.part.upper()}/{operation.address.section}")
    return exceptions.get(located) or exceptions.get(("part", operation.part.upper()))


def _assign(
    operations: tuple[Operation, ...],
    default: Timing,
    exceptions: dict[tuple[str, str | None], Timing],
) -> dict[str, Timing]:
    return {op.id: _scoped(op, exceptions) or default for op in operations}


def is_decision_for(document: Document, law_refid: str) -> bool:
    """A commencement decision whose title, or one of whose body block titles, cites this law."""
    title = document.header.title
    if not _DECISION_TITLE.match(title):
        return False
    if act_refid(title) == law_refid:
        return True
    return any(
        block_title is not None and act_refid(block_title) == law_refid
        for block_title, _ in _blocks(document)
    )


def _statements(node: Node) -> list[str]:
    """A node's sentences; a lead-in with a list is one statement per item (``list_statements``)."""
    listed = list_statements(node.ledd) if node.ledd is not None else None
    return listed if listed is not None else split_sentences(node.text)


def _blocks(decision: Document) -> list[tuple[str | None, list[str]]]:
    """The decision's sentences grouped under the title sentences that open each law's block."""
    blocks: list[tuple[str | None, list[str]]] = [(None, [])]
    for part in decision.parts:
        for node in part.nodes:
            for sentence in _statements(node):
                if _DECISION_TITLE.match(sentence) and act_refid(sentence) is not None:
                    blocks.append((sentence, []))
                elif not _informational(sentence):
                    blocks[-1][1].append(sentence)
    return blocks


class _Block(Frozen):
    """One law's share of a decision: its title sentence, its sentences, and whether it is alone."""

    title: str
    sentences: tuple[str, ...]
    shared: bool  # the decision has blocks for other laws too


def _block_for(decision: Document, law: str) -> _Block | None:
    """The block for ``law``; None when several blocks exist and none is its."""
    blocks = _blocks(decision)
    titled = [(title, body) for title, body in blocks if title is not None]
    for title, body in titled:
        if act_refid(title) == law:
            return _Block(title=title, sentences=tuple(body), shared=len(titled) > 1)
    if titled:
        return None  # every titled block is another law's
    body = [sentence for _, sentences in blocks for sentence in sentences]
    return _Block(title=decision.header.title, sentences=tuple(body), shared=False)


def _own_date(decision: Document) -> date | None:
    """The day of the resolution itself, from its refid (``forskrift/2010-01-08-3``)."""
    match = re.search(r"/(\d{4}-\d{2}-\d{2})", decision.header.refid)
    return date.fromisoformat(match.group(1)) if match else None


def _block_date(decision: Document, sentences: list[str]) -> date | None | str:
    """The one date a full block states, None for none, or the reason it cannot be read."""
    stated: set[date] = set()
    for sentence in sentences:
        if _RESERVATION.search(sentence):
            return "full decision body carries an exception"
        explicit = _stated_date(sentence)
        if _STRAKS.search(sentence) and explicit is not None:
            return "full decision body names several dates"
        when = _own_date(decision) if _STRAKS.search(sentence) else explicit
        if when is not None:
            stated.add(when)
    if len(stated) > 1:
        return "full decision body names several dates"
    return next(iter(stated), None)


def _stated_date(sentence: str) -> date | None:
    """The date a sentence puts after its commencement phrase; a cited act's date never counts."""
    phrase = _COMMENCEMENT_PHRASE.search(sentence)
    if phrase is None:
        return None
    return parse_date(_CITED.sub("", sentence[phrase.end() :]))


def _decision_dated(timing: Timing, law: str) -> bool:
    """Dated by an earlier decision, so a later partial decision can narrow it."""
    return timing.status == "dated" and bool(timing.proofs) and timing.proofs[0].refid != law


def _with_proof(timing: Timing, decision: Document, quote: str) -> Timing:
    return timing.model_copy(update={"proofs": (*timing.proofs, _proof(decision, quote))})


def _unsettled(current: Timing, decision: Document, reason: str) -> Timing:
    return _with_proof(current, decision, decision.header.title).model_copy(
        update={"status": "unresolved", "effective": None, "retroactive": False, "reason": reason}
    )


def _settle(current: Timing, when: date | None, decision: Document, quote: str) -> Timing:
    """Fold one decision's answer for an operation into its timing. None = not covered."""
    if when is None:
        return _unsettled(current, decision, "narrowed by a later partial decision")
    if current.status == "pending":
        dated = _dated(decision, when, quote)
        return dated.model_copy(update={"proofs": (*current.proofs[1:], *dated.proofs)})
    if current.effective == when:
        return _with_proof(current, decision, quote)
    return _unsettled(current, decision, CONFLICT)


CONFLICT = "conflicting commencement decisions"
SEVERAL = 2  # from two decisions on, their order matters


class _Decision:
    """One decision read once, for one law: its block's scopes or date, or why it is unreadable."""

    def __init__(
        self, decision: Document, law: Document, ordered: bool, chapter_of: ChapterOf | None = None
    ) -> None:
        self.document = decision
        self.chapter_of = chapter_of or {}
        self.title = decision.header.title
        self.block_problem: str | None = None
        block = _block_for(decision, law.header.refid)
        scopes = _partial_scopes(list(block.sentences), _own_date(decision)) if block else None
        # A block is partial when its title says so, or when every sentence it has is a scope.
        self.partial = block is not None and (
            bool(_PARTIAL_TITLE.match(self.title))
            or bool(_PARTIAL_TITLE.match(block.title))
            or bool(scopes)
        )
        self.scopes = scopes if self.partial else None
        raw = decision.header.date_in_force
        self.conditional = not self.partial and bool(_CONDITIONAL.match(raw))
        self.full_date = self._full_date(block) if block and not self.partial else None
        self.unreadable = self._unreadable(law, ordered, block)

    def _full_date(self, block: _Block) -> date | None:
        """The header's date when the block agrees with it; the block's own date when shared."""
        header_read = header_timing(self.document)
        header = header_read.dated_on
        stated = _block_date(self.document, list(block.sentences))
        if isinstance(stated, str):
            self.block_problem = stated
            return None
        raw = self.document.header.date_in_force.strip()
        if header_read.status == "unresolved" and not block.shared and raw and not self.conditional:
            self.block_problem = "dateInForce outside the grammar"
            return None
        if block.shared and stated is None:
            self.block_problem = "decision body has no commencement sentence for this law"
            return None
        if header is not None and stated is not None and stated != header:
            self.block_problem = "full decision body disagrees with its header"
            return None
        return header if header is not None and not block.shared else stated

    def _unreadable(self, law: Document, ordered: bool, block: _Block | None) -> str | None:
        if not ordered:
            return "decision without a publication time among several"
        if block is None:
            return "decision body has no commencement sentence for this law"
        return self._partial_unreadable(law) if self.partial else self._full_unreadable()

    def _partial_unreadable(self, law: Document) -> str | None:
        if not self.scopes:
            return "partial decision outside the grammar"
        if not any(_named_in(law, scope) for scope in self.scopes):
            return "partial decision names no part or section of the law"
        return None

    def _full_unreadable(self) -> str | None:
        if self.conditional:
            return None  # the date waits on an event; a later decision may state it
        if self.block_problem is not None:
            return self.block_problem
        if self.full_date is None:
            return "full decision without a readable date"
        return None

    def answer(self, op: Operation) -> tuple[date | None, str] | str:
        """(date, quote) for the operation; (None, title) when not reached; a reason on conflict."""
        if self.scopes is None:
            return self.full_date, self.title
        scopes = _scopes_for(op, self.scopes, self.chapter_of)
        if isinstance(scopes, str):
            return scopes
        if not scopes:
            return None, self.title
        if len({scope.when for scope in scopes}) > 1:
            return CONFLICT
        return scopes[-1].when, scopes[-1].quote


def _fold(current: Timing, read: _Decision, op: Operation, law: str) -> Timing | None:
    """The operation's timing after this decision; None when the decision leaves it alone."""
    if current.status == "unresolved":
        return None
    if read.unreadable is not None:
        return _unsettled(current, read.document, read.unreadable)
    if read.conditional:
        return _conditional(current, read.document)
    answer = read.answer(op)
    if isinstance(answer, str):
        return _unsettled(current, read.document, answer)
    when, quote = answer
    if when is None and not _decision_dated(current, law):
        return None  # a partial decision that does not reach this operation says nothing about it
    return _settle(current, when, read.document, quote)


def _apply_decision(
    read: _Decision, operations: tuple[Operation, ...], timings: dict[str, Timing], law: str
) -> None:
    for op in operations:
        folded = _fold(timings[op.id], read, op, law)
        if folded is not None:
            timings[op.id] = folded


def _citing(law: Document, decisions: DecisionsLike) -> list[_Decision]:
    """The decisions citing the law, in publication order; unordered ones are marked unreadable."""
    context = _decisions(decisions)
    cited = [d for d in context.documents if is_decision_for(d, law.header.refid)]
    ordered = len(cited) < SEVERAL or all(d.header.published_at is not None for d in cited)
    return [
        _Decision(d, law, ordered or d.header.published_at is not None, context.chapter_of)
        for d in sorted(cited, key=lambda d: (d.header.published_at or datetime.min, d.member))
    ]


def resolve_timings(
    law: Document,
    clauses: tuple[str, ...],
    operations: tuple[Operation, ...],
    decisions: DecisionsLike,
) -> dict[str, Timing]:
    """Every operation's timing from the law, its clause, and the decisions citing it."""
    timings = clause_timings(law, clauses, operations)
    for read in _citing(law, decisions):
        _apply_decision(read, operations, timings, law.header.refid)
    return timings


def _fold_baseline(timing: Timing, read: _Decision) -> Timing:
    if read.partial:
        return _unsettled(timing, read.document, "partial commencement of the act itself")
    if read.unreadable is not None:
        return _unsettled(timing, read.document, read.unreadable)
    if read.conditional:
        return _conditional(timing, read.document)
    return _settle(timing, read.full_date, read.document, read.title)


def _conditional(current: Timing, decision: Document) -> Timing:
    """A decision that waits on an event: pending stays pending, with the proof; dated conflicts."""
    if current.status != "pending":
        return _unsettled(current, decision, CONFLICT)
    return _with_proof(current, decision, decision.header.title).model_copy(
        update={"reason": CONDITIONAL}
    )


def baseline_timing(act: Document, decisions: DecisionsLike) -> Timing:
    """When the act itself commenced: its header, folded with every full decision citing it."""
    timing = header_timing(act)
    if timing.status != "pending":
        return timing
    folded: Timing = timing
    for read in _citing(act, decisions):
        if folded.status == "unresolved":
            break
        folded = _fold_baseline(folded, read)
    return folded
