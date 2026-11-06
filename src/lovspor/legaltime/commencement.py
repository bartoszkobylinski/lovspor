"""When an announced amendment took legal effect, proven or left open.

Evidence, in order of precedence, all of it from the publicData archives:
the amending law's own ``dateInForce`` header, its commencement clause
(``Loven trer i kraft straks.`` with any ``likevel`` exception), and the
commencement decisions (``Ikraftsetting av lov …``, ``Delt ikrafttredelse
…``) whose title cites the law. A date the evidence does not state is never
derived: the operation stays ``pending`` (delegated, no decision found) or
``unresolved`` (evidence present but outside the grammar).
"""

import re
from datetime import date, datetime
from hashlib import sha256
from typing import Literal

from lovspor.legaltime.document import Document, Frozen
from lovspor.legaltime.instructions import Operation
from lovspor.legaltime.text import ACT_CITATION, act_refid, parse_date, section_key, split_sentences

TimingStatus = Literal["dated", "pending", "unresolved"]
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PENDING_WORDS = re.compile(r"Kongen\s+(?:bestemmer|fastset|fastsett)", re.I)
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


def _sentences(document: Document, clauses: tuple[str, ...]) -> list[str]:
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
        return ("section", section_key(new_section.group("sec")) or "")
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
    if _INFORMATIONAL.match(sentence):
        return True
    without_citations = ACT_CITATION.sub("", sentence)
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
        self.exceptions: dict[tuple[str, str], Timing] = {}
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
    for sentence in _sentences(document, clauses):
        reading.read(document, sentence)
    if reading.failure is not None:
        return dict.fromkeys((op.id for op in operations), reading.failure)
    return _assign(operations, reading.default or header_timing(document), reading.exceptions)


def _scoped(operation: Operation, exceptions: dict[tuple[str, str], Timing]) -> Timing | None:
    section = ("section", operation.address.section or "")
    if operation.kind == "insert_section" and section in exceptions:
        return exceptions[section]
    located = ("part-section", f"{operation.part.upper()}/{operation.address.section}")
    return exceptions.get(located) or exceptions.get(("part", operation.part.upper()))


def _assign(
    operations: tuple[Operation, ...], default: Timing, exceptions: dict[tuple[str, str], Timing]
) -> dict[str, Timing]:
    return {op.id: _scoped(op, exceptions) or default for op in operations}


def is_decision_for(document: Document, law_refid: str) -> bool:
    """A commencement decision whose title cites exactly this law."""
    title = document.header.title
    return bool(_DECISION_TITLE.match(title)) and act_refid(title) == law_refid


def _partial_scopes(decision: Document) -> list[tuple[str, str | None, date]] | None:
    """(part, new section, date) per sentence of a ``Delt`` decision; None if any is foreign."""
    scopes: list[tuple[str, str | None, date]] = []
    for part in decision.parts:
        for node in part.nodes:
            for sentence in split_sentences(node.text):
                if _informational(sentence) or _DECISION_TITLE.match(sentence):
                    continue
                match = _DELT_SENTENCE.match(sentence)
                when = parse_date(match.group("when")) if match else None
                if match is None or when is None:
                    return None
                scopes.append((match.group("part").upper(), _named_section(match), when))
    return scopes


def _named_section(match: re.Match[str]) -> str | None:
    """The one section a ``Delt`` sentence names, from ``ny § X`` or the amended act's clause."""
    if match.group("sec"):
        return section_key(match.group("sec"))
    found = _SECTION_IN.findall(match.group("rest") or "")
    return section_key(found[-1]) if len(found) == 1 else None


def _covered(operation: Operation, scope: tuple[str, str | None, date]) -> bool:
    part, section, _ = scope
    if operation.part.upper() != part:
        return False
    return section is None or operation.address.section == section


def _apply_full(decision: Document, pending: list[Operation], timings: dict[str, Timing]) -> None:
    header = header_timing(decision)
    if header.status == "dated" and header.effective is not None:
        for op in pending:
            timings[op.id] = _dated(decision, header.effective, decision.header.title)


def _apply_partial(
    decision: Document, pending: list[Operation], timings: dict[str, Timing]
) -> None:
    scopes = _partial_scopes(decision)
    title = decision.header.title
    for op in pending:
        if scopes is None:
            timings[op.id] = _unresolved(decision, "partial decision outside the grammar", title)
            continue
        # A whole-part scope first, a section-specific scope last: the specific one wins.
        for scope in sorted(scopes, key=lambda item: item[1] is not None):
            if _covered(op, scope):
                timings[op.id] = _dated(decision, scope[2], f"{title}: del {scope[0]}")


def _apply_decision(
    decision: Document, operations: tuple[Operation, ...], timings: dict[str, Timing]
) -> None:
    pending = [op for op in operations if timings[op.id].status == "pending"]
    if not pending:
        return
    if _PARTIAL_TITLE.match(decision.header.title):
        _apply_partial(decision, pending, timings)
    else:
        _apply_full(decision, pending, timings)


def resolve_timings(
    law: Document,
    clauses: tuple[str, ...],
    operations: tuple[Operation, ...],
    decisions: tuple[Document, ...],
) -> dict[str, Timing]:
    """Every operation's timing from the law, its clause, and the decisions citing it."""
    timings = clause_timings(law, clauses, operations)
    for decision in sorted(
        decisions, key=lambda d: (d.header.published_at or datetime.min, d.member)
    ):
        if is_decision_for(decision, law.header.refid):
            _apply_decision(decision, operations, timings)
    return timings


def baseline_timing(act: Document, decisions: tuple[Document, ...]) -> Timing:
    """When the act itself commenced: its header, else a full decision citing it."""
    timing = header_timing(act)
    if timing.status != "pending":
        return timing
    for decision in decisions:
        if not is_decision_for(decision, act.header.refid):
            continue
        found = header_timing(decision)
        if _PARTIAL_TITLE.match(decision.header.title):
            return _unresolved(
                decision, "partial commencement of the act itself", decision.header.title
            )
        if found.status == "dated" and found.effective is not None:
            return _dated(decision, found.effective, decision.header.title)
    return timing
