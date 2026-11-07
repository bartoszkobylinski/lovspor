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

from pydantic import model_validator

from lovspor.legaltime.document import Document, Frozen
from lovspor.legaltime.instructions import Operation
from lovspor.legaltime.text import (
    ACT_CITATION,
    act_refid,
    parse_date,
    section_key,
    section_key_of,
    split_sentences,
)

TimingStatus = Literal["dated", "pending", "unresolved"]
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PENDING_WORDS = re.compile(r"Kongen\s+(?:bestemmer|fastset|fastsett)", re.I)
_CONDITIONAL = re.compile(
    r"^(?:Når|Frå\s+den\s+tid|Fra\s+den\s+tid|Fra\s+det\s+tidspunkt|Frå\s+det\s+tidspunktet)\b.*"
    r"\b(?:trer\s+i\s+kraft|tek\s+til\s+å\s+gjelde|tar\s+til\s+å\s+gjelde|gjelder|gjeld)$",
    re.I,
)
CONDITIONAL = "commencement conditional on an event outside the archive"
_DECISION_TITLE = re.compile(
    r"^(?:Delt\s+|Meddelelse\s+om\s+)?(?:Ikraftsetting|Ikraftsetjing|Ikrafttredelse|Ikrafttreden|"
    r"Ikraftsetjinga|Iverksetjing|Iverksetting|Ikraftsettelse)\b",
    re.I,
)
_PARTIAL_TITLE = re.compile(r"^Delt\b", re.I)
_STRAKS = re.compile(r"\b(?:straks|med\s+en\s+gang|omgående)\b", re.I)
_VERB = (
    r"(?:trer\s+i\s+kraft|gjeld\s+frå|gjelder\s+fra|tek\s+til\s+å\s+gjelde|tar\s+til\s+å\s+gjelde)"
)
_DEFAULT_CLAUSE = re.compile(
    rf"^(?:For\s+øvrig\s+|Elles\s+)?(?:(?:Loven|Lova|Endringslova|Endringsloven)\s+{_VERB}"
    rf"|(?:gjelder\s+loven|gjeld\s+lova)\s+(?:fra|frå)|trer\s+loven\s+i\s+kraft)\b",
    re.I,
)
_COMMENCEMENT_PHRASE = re.compile(rf"{_VERB}|\bi\s+kraft\b|\bvirkning\s+fr[aå]\b", re.I)
_RESERVATION = re.compile(r"\b(?:unntatt|bortsett|likevel|med\s+unntak|unnateke|unnatatt)\b", re.I)
_EXCEPTION_NEW_SECTION = re.compile(
    r"^Ny\s+(?P<sec>§\s*\d+(?:-\d+)?(?:\s*[a-zæøå](?![a-zæøå]))?)(?:\s+i\s+\S+)?\s+trer\s+likevel\s+i\s+kraft\b",
    re.I,
)
_SEC = r"§\s*\d+(?:-\d+)?(?:\s*[a-hj-zæøå](?![a-zæøå]))?"
_DELT_SENTENCE = re.compile(
    rf"^(?:Endringsloven|Endringslova)\s+del\s+(?P<part>[IVXLC]+)"
    rf"(?:,\s*(?:ny\s+(?P<sec>{_SEC})\s+i\s+lov|(?:endringene|endringane)\s+i\s+lov)(?P<rest>[^,]*))?"
    r",?\s*trer\s+i\s+kraft\s+(?P<when>.+)$",
    re.I,
)
_SECTION_IN = re.compile(_SEC)
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
    r"Frå\s+den\s+tid|Er\s|Dersom\s|Hvis\s|Når\s|Fristen\s|Delt\s+ikraft)",
    re.I,
)
_CONTINUES = re.compile(
    r"\b(?:held|holder|heldt)\s+fram\s+å\s+gjelde\b|\bfortsetter\s+å\s+gjelde\b", re.I
)
_OWN_SECTIONS = re.compile(
    r"^(?:Paragrafane|Paragrafene|Paragrafar|Paragrafer|Paragraf|§§?)\s+(?P<list>.+?)\s+"
    r"(?:gjeld|gjelder|trer\s+i\s+kraft|tek\s+til\s+å\s+gjelde|tar\s+til\s+å\s+gjelde)\s+"
    r"(?:frå|fra)?\s*(?P<when>\d.+)$",
    re.I,
)
_LISTING_TOKEN = re.compile(
    r"\s*(?:(?P<range>(?P<first>\d+)\s+til\s+(?P<last>\d+))|(?P<one>\d+(?:\s*[a-hj-zæøå](?![a-zæøå]))?)"
    r"|(?P<sep>,|og|§§|§))(?=\s|,|$)"
)


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
    if _PENDING_WORDS.fullmatch(raw):
        return _pending(document, f"dateInForce: {raw}")
    return _unresolved(document, "dateInForce outside the grammar", f"dateInForce: {raw}")


