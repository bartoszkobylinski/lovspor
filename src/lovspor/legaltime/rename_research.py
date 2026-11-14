"""Research adapter: Lovtidend section renames to Phase B1 steps (ADR-0023 Phase B2, #861).

Research only. Nothing in the replay, ``run.py``, the archive flow, the report or any
public interface imports this module (a structural test holds that), so it changes no
replay output and no ``REPLAY_VERSION``.

It consumes the engine's own events: the operations ``instructions.interpret`` read from
pinned members, bound to an act by the D7/phase-4B chain and dated by ``resolve_timings``
and the #893 host timing. It re-reads nothing of that. From them it builds:

* **step candidates**: B1 ``Step``s, one per (affected act, member, proven effective
  date), holding the renames this adapter could prove. A step is *complete* only when
  every operation of that group is represented and the member states no commencement
  units for the act. An incomplete step must not be executed.
* **refusals**: every rename-shaped sentence it could not prove, with its reason and proof.

R3's grammar is closed. A sentence is rename-shaped when a § stands before ``blir`` and
another after it; ledd and punktum renumberings are not section renames. Accepted shapes:

* ``(Nåværende|Noverande|Gjeldende|Tidligere) § X blir (ny) § Y``;
* ``§ X (i <citation of the bound act>) blir (ny) § Y``;

each optionally followed by ``(,) og skal lyde (slik):`` and a payload headed ``§ Y``.
A placement clause (``som innleder <ordinal> del``, ``under kapittel N``) is recorded,
and refuses the whole rename: without the structure map ``S`` (R3) its pair is never proven.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date

from pydantic import model_validator

from lovspor.legaltime.archive import Evidence
from lovspor.legaltime.attribution import citations
from lovspor.legaltime.commencement import Event
from lovspor.legaltime.document import Document, Frozen, Provision
from lovspor.legaltime.identity import LabelProof
from lovspor.legaltime.run import events_for, unattributed_for
from lovspor.legaltime.step import Rename, Replacement, Step
from lovspor.legaltime.text import section_key

_SEC = r"§\s?\d+(?:[A-Z]|\s[A-Z](?=-))?(?:-\d+)?(?:\s?[a-hj-zæøå](?![\wæøå]))?"
_KEYWORD = r"(?:Nåværende|Noverande|Gjeldende|Tidligere)"
_TAIL = rf"\s+blir\s+(?:ny\s+)?(?P<target>{_SEC})(?P<rest>.*)"
_KEYWORD_SHAPE = re.compile(rf"{_KEYWORD}\s+(?P<source>{_SEC}){_TAIL}")
_CITATION_SHAPE = re.compile(rf"(?P<source>{_SEC})(?:\s+i\s+(?P<citation>[^§]+?))?{_TAIL}")
_REPLACEMENT = re.compile(r",?\s+og\s+skal\s+lyde(?:\s+slik)?:")
_PLACEMENT = re.compile(
    r",?\s+(?P<clause>som\s+innleder\s+\w+\s+del|under\s+kapittel\s+[\w ]+?)"
    r"(?P<after>\.?|,?\s+og\s+(?:som\s+)?skal\s+lyde(?:\s+slik)?:)"
)
_RENAME_SHAPED = re.compile(r"§.*\bblir\b.*§")
_PARTIAL_LABELS = re.compile(
    rf"De delene av (?P<source>{_SEC})\b.*?\bblir\s+(?:ny\s+)?(?P<target>{_SEC}).*"
)

PARTIAL = "partial rename: extent not in the source"
SEVERAL = "range or several sections: unsupported"
OUTSIDE = "rename outside the grammar"
NO_OP = "rename to the same label"
PLACED = "placement incomplete: no structure map S"
UNHEADED = "replacement not headed by the target label"
UNCLAUSED = "payload without a replacement clause"
CITED = "citation names another act than the bound one"
UNBOUND = "act not bound by the D7/phase-4B chain"
UNKNOWN_MEMBER = "member not in evidence"
UNITS = "member states commencement units for the act"


class ParsedRename(Frozen):
    """What R3's closed grammar reads from one sentence; ``refusal`` set when it reads no rename."""

    source: str | None = None
    target: str | None = None
    citation: str | None = None
    replacement: bool = False
    placement: str | None = None
    refusal: str | None = None


class RenameRefusal(Frozen):
    """A rename-shaped sentence the adapter could not prove, with its provenance."""

    op_id: str
    instrument: str
    member: str
    member_sha256: str | None
    sentence_sha256: str
    act: str | None
    source: str | None  # the labels R3 read, when it read them (a partial rename keeps both)
    target: str | None
    reason: str
    placement: str | None
    timing_status: str
    effective: date | None
    known_from: date | None


