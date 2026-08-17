"""Temporal event derivation and serving-side not-in-force notices (ADR-0009).

Detects, in rendered corpus Markdown, what the source itself marks as not
(or not necessarily) in force:

* announced amendments — future-tense verbs in amendment notes (``Endres
  ved``, periphrastic ``Vert endra ved`` / ``Blir endret ved``);
* dated commencements that have not arrived at the evaluation date;
* delegated commencement never exercised (``i kraft fra den tid Kongen
  bestemmer`` — ``pending_indeterminate``);
* body-text ``ikke satt i kraft`` markers on provisions never brought
  into force.

T1 exposes a deterministic canonical layer with act and commencement refs,
``commencement_kind``, provenance, source-note traceability, independent XML
note-count reconciliation, and visible audit residue. It does not persist or
regenerate corpus artifacts; that migration remains separate.

The rule set is ADR-0009 (lovspor-notebook, Accepted 2026-08-14, amended §3):
the canonical fact is evaluation-time independent; in-force status is the
result of total function ``in_force_at`` with evaluation time as explicit
input; ``unknown`` is epistemic and never triggers a notice; an unrecognised
commencement marker never falls through to a date-shaped guess. Classification
is ported from the measured notebook prototype, extended with corpus-observed
periphrastic-future, nynorsk and structural-scope forms.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from io import BytesIO
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo

from lxml import etree
from pydantic import BaseModel, model_validator

from lovspor.errors import TemporalDerivationError
from lovspor.parsing.xml_normalizer import safe_parser

_DOCUMENT_LEVEL = "(document)"

_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_SCOPE_WORDS = (
    r"Kapitteloverskriften|Kapitteloverskrift|Kapittelnummeret|Kapitelnummeret"
    r"|Deloverskrift|Kapitlet|Kapittelet|Kapittel|Overskriften|Overskrifta"
    r"|Overskrift|Avsnittet|Avsnitt|Paragrafen|Delen"
)
# Periphrastic future is how nynorsk (and occasionally bokmål) marks an
# amendment not yet in effect: ``**Vert endra** ved lov …``. A bare
# participle pattern reads it as past tense — the exact misclassification
# ADR-0009 §3b exists to prevent.
_AUX = r"Vert|Blir|Vil\s+bli"
_VERBS = (
    r"Endret|Endra|Tilføyd|Tilføydd|Tilføyet|Føyd\s+til|Opphevet|Oppheva"
    r"|Endres|Tilføyes|Oppheves"
)
# ``ved`` in bokmål notes; ``med`` in the nynorsk head form ``Endra med
# lover …`` — requiring ``ved`` alone silently drops every event in such
# a note, announced ones included.
_NOTE_START = re.compile(
    rf"^> \*{{0,2}}(?:({_SCOPE_WORDS})\s+)?\*{{0,2}}(?:({_AUX})\s+)?"
    rf"({_VERBS})\*{{0,2}}(?:\s+i\s+sin\s+helhet)?\s+(?:ved|med)\b",
    re.I,
)
_VERB = re.compile(
    rf"\*{{0,2}}(?:({_AUX})\s+)?({_VERBS})\*{{0,2}}"
    rf"(?:\s+i\s+sin\s+helhet)?\s+(?:ved|med)\b",
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
    "tilføyes": "inserted", "føyd til": "inserted",
    "opphevet": "repealed", "oppheva": "repealed", "oppheves": "repealed",
}  # fmt: skip
_SCOPE_MAP: dict[str, Scope] = {
    "kapitlet": "chapter", "kapittelet": "chapter",
    "kapittel": "chapter",
    "overskriften": "heading", "overskrift": "heading",
    "overskrifta": "heading",
    "kapitteloverskrift": "chapter_heading",
    "kapitteloverskriften": "chapter_heading",
    "deloverskrift": "part_heading",
    "kapittelnummeret": "chapter_number", "kapitelnummeret": "chapter_number",
    "avsnitt": "paragraph", "avsnittet": "paragraph",
    "paragrafen": "provision", "delen": "part",
}  # fmt: skip

Kind = Literal["amended", "inserted", "repealed"]
Scope = Literal[
    "provision",
    "chapter",
    "heading",
    "part",
    "paragraph",
    "chapter_heading",
    "chapter_number",
    "part_heading",
]


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


class CommencementKind(StrEnum):
    """Evaluation-time-independent commencement fact from ADR-0009 §3."""

    DATED = "dated"
    PENDING_INDETERMINATE = "pending_indeterminate"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"


class Provenance(StrEnum):
    """Basis of a canonical temporal assertion from ADR-0009 §3."""

    SOURCE_EXPLICIT = "source_explicit"
    DETERMINISTICALLY_DERIVED = "deterministically_derived"


class TemporalProblemKind(StrEnum):
    """Visible residue from deriving events out of one amendment note."""

    MIXED_KIND_NOTE = "mixed_kind_note"
    UNRECOGNISED_NOTE = "unrecognised_note"
    MARKER_BEFORE_ACT = "marker_before_act"
    NO_AMENDING_ACT = "no_amending_act"
    UNRECOGNISED_MARKER = "unrecognised_marker"
    NON_COMMENCEMENT_MARKER = "non_commencement_marker"
    UNLINKED_ACT_REFERENCE = "unlinked_act_reference"
    DUPLICATE_EVENT = "duplicate_event"


class AmendmentEvent(BaseModel):
    """One amending act cited in one amendment note, with its marker."""

    provision: str
    scope: Scope
    kind: Kind
    announced: bool
    amending_act: str
    amending_act_ref: str | None
    marker_class: MarkerClass
    commencement_kind: CommencementKind
    commencement_instrument: str | None
    provenance: Provenance
    valid_from: date | None
    raw_marker: str | None
    source_note: str
    source_line: int

    @model_validator(mode="after")
    def _dated_iff_valid_from(self) -> AmendmentEvent:
        dated = self.marker_class is MarkerClass.EXPLICIT_DATE
        if dated != (self.valid_from is not None):
            msg = "valid_from is set exactly when marker_class is explicit_date"
            raise ValueError(msg)
        expected_kind, expected_provenance = _canonical_classification(self.marker_class)
        if self.commencement_kind is not expected_kind:
            msg = "commencement_kind must match marker_class"
            raise ValueError(msg)
        if self.provenance is not expected_provenance:
            msg = "provenance must match marker_class"
            raise ValueError(msg)
        return self


class TemporalProblem(BaseModel):
    """One auditable parser condition that must not disappear silently."""

    kind: TemporalProblemKind
    provision: str
    source_line: int
    raw_value: str | None


class TemporalLayer(BaseModel):
    """Canonical, deterministic temporal events derived for one document."""

    schema_version: Literal[1] = 1
    document_ref: str | None
    notes_seen: int
    events: list[AmendmentEvent]
    problems: list[TemporalProblem]


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
    source_note: str


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
    if event.valid_from is not None:
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


def derive_temporal_layer(
    markdown: str,
    *,
    document_ref: str | None = None,
    expected_note_count: int | None = None,
    strict: bool = True,
) -> TemporalLayer:
    """Derive canonical events and visible audit residue for one document.

    ``expected_note_count`` comes from an independent source-XML count. A
    mismatch fails closed. Strict mode rejects commencement markers whose
    structure would require guessing a temporal interpretation.
    """
    notes = _collect_notes(markdown.splitlines())
    if expected_note_count is not None and len(notes) != expected_note_count:
        msg = f"temporal note count mismatch: source={expected_note_count}, rendered={len(notes)}"
        raise TemporalDerivationError(msg)

    events: list[AmendmentEvent] = []
    problems: list[TemporalProblem] = []
    for line_no, provision, note in notes:
        note_events = _events_from_note(line_no, provision, note)
        events.extend(note_events)
        problems.extend(_problems_from_note(line_no, provision, note, note_events))
    problems.extend(_duplicate_problems(events))

    fatal = {TemporalProblemKind.UNRECOGNISED_MARKER}
    first_fatal = next((problem for problem in problems if problem.kind in fatal), None)
    if strict and first_fatal is not None:
        raise TemporalDerivationError(
            f"temporal derivation failed: {first_fatal.kind.value} "
            f"at line {first_fatal.source_line}"
        )
    return TemporalLayer(
        document_ref=document_ref,
        notes_seen=len(notes),
        events=events,
        problems=problems,
    )


def count_source_amendment_notes(xml_bytes: bytes) -> int:
    """Count ``changesToParent`` elements independently in source XML."""
    try:
        tree = etree.parse(BytesIO(xml_bytes), parser=safe_parser(remove_blank_text=False))
    except etree.XMLSyntaxError as exc:
        raise TemporalDerivationError(
            f"malformed XML while counting amendment notes: {exc}"
        ) from exc
    return sum(
        1 for element in tree.iter() if "changesToParent" in (element.get("class") or "").split()
    )


def derive_temporal_layer_from_source(
    xml_bytes: bytes,
    markdown: str,
    *,
    document_ref: str | None = None,
    strict: bool = True,
) -> TemporalLayer:
    """Derive a layer while reconciling rendered notes against source XML."""
    return derive_temporal_layer(
        markdown,
        document_ref=document_ref,
        expected_note_count=count_source_amendment_notes(xml_bytes),
        strict=strict,
    )


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


def _relabel[T: (AmendmentEvent, NeverInForceMarker)](model: T, provision: str | None) -> T:
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
    if _CHAPTER.match(line):
        return line.removeprefix("## ").strip()
    return current


def _collect_notes(lines: list[str]) -> list[tuple[int, str, str]]:
    """(line_no, provision, note text) for every amendment note block."""
    found: list[tuple[int, str, str]] = []
    provision = _DOCUMENT_LEVEL
    skip_until = 0
    for i, raw_line in enumerate(lines):
        if i < skip_until:
            continue
        line = raw_line
        provision = _track_provision(line, provision)
        if line.startswith("> "):
            start = i + 1
            skip_until, note = _note_block(lines, i)
            found.append((start, provision, note))
    return found


def _note_block(lines: list[str], start: int) -> tuple[int, str]:
    """Join a ``> …`` block from ``start``; return (next index, note text)."""
    block = [lines[start]]
    for j in range(start + 1, len(lines)):
        if lines[j] != ">" and not lines[j].startswith("> "):
            return j, " ".join(b[2:] if b.startswith("> ") else "" for b in block)
        block.append(lines[j])
    return len(lines), " ".join(b[2:] if b.startswith("> ") else "" for b in block)


def _events_from_note(line_no: int, provision: str, note: str) -> list[AmendmentEvent]:
    head = _NOTE_START.match("> " + note)
    if head is None:
        return []
    scope_group = head.group(1)
    scope = "provision" if scope_group is None else _SCOPE_MAP[scope_group.lower()]
    context = _NoteContext(
        line_no,
        provision,
        scope,
        _verbs_in(note),
        note,
    )
    return [
        _event_for(context, pos, act_text, act_ref, marker)
        for pos, act_text, act_ref, marker in _acts_and_markers(note)
    ]


def _event_for(
    context: _NoteContext,
    pos: int,
    act_text: str,
    act_ref: str | None,
    marker: str | None,
) -> AmendmentEvent:
    kind, announced = _verb_for(pos, context.verbs)
    if marker is None:
        marker_class, valid_from = MarkerClass.ABSENT, None
    else:
        marker_class, valid_from = _classify_marker(marker)
    commencement_kind, provenance = _canonical_classification(marker_class)
    return AmendmentEvent(
        provision=context.provision,
        scope=context.scope,
        kind=kind,
        announced=announced,
        amending_act=act_text,
        amending_act_ref=act_ref,
        marker_class=marker_class,
        commencement_kind=commencement_kind,
        commencement_instrument=_commencement_instrument(marker),
        provenance=provenance,
        valid_from=valid_from,
        raw_marker=marker,
        source_note=context.source_note,
        source_line=context.line_no,
    )


def _classify_marker(marker: str) -> tuple[MarkerClass, date | None]:
    """Classify on marker STRUCTURE, never on the presence of a year.

    An unrecognised form yields no date — a misparsed marker would be a
    fabricated commencement date (ADR-0009 §2).
    """
    # Link labels are the legal text; hrefs are identifiers. Character-set
    # mutations of strip("() ") are equivalent after this grammar match.
    body = _LINK.sub(lambda m: m.group(1), marker).strip("() ")  # pragma: no mutate
    if not _COMMENCEMENT_MARKER.search(body):
        return MarkerClass.NOT_A_COMMENCEMENT_MARKER, None
    if _PENDING.search(body):
        return MarkerClass.PENDING_INDETERMINATE, None
    if _RELATIVE.search(body):
        return MarkerClass.RELATIVE, None
    if (parsed := _parse_date(body)) is not None:
        return MarkerClass.EXPLICIT_DATE, parsed
    return MarkerClass.UNRECOGNISED, None


def _canonical_classification(
    marker_class: MarkerClass,
) -> tuple[CommencementKind, Provenance]:
    if marker_class is MarkerClass.EXPLICIT_DATE:
        return CommencementKind.DATED, Provenance.SOURCE_EXPLICIT
    if marker_class is MarkerClass.PENDING_INDETERMINATE:
        return CommencementKind.PENDING_INDETERMINATE, Provenance.SOURCE_EXPLICIT
    if marker_class in {MarkerClass.RELATIVE, MarkerClass.UNRECOGNISED}:
        return CommencementKind.AMBIGUOUS, Provenance.SOURCE_EXPLICIT
    return CommencementKind.UNKNOWN, Provenance.DETERMINISTICALLY_DERIVED


def _commencement_instrument(marker: str | None) -> str | None:
    if marker is None:
        return None
    link = _LINK.search(marker)
    return link.group(2) if link else None


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
) -> tuple[Kind, bool]:
    """The verb governing an act is the nearest one preceding its citation."""
    prior = [(kind, announced) for verb_pos, kind, announced in verbs if verb_pos <= pos]
    return prior[-1]


def _acts_and_markers(note: str) -> list[tuple[int, str, str | None, str | None]]:
    """Pair each amending-act citation with every marker following it.

    One act may carry several markers — partial commencement puts two
    scoped dates behind one act, and taking only the first is the collapse
    ADR-0009 §4 forbids.
    """
    acts, spans = _find_acts(note)
    triples: list[tuple[int, str, str | None, str | None]] = []
    for idx, (pos, text, ref) in enumerate(acts):
        nxt = acts[idx + 1][0] if idx + 1 < len(acts) else len(note)
        # Span starts cannot equal an act start or the next act start: links
        # were masked before parenthesis scanning. Mutmut's <= forms are
        # therefore equivalent.
        mine = [(s, e) for s, e in spans if pos < s < nxt]  # pragma: no mutate
        if not mine:
            triples.append((pos, text, ref, None))
            continue
        for s, e in mine:
            triples.append((pos, text, ref, note[s:e]))
    return triples


def _find_acts(note: str) -> tuple[list[tuple[int, str, str | None]], list[tuple[int, int]]]:
    """Amending acts cited outside any marker, linked or bare, in order."""
    masked, links = _mask_links(note)
    spans = _top_level_spans(masked)
    acts: list[tuple[int, str, str | None]] = [
        (pos, text, ref)
        for pos, text, ref in links
        if ref.startswith("lov/") and not _inside(pos, spans)
    ]
    # A bare citation carries no link; dropping it would silently lose the
    # event.
    acts += [
        (m.start(), m.group(0), None)
        for m in _BARE_ACT.finditer(masked)
        if not _inside(m.start(), spans)
    ]
    # Natural tuple order and this explicit key are equivalent because act
    # positions are unique; keep the key to state the ordering contract.
    return sorted(acts, key=lambda act: act[0]), spans  # pragma: no mutate


def _problems_from_note(
    line_no: int,
    provision: str,
    note: str,
    events: list[AmendmentEvent],
) -> list[TemporalProblem]:
    problems: list[TemporalProblem] = []
    head = _NOTE_START.match("> " + note)
    verbs = _verbs_in(note)
    kinds = {kind for _pos, kind, _announced in verbs}
    if head is not None:
        kinds.add(_KIND_MAP[head.group(3).lower()])
    else:
        problems.append(_problem(TemporalProblemKind.UNRECOGNISED_NOTE, provision, line_no, note))
    if len(kinds) > 1:
        problems.append(_problem(TemporalProblemKind.MIXED_KIND_NOTE, provision, line_no, note))

    acts, spans = _find_acts(note)
    first_act = acts[0][0] if acts else len(note)
    if any(start < first_act for start, _end in spans):
        problems.append(_problem(TemporalProblemKind.MARKER_BEFORE_ACT, provision, line_no, note))
    if not acts:
        problems.append(_problem(TemporalProblemKind.NO_AMENDING_ACT, provision, line_no, note))

    for event in events:
        if event.marker_class is MarkerClass.UNRECOGNISED:
            problems.append(
                _problem(
                    TemporalProblemKind.UNRECOGNISED_MARKER,
                    provision,
                    line_no,
                    event.raw_marker,
                )
            )
        elif event.marker_class is MarkerClass.NOT_A_COMMENCEMENT_MARKER:
            problems.append(
                _problem(
                    TemporalProblemKind.NON_COMMENCEMENT_MARKER,
                    provision,
                    line_no,
                    event.raw_marker,
                )
            )
        if event.amending_act_ref is None:
            problems.append(
                _problem(
                    TemporalProblemKind.UNLINKED_ACT_REFERENCE,
                    provision,
                    line_no,
                    event.amending_act,
                )
            )
    return problems


def _duplicate_problems(events: list[AmendmentEvent]) -> list[TemporalProblem]:
    seen: set[tuple[str, str | None, date | None, Kind]] = set()
    problems: list[TemporalProblem] = []
    for event in events:
        identity = (event.provision, event.amending_act_ref, event.valid_from, event.kind)
        if identity in seen:
            problems.append(
                _problem(
                    TemporalProblemKind.DUPLICATE_EVENT,
                    event.provision,
                    event.source_line,
                    event.amending_act_ref or event.amending_act,
                )
            )
        seen.add(identity)
    return problems


def _problem(
    kind: TemporalProblemKind,
    provision: str,
    source_line: int,
    raw_value: str | None,
) -> TemporalProblem:
    return TemporalProblem(
        kind=kind,
        provision=provision,
        source_line=source_line,
        raw_value=raw_value,
    )


def _mask_links(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Blank markdown links (length-preserving) so paren scanning holds."""
    links: list[tuple[int, str, str]] = []
    out: list[str] = []
    cursor = 0  # pragma: no mutate - None and 0 are equivalent slice starts
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
    start = 0  # pragma: no mutate - overwritten on every depth-0 opening paren
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
