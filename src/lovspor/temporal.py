"""Serving-side classification of not-in-force law (ADR-0009 T0).

Detects, in rendered corpus Markdown, what the source itself marks as not
(or not necessarily) in force:

* announced amendments — future-tense verbs in amendment notes (``Endres
  ved``, periphrastic ``Vert endra ved`` / ``Blir endret ved``);
* dated commencements that have not arrived at the evaluation date;
* delegated commencement never exercised (``i kraft fra den tid Kongen
  bestemmer`` — ``pending_indeterminate``);
* body-text ``ikke satt i kraft`` markers on provisions never brought
  into force.

The rule set is ADR-0009 (lovspor-notebook, Accepted 2026-08-14, amended
§3): the canonical fact is evaluation-time independent; in-force status is
the result of the total function ``in_force_at`` with the evaluation time
an explicit input; ``unknown`` is epistemic and never triggers a notice; a
marker the parser does not recognise never falls through to a date-shaped
guess. Classification is ported from the measured notebook prototype
(``experiments/temporal-parser-prototype``), extended with the
periphrastic-future and ``med``-for-``ved`` forms it left unparsed.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Literal, NamedTuple, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, model_validator

_DOCUMENT_LEVEL = "(document)"

_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_SCOPE_WORDS = r"Kapitlet|Kapittelet|Overskriften|Overskrift|Paragrafen|Delen"
# Periphrastic future is how nynorsk (and occasionally bokmål) marks an
# amendment not yet in effect: ``**Vert endra** ved lov …``. A bare
# participle pattern reads it as past tense — the exact misclassification
# ADR-0009 §3b exists to prevent.
_AUX = r"Vert|Blir|Vil\s+bli"
_VERBS = (
    r"Endret|Endra|Tilføyd|Tilføydd|Tilføyet|Opphevet|Oppheva"
    r"|Endres|Tilføyes|Oppheves"
)
# ``ved`` in bokmål notes; ``med`` in the nynorsk head form ``Endra med
# lover …`` — requiring ``ved`` alone silently drops every event in such
# a note, announced ones included.
_NOTE_START = re.compile(
    rf"^> \*{{0,2}}(?:({_SCOPE_WORDS})\s+)?(?:({_AUX})\s+)?"
    rf"({_VERBS})\*{{0,2}}\s+(?:ved|med)\b",
    re.I,
)
_VERB = re.compile(
    rf"\*{{0,2}}(?:({_AUX})\s+)?({_VERBS})\*{{0,2}}\s+(?:ved|med)\b",
    re.I,
)
_ANNOUNCED_FORMS = {"endres", "tilføyes", "oppheves"}
_BARE_ACT = re.compile(r"\b\d{1,2}\s+[a-zæøå]+\.?\s+(?:19|20)\d{2}\s+nr\.\s*\d+", re.I)
_PROVISION = re.compile(r"^#{2,4}\s+(§{1,2}\s*[^.]+?)\s*(?:\.|$)")
_CHAPTER = re.compile(r"^##\s+(Kapittel|Kap\.|Del)\s+([^\s.]+)")

_MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "mars": 3, "mar": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11, "desember": 12, "des": 12,
}  # fmt: skip
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_ALT})\.?\s+(\d{{4}})\b", re.I)

# Delegated commencement, not yet exercised. Deliberately loose: the corpus
# carries ``den tiden``, nynorsk ``Kongen fastset``, and the source typo
# ``den til``.
_PENDING = re.compile(
    r"\bden\s+ti(d|den|l)\b.{0,20}\b(Kongen|departementet)\b|Kongen\s+fastset", re.I
)
# Commencement expressed against another instrument; any year present
# belongs to that instrument, never to this event (the cotif-loven fixture).
_RELATIVE = re.compile(r"\bsamtidig\s+(som|med)\b|\bsame\s+tid\s+som\b|\bfrå\s+same\s+tid\b", re.I)
_COMMENCEMENT_MARKER = re.compile(r"(?:^|[,;]\s*)(ikr\.|i\s?kraft|iverksatt)", re.I)
_NEVER_IN_FORCE = re.compile(r"\b(?:ikke\s+satt|ikkje\s+sett)\s+i\s+kraft\b", re.I)

_KIND_MAP: dict[str, Kind] = {
    "endret": "amended", "endra": "amended", "endres": "amended",
    "tilføyd": "inserted", "tilføydd": "inserted", "tilføyet": "inserted",
    "tilføyes": "inserted",
    "opphevet": "repealed", "oppheva": "repealed", "oppheves": "repealed",
}  # fmt: skip
_SCOPE_MAP: dict[str, Scope] = {
    "kapitlet": "chapter", "kapittelet": "chapter",
    "overskriften": "heading", "overskrift": "heading",
    "paragrafen": "provision", "delen": "part",
}  # fmt: skip

Kind = Literal["amended", "inserted", "repealed"]
Scope = Literal["provision", "chapter", "heading", "part"]
# PEP 695 syntax is off-limits while mutmut is pinned at 2.5.1 (issue #91).
_M = TypeVar("_M", "AmendmentEvent", "NeverInForceMarker")


class MarkerClass(StrEnum):
    """Structural classification of one commencement marker."""

    EXPLICIT_DATE = "explicit_date"
    PENDING_INDETERMINATE = "pending_indeterminate"
    RELATIVE = "relative"
    UNRECOGNISED = "unrecognised"
    NOT_A_COMMENCEMENT_MARKER = "not_a_commencement_marker"
    ABSENT = "absent"


class InForceStatus(StrEnum):
    """Result of evaluating one event against an explicit evaluation date."""

    IN_FORCE = "in_force"
    NOT_IN_FORCE = "not_in_force"
    INDETERMINATE = "indeterminate"


class AmendmentEvent(BaseModel):
    """One amending act cited in one amendment note, with its marker."""

    provision: str
    scope: Scope
    kind: Kind
    announced: bool
    amending_act: str
    marker_class: MarkerClass
    valid_from: date | None
    raw_marker: str | None
    source_line: int

    @model_validator(mode="after")
    def _dated_iff_valid_from(self) -> AmendmentEvent:
        dated = self.marker_class is MarkerClass.EXPLICIT_DATE
        if dated != (self.valid_from is not None):
            msg = "valid_from is set exactly when marker_class is explicit_date"
            raise ValueError(msg)
        return self


class NeverInForceMarker(BaseModel):
    """Body-text statement that a provision was never brought into force."""

    provision: str
    text: str
    source_line: int


class TemporalNotice(BaseModel):
    """Everything in one served document that must not read as law in force."""

    evaluation_date: date
    events: list[AmendmentEvent]
    never_in_force: list[NeverInForceMarker]


class _NoteContext(NamedTuple):
    line_no: int
    provision: str
    scope: Scope
    verbs: list[tuple[int, Kind, bool]]
    fallback: tuple[Kind, bool]


def evaluation_date_today() -> date:
    """Today on the law's own clock (Europe/Oslo), for serving-time evaluation.

    Norwegian commencement dates begin on Norwegian calendar days; around
    midnight UTC is a day off.
    """
    return datetime.now(tz=ZoneInfo("Europe/Oslo")).date()


def in_force_at(event: AmendmentEvent, evaluation_date: date) -> InForceStatus:
    """Evaluate one event against an explicit date. Total: never raises.

    ``pending_indeterminate`` has no in-force date by the source's own
    statement; the epistemic classes (relative / unrecognised / absent)
    support no verdict either way.
    """
    if event.marker_class is MarkerClass.EXPLICIT_DATE and event.valid_from is not None:
        if event.valid_from <= evaluation_date:
            return InForceStatus.IN_FORCE
        return InForceStatus.NOT_IN_FORCE
    if event.marker_class is MarkerClass.PENDING_INDETERMINATE:
        return InForceStatus.NOT_IN_FORCE
    return InForceStatus.INDETERMINATE


def extract_events(markdown: str) -> list[AmendmentEvent]:
    """Every amendment event in every note of a rendered document."""
    events: list[AmendmentEvent] = []
    for line_no, provision, note in _collect_notes(markdown.splitlines()):
        events.extend(_events_from_note(line_no, provision, note))
    return events


def extract_never_in_force(markdown: str) -> list[NeverInForceMarker]:
    """Body-text ``ikke satt i kraft`` markers, attributed to their provision.

    Note lines (``> …``) are skipped — their events go through
    ``extract_events``.
    """
    markers: list[NeverInForceMarker] = []
    provision = _DOCUMENT_LEVEL
    for line_no, line in enumerate(markdown.splitlines(), start=1):
        provision = _track_provision(line, provision)
        if line.startswith(">"):
            continue
        if _NEVER_IN_FORCE.search(line):
            markers.append(
                NeverInForceMarker(provision=provision, text=line.strip(), source_line=line_no)
            )
    return markers


def build_notice(
    markdown: str,
    evaluation_date: date,
    default_provision: str | None = None,
) -> TemporalNotice | None:
    """The not-in-force notice for one served document, or None.

    ``default_provision`` labels events found in a heading-less fragment
    (a single section body served without its heading).
    """
    events = [
        _relabel(event, default_provision)
        for event in extract_events(markdown)
        if _notice_worthy(event, evaluation_date)
    ]
    never = [_relabel(marker, default_provision) for marker in extract_never_in_force(markdown)]
    if not events and not never:
        return None
    return TemporalNotice(evaluation_date=evaluation_date, events=events, never_in_force=never)


def render_notice(notice: TemporalNotice) -> str:
    """Deterministic Markdown for one notice: f(notice) alone, no clock."""
    lines = [
        "---",
        "",
        f"**Temporal notice — law not in force (evaluated {notice.evaluation_date.isoformat()}).**",
        "The source marks the following as announced amendments or provisions"
        " not in force; announced text is not part of the consolidated law"
        " above. The evaluation date is an explicit input of this notice"
        " (ADR-0009 T0).",
        "",
    ]
    lines.extend(_event_line(event, notice.evaluation_date) for event in notice.events)
    lines.extend(
        f"- {marker.provision}: never brought into force — «{marker.text}»"
        for marker in notice.never_in_force
    )
    return "\n".join(lines)


def append_notice(markdown: str, evaluation_date: date) -> str:
    """Serve-time composition: document plus its notice, or unchanged bytes."""
    notice = build_notice(markdown, evaluation_date)
    if notice is None:
        return markdown
    return f"{markdown.rstrip()}\n\n{render_notice(notice)}\n"


def _notice_worthy(event: AmendmentEvent, evaluation_date: date) -> bool:
    """ADR-0009 §3b: announced by verb OR date not arrived. Unknown: never."""
    if event.announced:
        return True
    return in_force_at(event, evaluation_date) is InForceStatus.NOT_IN_FORCE


def _relabel(model: _M, provision: str | None) -> _M:
    if provision is None or model.provision != _DOCUMENT_LEVEL:
        return model
    return model.model_copy(update={"provision": provision})


def _event_line(event: AmendmentEvent, evaluation_date: date) -> str:
    verb = "announced " if event.announced else ""
    marker = _LINK.sub(lambda m: m.group(1), event.raw_marker or "").strip()
    tail = f" {marker}" if marker else ""
    status = _status_phrase(event, evaluation_date)
    return f"- {event.provision}: {verb}{event.kind} by {event.amending_act}{tail} — {status}"


def _status_phrase(event: AmendmentEvent, evaluation_date: date) -> str:
    if event.marker_class is MarkerClass.EXPLICIT_DATE:
        if in_force_at(event, evaluation_date) is InForceStatus.NOT_IN_FORCE:
            return f"not in force at {evaluation_date.isoformat()}"
        return (
            f"commencement date has arrived at {evaluation_date.isoformat()};"
            " the consolidated text above may not yet reflect it"
        )
    if event.marker_class is MarkerClass.PENDING_INDETERMINATE:
        return "no commencement date exists (pending_indeterminate)"
    if event.marker_class is MarkerClass.RELATIVE:
        return "commencement relative to another instrument; no date derived"
    if event.marker_class is MarkerClass.UNRECOGNISED:
        return "commencement marker not recognised; no date derived"
    return "commencement not stated"


def _track_provision(line: str, current: str) -> str:
    if m := _PROVISION.match(line):
        return m.group(1).strip()
    if _CHAPTER.match(line) and not line.startswith("### "):
        return line.lstrip("# ").strip()
    return current


def _collect_notes(lines: list[str]) -> list[tuple[int, str, str]]:
    """(line_no, provision, note text) for every amendment note block."""
    found: list[tuple[int, str, str]] = []
    provision = _DOCUMENT_LEVEL
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")
        provision = _track_provision(line, provision)
        if _NOTE_START.match(line):
            start = i + 1
            i, note = _note_block(lines, i)
            found.append((start, provision, note))
            continue
        i += 1
    return found


def _note_block(lines: list[str], start: int) -> tuple[int, str]:
    """Join a ``> …`` block from ``start``; return (next index, note text)."""
    block = [lines[start].rstrip("\n")]
    j = start + 1
    while j < len(lines) and lines[j].startswith("> ") and lines[j].strip() != ">":
        block.append(lines[j].rstrip("\n"))
        j += 1
    return j, " ".join(b[2:] for b in block)


def _events_from_note(line_no: int, provision: str, note: str) -> list[AmendmentEvent]:
    head = _NOTE_START.match("> " + note)
    if head is None:
        return []
    scope = _SCOPE_MAP.get((head.group(1) or "").lower(), "provision")
    head_verb = head.group(3).lower()
    fallback = (_KIND_MAP[head_verb], head.group(2) is not None or head_verb in _ANNOUNCED_FORMS)
    context = _NoteContext(line_no, provision, scope, _verbs_in(note), fallback)
    return [
        _event_for(context, pos, act_text, marker)
        for pos, act_text, marker in _acts_and_markers(note)
    ]


def _event_for(
    context: _NoteContext,
    pos: int,
    act_text: str,
    marker: str | None,
) -> AmendmentEvent:
    kind, announced = _verb_for(pos, context.verbs, context.fallback)
    if marker is None:
        marker_class, valid_from = MarkerClass.ABSENT, None
    else:
        marker_class, valid_from = _classify_marker(marker)
    return AmendmentEvent(
        provision=context.provision,
        scope=context.scope,
        kind=kind,
        announced=announced,
        amending_act=act_text,
        marker_class=marker_class,
        valid_from=valid_from,
        raw_marker=marker,
        source_line=context.line_no,
    )


def _classify_marker(marker: str) -> tuple[MarkerClass, date | None]:
    """Classify on marker STRUCTURE, never on the presence of a year.

    An unrecognised form yields no date — a misparsed marker would be a
    fabricated commencement date (ADR-0009 §2).
    """
    body = _LINK.sub(lambda m: m.group(1), marker).strip("() ")
    if not _COMMENCEMENT_MARKER.search(body):
        return MarkerClass.NOT_A_COMMENCEMENT_MARKER, None
    if _PENDING.search(body):
        return MarkerClass.PENDING_INDETERMINATE, None
    if _RELATIVE.search(body):
        return MarkerClass.RELATIVE, None
    if (parsed := _parse_date(body)) is not None:
        return MarkerClass.EXPLICIT_DATE, parsed
    return MarkerClass.UNRECOGNISED, None


def _parse_date(text: str) -> date | None:
    m = _DATE.search(text)
    if m is None:
        return None
    return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))


def _verbs_in(note: str) -> list[tuple[int, Kind, bool]]:
    """(position, kind, announced) for every verb outside a marker."""
    masked, _ = _mask_links(note)
    spans = _top_level_spans(masked)
    verbs: list[tuple[int, Kind, bool]] = []
    for m in _VERB.finditer(masked):
        if _inside(m.start(), spans):
            continue
        verb = m.group(2).lower()
        announced = m.group(1) is not None or verb in _ANNOUNCED_FORMS
        verbs.append((m.start(), _KIND_MAP[verb], announced))
    return verbs


def _verb_for(
    pos: int,
    verbs: list[tuple[int, Kind, bool]],
    fallback: tuple[Kind, bool],
) -> tuple[Kind, bool]:
    """The verb governing an act is the nearest one preceding its citation."""
    prior = [(kind, announced) for verb_pos, kind, announced in verbs if verb_pos <= pos]
    return prior[-1] if prior else fallback


def _acts_and_markers(note: str) -> list[tuple[int, str, str | None]]:
    """Pair each amending-act citation with every marker following it.

    One act may carry several markers — partial commencement puts two
    scoped dates behind one act, and taking only the first is the collapse
    ADR-0009 §4 forbids.
    """
    acts, spans = _find_acts(note)
    triples: list[tuple[int, str, str | None]] = []
    for idx, (pos, text) in enumerate(acts):
        nxt = acts[idx + 1][0] if idx + 1 < len(acts) else len(note)
        mine = [(s, e) for s, e in spans if pos < s < nxt]
        for s, e in mine or [(-1, -1)]:
            triples.append((pos, text, note[s:e] if s >= 0 else None))
    return triples


def _find_acts(note: str) -> tuple[list[tuple[int, str]], list[tuple[int, int]]]:
    """Amending acts cited outside any marker, linked or bare, in order."""
    masked, links = _mask_links(note)
    spans = _top_level_spans(masked)
    acts = [
        (pos, text)
        for pos, text, ref in links
        if ref.startswith("lov/") and not _inside(pos, spans)
    ]
    # A bare citation carries no link; dropping it would silently lose the
    # event.
    acts += [
        (m.start(), m.group(0)) for m in _BARE_ACT.finditer(masked) if not _inside(m.start(), spans)
    ]
    return sorted(acts, key=lambda act: act[0]), spans


def _mask_links(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Blank markdown links (length-preserving) so paren scanning holds."""
    links: list[tuple[int, str, str]] = []
    out: list[str] = []
    cursor = 0
    for m in _LINK.finditer(text):
        out.append(text[cursor : m.start()])
        links.append((sum(len(part) for part in out), m.group(1), m.group(2)))
        out.append("\x00" * (m.end() - m.start()))
        cursor = m.end()
    out.append(text[cursor:])
    return "".join(out), links


def _top_level_spans(masked: str) -> list[tuple[int, int]]:
    """(start, end) of every depth-1 parenthesised group."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for i, ch in enumerate(masked):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
    return spans


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)
