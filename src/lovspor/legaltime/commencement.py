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
from datetime import date
from typing import Literal

from lovspor.legaltime.document import Document, Frozen
from lovspor.legaltime.instructions import Operation
from lovspor.legaltime.text import act_refid, parse_date, section_key, split_sentences

TimingStatus = Literal["dated", "pending", "unresolved"]
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PENDING_WORDS = re.compile(r"Kongen\s+(?:bestemmer|fastset|fastsett)", re.I)
_DECISION_TITLE = re.compile(
    r"^(?:Delt\s+)?(?:Ikraftsetting|Ikraftsetjing|Ikrafttredelse|Ikraftsetjinga|Iverksetjing|"
    r"Ikraftsettelse|iverksetting|ikraftsetting)\b",
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
_COMMENCEMENT_PHRASE = re.compile(rf"{_VERB}|\bi\s+kraft\b", re.I)
_EXCEPTION_NEW_SECTION = re.compile(
    r"^Ny\s+(?P<sec>§\s*\d+(?:-\d+)?(?:\s*[a-zæøå](?![a-zæøå]))?)(?:\s+i\s+\S+)?\s+trer\s+likevel\s+i\s+kraft\b",
    re.I,
)
_DELT_SENTENCE = re.compile(
    r"^(?:Endringsloven|Endringslova)\s+del\s+(?P<part>[IVXLC]+)(?:,\s*ny\s+"
    r"(?P<sec>§\s*\d+(?:-\d+)?\s*[a-zæøå]?)\s+i\s+lov[^,]*)?,\s*trer\s+i\s+kraft\s+(?P<when>.+)$",
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
    refid: str
    member: str
    sha256: str
    quote: str


class Timing(Frozen):
    status: TimingStatus
    effective: date | None = None
    known_from: date | None = None
    retroactive: bool = False
    reason: str = ""
    proofs: tuple[Proof, ...] = ()


def _proof(document: Document, quote: str) -> Proof:
    return Proof(
        refid=document.header.refid, member=document.member, sha256=document.sha256, quote=quote
    )


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


def _default_timing(document: Document, sentence: str) -> Timing | None:
    if _DEFAULT_CLAUSE.match(sentence) is None:
        return None
    if _STRAKS.search(sentence):
        first = re.search(r"\d{4}-\d{2}-\d{2}", document.header.date_in_force)
        if first is None:
            return _unresolved(document, "straks without a dated header", sentence)
        return _dated(document, date.fromisoformat(first.group(0)), sentence)
    if _PENDING_WORDS.search(sentence):
        return _pending(document, sentence)
    when = parse_date(sentence)
    if when is None:
        return _unresolved(document, "commencement clause outside the grammar", sentence)
    return _dated(document, when, sentence)


def _exception_scope(sentence: str) -> tuple[str, str] | None:
    """``("section", "§99a")`` or ``("part", "V")`` for an exception sentence, else None."""
    new_section = _EXCEPTION_NEW_SECTION.match(sentence)
    if new_section is not None:
        return ("section", section_key(new_section.group("sec")) or "")
    part = _EXCEPTION_PART.match(sentence)
    return ("part", part.group("part").upper()) if part else None


def _informational(sentence: str) -> bool:
    """Transitional or conditional prose that states no commencement of its own."""
    return bool(_INFORMATIONAL.match(sentence)) or _COMMENCEMENT_PHRASE.search(sentence) is None


def _exception_timing(document: Document, sentence: str) -> Timing:
    if _PENDING_WORDS.search(sentence):
        return _pending(document, sentence)
    if _STRAKS.search(sentence):
        first = re.search(r"\d{4}-\d{2}-\d{2}", document.header.date_in_force)
        if first is not None:
            return _dated(document, date.fromisoformat(first.group(0)), sentence)
    when = parse_date(sentence)
    if when is not None:
        return _dated(document, when, sentence)
    return _unresolved(document, "exception outside the grammar", sentence)


def clause_timings(
    document: Document, clauses: tuple[str, ...], operations: tuple[Operation, ...]
) -> dict[str, Timing]:
    """Per-operation timing from the law's own clause; header timing when there is none."""
    base = header_timing(document)
    default: Timing | None = None
    exceptions: dict[tuple[str, str], Timing] = {}
    failure: Timing | None = None
    for sentence in _sentences(document, clauses):
        found = _default_timing(document, sentence)
        if found is not None:
            default = found
        elif not _informational(sentence):
            scope = _exception_scope(sentence)
            if scope is None:
                failure = _unresolved(
                    document, "commencement sentence outside the grammar", sentence
                )
            else:
                exceptions[scope] = _exception_timing(document, sentence)
    if failure is not None:
        return dict.fromkeys((op.id for op in operations), failure)
    return _assign(operations, default or base, exceptions)


def _scoped(operation: Operation, exceptions: dict[tuple[str, str], Timing]) -> Timing | None:
    section = ("section", operation.address.section or "")
    if operation.kind == "insert_section" and section in exceptions:
        return exceptions[section]
    return exceptions.get(("part", operation.part.upper()))


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
                section = section_key(match.group("sec")) if match.group("sec") else None
                scopes.append((match.group("part"), section, when))
    return scopes


def _covered(operation: Operation, scope: tuple[str, str | None, date]) -> bool:
    part, section, _ = scope
    if operation.part != part:
        return False
    return section is None or (
        operation.kind == "insert_section" and operation.address.section == section
    )


def _apply_decision(
    decision: Document, operations: tuple[Operation, ...], timings: dict[str, Timing]
) -> None:
    pending = [op for op in operations if timings[op.id].status == "pending"]
    header = header_timing(decision)
    if not pending or header.status != "dated" or header.effective is None:
        return
    if not _PARTIAL_TITLE.match(decision.header.title):
        for op in pending:
            timings[op.id] = _dated(decision, header.effective, decision.header.title)
        return
    scopes = _partial_scopes(decision)
    for op in pending:
        if scopes is None:
            timings[op.id] = _unresolved(
                decision, "partial decision outside the grammar", decision.header.title
            )
            continue
        for scope in scopes:
            if _covered(op, scope):
                timings[op.id] = _dated(
                    decision, scope[2], f"{decision.header.title}: del {scope[0]}"
                )


def resolve_timings(
    law: Document,
    clauses: tuple[str, ...],
    operations: tuple[Operation, ...],
    decisions: tuple[Document, ...],
) -> dict[str, Timing]:
    """Every operation's timing from the law, its clause, and the decisions citing it."""
    timings = clause_timings(law, clauses, operations)
    for decision in sorted(
        decisions, key=lambda d: (d.header.published_at or date.min.isoformat(), d.member)
    ):
        if is_decision_for(decision, law.header.refid):
            _apply_decision(decision, operations, timings)
    return timings