def _sentences(clauses: tuple[str, ...]) -> list[str]:
    return [s for clause in clauses for s in split_sentences(clause)]


def _header_date(document: Document) -> date | None:
    first = re.search(r"\d{4}-\d{2}-\d{2}", document.header.date_in_force)
    return date.fromisoformat(first.group(0)) if first else None


def _dated_or_pending(document: Document, sentence: str) -> Timing:
    """A default clause's own date: ``straks`` means the header's date, else the date it names."""
    if _PENDING_WORDS.search(sentence):
        return _pending(document, sentence)
    when = _header_date(document) if _STRAKS.search(sentence) else parse_date(sentence)
    if when is None:
        return _unresolved(document, "commencement clause outside the grammar", sentence)
    return _dated(document, when, sentence)


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
    without_citations = ACT_CITATION.sub("", sentence)
    if _INFORMATIONAL.match(sentence):
        return True
    if _CONTINUES.search(sentence):
        return parse_date(without_citations) is None
    return _COMMENCEMENT_PHRASE.search(sentence) is None and parse_date(without_citations) is None


def _exception_timing(document: Document, sentence: str) -> Timing:
    if _PENDING_WORDS.search(sentence):
        return _pending(document, sentence)
    if _STRAKS.search(sentence) and (header := _header_date(document)) is not None:
        return _dated(document, header, sentence)
    when = parse_date(sentence)
    if when is not None:
        return _dated(document, when, sentence)
    return _unresolved(document, "exception outside the grammar", sentence)


class _ClauseReading:
    def __init__(self) -> None:
        self.default: Timing | None = None
        self.exceptions: dict[tuple[str, str | None], Timing] = {}
        self.failure: Timing | None = None

    def read(self, document: Document, sentence: str) -> None:
        found = _default_timing(document, sentence)
        if found is not None and self.default is not None:
            self.failure = _unresolved(document, "two default commencement clauses", sentence)
        elif found is not None:
            self.default = found
        elif not _informational(sentence):
            scope = _exception_scope(sentence)
            if scope is None:
                self.failure = _unresolved(
                    document, "commencement sentence outside the grammar", sentence
                )
            else:
                self.exceptions[scope] = _exception_timing(document, sentence)


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
    """A commencement decision whose title cites exactly this law."""
    title = document.header.title
    return bool(_DECISION_TITLE.match(title)) and act_refid(title) == law_refid


class Scope(Frozen):
    """What one sentence of a partial decision puts into force, and when."""

    part: str | None = None
    section: str | None = None
    own_sections: frozenset[str] = frozenset()
    when: date
    quote: str  # the sentence the scope and date were read from

    @model_validator(mode="after")
    def _one_kind(self) -> "Scope":
        if self.own_sections and (self.part is not None or self.section is not None):
            raise ValueError("a scope names either own_sections or a part, never both")
        return self

    def covers(self, operation: Operation) -> bool:
        if self.own_sections:
            return operation.host_section in self.own_sections
        if self.part is None or operation.part.upper() != self.part:
            return False
        return self.section is None or operation.address.section == self.section


def _own_sections(listing: str) -> frozenset[str] | None:
    """``1 til 10 og §§ 12 til 25`` → the act's own section keys; None on any other token."""
    keys: set[str] = set()
    position = 0
    for match in _LISTING_TOKEN.finditer(listing):
        if match.start() != position:
            return None  # a word the listing grammar does not know (``unntatt``, a letter range)
        position = match.end()
        if match.group("range"):
            first, last = int(match.group("first")), int(match.group("last"))
            if last < first:
                return None
            keys.update(f"§{n}" for n in range(first, last + 1))
        elif match.group("one"):
            keys.add(f"§{match.group('one').replace(' ', '')}")
    return frozenset(keys) if position == len(listing) and keys else None


def _scope_of(sentence: str) -> Scope | None:
    if _RESERVATION.search(sentence):
        return None  # an exception the grammar cannot scope: the whole decision is unreadable
    part_match = _DELT_SENTENCE.match(sentence)
    if part_match is not None:
        when = parse_date(part_match.group("when"))
        if when is None:
            return None
        part, section = part_match.group("part").upper(), _named_section(part_match)
        return Scope(part=part, section=section, when=when, quote=sentence)
    own = _OWN_SECTIONS.match(sentence)
    if own is not None:
        when = parse_date(own.group("when"))
        sections = _own_sections(own.group("list"))
        if when is not None and sections is not None:
            return Scope(own_sections=sections, when=when, quote=sentence)
    return None