class StepCandidate(Frozen):
    """One (act, member, effective date) group as a B1 step; executable only when complete."""

    act: str
    member: str
    effective: date
    step: Step
    gaps: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.gaps

    @model_validator(mode="after")
    def _one_group(self) -> "StepCandidate":
        if (self.step.act, self.step.member, self.step.effective) != (
            self.act, self.member, self.effective,
        ):  # fmt: skip
            raise ValueError("a candidate's step is its own group")
        return self


class Research(Frozen):
    steps: tuple[StepCandidate, ...] = ()
    refusals: tuple[RenameRefusal, ...] = ()

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)

    def projection(self) -> str:
        """I6's projection: op ids (document positions) dropped, lists sorted by sentence digest."""
        refusals = sorted(
            ({k: v for k, v in r.items() if k != "op_id"} for r in self._dumped("refusals")),
            key=lambda r: (r["member"], r["sentence_sha256"]),
        )
        steps = [_projected_step(c) for c in self._dumped("steps")]
        return json.dumps(
            {"refusals": refusals, "steps": steps}, sort_keys=True, ensure_ascii=False
        )

    def _dumped(self, field: str) -> list[dict[str, object]]:
        return list(self.model_dump(mode="json")[field])


def _projected_step(candidate: dict[str, object]) -> dict[str, object]:
    step = dict(candidate["step"])  # type: ignore[call-overload]
    renames = [dict(r) for r in step["renames"]]
    for rename in renames:
        rename["proof"] = {k: v for k, v in rename["proof"].items() if k != "op_id"}
    step["renames"] = sorted(renames, key=lambda r: r["proof"]["sentence_sha256"])
    gaps = sorted(g.split(": ", 1)[0] for g in candidate["gaps"])  # type: ignore[attr-defined]
    return {**candidate, "step": step, "gaps": gaps}


# ---------------------------------------------------------------------------- R3 parser


def parse_rename(sentence: str) -> ParsedRename:
    """R3's closed grammar over one instruction sentence; anything else is a refusal."""
    if sentence.startswith("De delene av"):
        return _partial(sentence)
    if "§§" in sentence:
        return ParsedRename(refusal=SEVERAL)
    match = _KEYWORD_SHAPE.fullmatch(sentence) or _CITATION_SHAPE.fullmatch(sentence)
    if match is None:
        return ParsedRename(refusal=OUTSIDE)
    source, target = section_key(match["source"]), section_key(match["target"])
    if source is None or target is None:
        return ParsedRename(refusal=OUTSIDE)
    citation = match.groupdict().get("citation")
    parsed = ParsedRename(source=source, target=target, citation=citation)
    if source == target:
        return parsed.model_copy(update={"refusal": NO_OP})
    return _with_rest(parsed, match["rest"])


def _partial(sentence: str) -> ParsedRename:
    """A partial rename is refused, with both labels named when the sentence names them (R3)."""
    labels = _PARTIAL_LABELS.fullmatch(sentence)
    if labels is None:
        return ParsedRename(refusal=PARTIAL)
    return ParsedRename(
        source=section_key(labels["source"]), target=section_key(labels["target"]), refusal=PARTIAL
    )


def _with_rest(parsed: ParsedRename, rest: str) -> ParsedRename:
    if rest in {"", "."}:
        return parsed
    if _REPLACEMENT.fullmatch(rest):
        return parsed.model_copy(update={"replacement": True})
    placed = _PLACEMENT.fullmatch(rest)
    if placed is not None:
        return parsed.model_copy(update={"placement": placed["clause"], "refusal": PLACED})
    return parsed.model_copy(update={"refusal": OUTSIDE})


# ----------------------------------------------------------------------------- adapter


def adapt(
    events: Iterable[Event],
    documents: Mapping[str, Document],
    decision_members: frozenset[str] = frozenset(),
) -> Research:
    """Step candidates and refusals for every rename-shaped event; order-free and pure."""
    ordered = sorted(events, key=lambda e: (e.member, e.operation.id))
    proven: list[tuple[str, Rename]] = []
    refusals: list[RenameRefusal] = []
    for event in ordered:
        if not _RENAME_SHAPED.search(event.operation.instruction):
            continue
        outcome = _adapted(event, documents)
        if isinstance(outcome, RenameRefusal):
            refusals.append(outcome)
        else:
            proven.append((str(event.operation.act), outcome))
    steps = _steps(_Context(ordered, refusals, decision_members), proven)
    return Research(
        steps=steps, refusals=tuple(sorted(refusals, key=lambda r: (r.member, r.op_id)))
    )


def adapt_evidence(evidence: Evidence) -> Research:
    """``adapt`` over one act's collected evidence: its bound and its unbound operations."""
    events = [*events_for(evidence), *unattributed_for(evidence)]
    documents = {document.member: document for document in evidence.amenders}
    decisions = frozenset(document.member for document in evidence.decisions)
    return adapt(events, documents, decisions)


def _adapted(event: Event, documents: Mapping[str, Document]) -> Rename | RenameRefusal:
    read = parse_rename(event.operation.instruction)
    document = documents.get(event.member)
    if document is None:
        return _refusal(event, None, UNKNOWN_MEMBER, read)
    if event.operation.act is None:
        return _refusal(event, document, UNBOUND, read)
    parsed = _checked(event, read)
    if parsed.refusal is not None:
        return _refusal(event, document, parsed.refusal, parsed)
    timing = event.timing
    if timing.status != "dated":
        reason = f"timing not proven: {timing.status}: {timing.reason}"
        return _refusal(event, document, reason, parsed)
    return _rename(event, document, parsed)


def _checked(event: Event, parsed: ParsedRename) -> ParsedRename:
    """The parse, refused where the act, the citation or the payload disagree with it."""
    if parsed.refusal is not None:
        return parsed
    if parsed.citation is not None and citations(parsed.citation) != [event.operation.act]:
        return parsed.model_copy(update={"refusal": CITED})
    payload = event.operation.new_provision
    if parsed.replacement and (payload is None or section_key(payload.label) != parsed.target):
        return parsed.model_copy(update={"refusal": UNHEADED})
    if not parsed.replacement and payload is not None:
        return parsed.model_copy(update={"refusal": UNCLAUSED})
    return parsed


def _proof(event: Event, document: Document) -> LabelProof:
    return LabelProof(
        instrument=document.header.refid, op_id=event.operation.id, member=event.member,
        member_sha256=document.sha256, sentence_sha256=_sha(event.operation.instruction),
        effective=event.timing.effective, known_from=event.timing.known_from,
    )  # fmt: skip


def _rename(event: Event, document: Document, parsed: ParsedRename) -> Rename:
    payload = event.operation.new_provision
    replacement = None
    if payload is not None:
        replacement = Replacement(
            headed=section_key(payload.label), payload_sha256=_digest(payload)
        )
    fields = {"source": parsed.source, "target": parsed.target, "replacement": replacement}
    return Rename.model_validate({**fields, "proof": _proof(event, document)})


def _refusal(
    event: Event, document: Document | None, reason: str, parsed: ParsedRename | None = None
) -> RenameRefusal:
    timing = event.timing
    read = parsed or ParsedRename()
    return RenameRefusal(
        op_id=event.operation.id, instrument=event.law, member=event.member,
        member_sha256=None if document is None else document.sha256,
        sentence_sha256=_sha(event.operation.instruction), act=event.operation.act,
        source=read.source, target=read.target, reason=reason, placement=read.placement,
        timing_status=timing.status,
        effective=timing.dated_on, known_from=timing.known_from,
    )  # fmt: skip


def _sha(text: str) -> str:
    """ADR-0020 D9's sentence digest, as the engine computes ``quote_sha256``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(payload: Provision) -> str:
    """The adapter's own digest of a replacement payload: label, heading and ledd texts."""
    parts = [payload.label, payload.heading, *(ledd.text for ledd in payload.ledd)]
    return _sha(json.dumps(parts, ensure_ascii=False))


# ------------------------------------------------------------------------------ grouping

Group = tuple[str, str, date]


class _Context:
    """The whole event list and the refusals, for telling a step what it leaves out."""

    def __init__(
        self, events: list[Event], refusals: list[RenameRefusal], decision_members: frozenset[str]
    ) -> None:
        self.events = events
        self.refused = {r.op_id: r.reason for r in refusals}
        self.decision_members = decision_members


def _steps(context: _Context, proven: list[tuple[str, Rename]]) -> tuple[StepCandidate, ...]:
    """One candidate per (act, member, date) holding a proven rename (R4: after attribution)."""
    held: defaultdict[Group, list[Rename]] = defaultdict(list)
    for act, rename in proven:
        held[(act, rename.proof.member, rename.proof.effective)].append(rename)  # type: ignore[index]
    candidates = [_candidate(group, renames, context) for group, renames in held.items()]
    return tuple(sorted(candidates, key=lambda c: (c.act, c.effective, c.member)))


def _candidate(group: Group, renames: list[Rename], context: _Context) -> StepCandidate:
    act, member, effective = group
    step = Step(act=act, member=member, effective=effective, renames=tuple(renames))
    gaps = _gaps(group, {r.proof.op_id for r in renames}, context)
    return StepCandidate(act=act, member=member, effective=effective, step=step, gaps=gaps)


def _gaps(group: Group, represented: set[str], context: _Context) -> tuple[str, ...]:
    """What keeps the step from being executed: operations or units it does not represent."""
    gaps = []
    for event in context.events:
        op = event.operation
        if (op.act, event.member, event.timing.dated_on) != group or op.id in represented:
            continue
        reason = context.refused.get(op.id)
        gaps.append(
            f"operation not represented: {op.id} ({op.kind})"
            if reason is None
            else f"rename refused: {op.id} ({reason})"
        )
    if group[1] in context.decision_members:
        gaps.append(UNITS)
    return tuple(gaps)