def _partial_scopes(decision: Document) -> list[Scope] | None:
    """One scope per sentence of a ``Delt`` decision; None when any sentence is foreign."""
    scopes: list[Scope] = []
    for part in decision.parts:
        for node in part.nodes:
            for sentence in split_sentences(node.text):
                if _informational(sentence) or _DECISION_TITLE.match(sentence):
                    continue
                scope = _scope_of(sentence)
                if scope is None:
                    return None
                scopes.append(scope)
    return scopes


def _named_section(match: re.Match[str]) -> str | None:
    """The one section a ``Delt`` sentence names, from ``ny § X`` or the amended act's clause."""
    if match.group("sec"):
        return section_key_of(match.group("sec"))
    rest = match.group("rest")
    if rest is None:
        return None
    found = _SECTION_IN.findall(rest)
    return section_key(found[-1]) if len(found) == 1 else None


def _scopes_for(operation: Operation, scopes: list[Scope]) -> list[Scope]:
    """The scopes that reach the operation; the specific ones when any does."""
    covering = [scope for scope in scopes if scope.covers(operation)]
    specific = [s for s in covering if s.section is not None or s.own_sections]
    return specific or covering


def _named_in(law: Document, scope: Scope) -> bool:
    """Whether the law has the part or own sections the scope names."""
    if scope.own_sections:
        return any(p.key in scope.own_sections for p in law.provisions)
    return scope.part in {part.label.upper() for part in law.parts}


def _body_disagrees(decision: Document, header_date: date) -> bool:
    """A full decision whose body names another date or an exception is not the header alone."""
    for part in decision.parts:
        for node in part.nodes:
            for sentence in split_sentences(node.text):
                if _informational(sentence) or _DECISION_TITLE.match(sentence):
                    continue
                if _RESERVATION.search(sentence):
                    return True
                stated = _stated_date(sentence)
                if stated is not None and stated != header_date:
                    return True
    return False


def _stated_date(sentence: str) -> date | None:
    """The date a sentence puts after its commencement phrase; a cited act's date never counts."""
    phrase = _COMMENCEMENT_PHRASE.search(sentence)
    if phrase is None:
        return None
    return parse_date(ACT_CITATION.sub("", sentence[phrase.end() :]))


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
    """One decision read once: partial scopes or a full date, and what to say when unreadable."""

    def __init__(self, decision: Document, law: Document, ordered: bool) -> None:
        self.document = decision
        self.title = decision.header.title
        self.partial = bool(_PARTIAL_TITLE.match(self.title))
        self.scopes = _partial_scopes(decision) if self.partial else None
        self.full_date = None if self.partial else header_timing(decision).dated_on
        raw = decision.header.date_in_force
        self.conditional = not self.partial and bool(_CONDITIONAL.match(raw))
        self.unreadable = self._unreadable(law, ordered)

    def _unreadable(self, law: Document, ordered: bool) -> str | None:
        if not ordered:
            return "decision without a publication time among several"
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
        if self.full_date is None:
            return "full decision without a readable date"
        if _body_disagrees(self.document, self.full_date):
            return "full decision body disagrees with its header"
        return None

    def answer(self, op: Operation) -> tuple[date | None, str] | None:
        """(date, quote) for the operation, (None, title) when not reached, None on a conflict."""
        if self.scopes is None:
            return self.full_date, self.title
        scopes = _scopes_for(op, self.scopes)
        if not scopes:
            return None, self.title
        if len({scope.when for scope in scopes}) > 1:
            return None
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
    if answer is None:
        return _unsettled(current, read.document, CONFLICT)
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


def _citing(law: Document, decisions: tuple[Document, ...]) -> list[_Decision]:
    """The decisions citing the law, in publication order; unordered ones are marked unreadable."""
    cited = [d for d in decisions if is_decision_for(d, law.header.refid)]
    ordered = len(cited) < SEVERAL or all(d.header.published_at is not None for d in cited)
    return [
        _Decision(d, law, ordered or d.header.published_at is not None)
        for d in sorted(cited, key=lambda d: (d.header.published_at or datetime.min, d.member))
    ]


def resolve_timings(
    law: Document,
    clauses: tuple[str, ...],
    operations: tuple[Operation, ...],
    decisions: tuple[Document, ...],
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


def baseline_timing(act: Document, decisions: tuple[Document, ...]) -> Timing:
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
