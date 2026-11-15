"""Evidence builders for B1 steps: step 0, insertions and candidate steps (ADR-0023 B4a, #861).

Research only. Nothing in the replay, ``run.py``, the archive flow, the report or any
public interface imports this module (a structural test holds that), so it changes no
replay output and no ``REPLAY_VERSION``. It consumes the engine's own objects, the
enacting ``Document`` and the attributed, dated ``Event``s, and re-reads none of them.

**Step 0.** ``build_step0`` reads, from each enacted ``legalArticle`` node, its canonical
label, its R1 ``enacted_id`` and its (chapter, part) pair, and mints the ``Ledger`` and
the ``Structure`` together, keyed by identity. A node whose label, data-name or enclosing
structure it cannot read refuses the whole step 0, with every problem listed; nothing is
minted from a partial reading. ADR-0023 R2 requires a ``sentence_sha256`` on every start
proof, but an enacted provision is stated by no sentence and ADR-0020 D9 defines no digest
for it. The builder therefore puts in that field the digest of a named, versioned source
record (``STEP0_POLICY``): the member path, its sha256, the node's ``data-name`` and its
printed label. It is a node digest, never a sentence digest, and ``verify_step0`` re-derives
it from the member. That policy is a proposal awaiting an ADR clarification (#861).

**Operations.** ``build_operation`` turns one attributed event into a B1 operation only
where D7 attribution, a proven effective date, the sentence proof and the operation's own
semantics suffice. A rename goes through the Phase B2 adapter. An insertion takes the
pair its inserting structure states (``Operation.stated``) and only when every stamp that
structure names was read; an ``_enclosing`` stamp of the amending member is kept as raw
evidence and never used. Anything else is an ``Unbuilt`` record with its reason.

**Candidates.** ``build_candidates`` groups built operations by (act, member, proven
effective date), after attribution (R4). A candidate is complete only when every
operation of its act in that member is built for that date, no operation of the member is
unattributed or undated, and no commencement unit or statement is left unrepresented.
``Candidate.executable`` refuses an incomplete one.
"""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Literal, Self

from pydantic import model_validator

from lovspor.legaltime.archive import Evidence
from lovspor.legaltime.baseline import _payload_sentence
from lovspor.legaltime.commencement import _DEFAULT_CLAUSE, Event
from lovspor.legaltime.document import (
    _CHAPTER_NAME,
    _PART_NAME,
    Document,
    Enclosure,
    Provision,
    StatedPlacement,
)
from lovspor.legaltime.errors import LegalTimeError
from lovspor.legaltime.identity import (
    LabelProof,
    Ledger,
    _Validated,
    enacted_id,
    mint_enacted,
)
from lovspor.legaltime.rename_research import (
    _RENAME_SHAPED,
    _SEC,
    UNBOUND,
    UNITS,
    UNKNOWN_MEMBER,
    RenameRefusal,
    _adapted,
    _digest,
    _proof,
    _sha,
)
from lovspor.legaltime.run import events_for, unattributed_for
from lovspor.legaltime.step import (
    Finer,
    FinerKind,
    Insertion,
    Rename,
    Repeal,
    Replace,
    Step,
    _operations,
)
from lovspor.legaltime.structure import Pair, Structure, StructureError, enacted_structure_by_id
from lovspor.legaltime.text import chapter_key, section_key

Step0Policy = Literal["lovspor-step0-label-node-v1"]
STEP0_POLICY: Step0Policy = "lovspor-step0-label-node-v1"

NOT_ENACTING = "member is not the act's enacting announcement"
UNDATED = "enacting member has no publication date"
EMPTY = "enacting member carries no provision"
UNLABELLED = "provision label not read"
MISLABELLED = "printed label and data-name disagree"
DUPLICATE = "label printed twice"
NESTED = "provision nested in another provision"
UNREAD_SECTION = "enclosing section outside the stamp grammar"
OUTLINE = "chapter stated under two parts"

KIND = "operation kind not represented by B1"
RANGE = "range or several sections: unsupported"
NO_SECTION = "operation addresses no section"
UNHEADED = "payload not headed by the addressed label"
UNREAD_CHAPTER = "inserting structure names a chapter it does not read"
UNREAD_PART = "inserting structure names a part it does not read"
TITLE_UNREAD = "chapter title before the provision not read"
CHAPTER_CONFLICT = "inserting structure states two different chapters"
PART_REWRITE = "whole part rewritten: what it leaves out is repealed by no operation"

UNBOUND_IN_MEMBER = "unattributed operation in the member"
UNDATED_IN_MEMBER = "undated operation of the act in the member"
NOT_BUILT = "operation not built"
COMMENCEMENT_STATED = "operation states the act's commencement"

_FINER: dict[str, FinerKind] = {
    "replace_ledd": "ledd", "insert_ledd": "ledd", "repeal_ledd": "ledd", "move_ledd": "ledd",
    "replace_punktum": "punktum", "insert_punktum": "punktum", "repeal_punktum": "punktum",
    "replace_bokstav": "bokstav", "insert_bokstav": "bokstav", "repeal_bokstav": "bokstav",
    "replace_heading": "heading",
}  # fmt: skip
_SECTION_KINDS = frozenset({"insert_section", "replace_section", "repeal_section", *_FINER})
_NAMES_CHAPTER = re.compile(r"\bkapit(?:te)?l(?:er)?\b", re.I)
_SEVERAL_CHAPTERS = re.compile(r"\bkapitler\b|\btil\b", re.I)
_NAMES_PART = re.compile(r"\bdel\b", re.I)
_PRINTED = re.compile(rf"{_SEC}\.?")  # the whole printed label, nothing after it

B1Operation = Rename | Repeal | Replace | Finer | Insertion


class CandidateError(LegalTimeError):
    """An incomplete candidate was asked for an executable step."""


# --------------------------------------------------------------------------- step 0


class Problem(_Validated):
    """One reason step 0 cannot be read; ``node`` is the article's ``data-name``."""

    node: str | None
    reason: str
    detail: str | None = None


class EnactedSource(_Validated):
    """One enacted ``legalArticle`` node: the label, identity and stamp it states."""

    provision_id: str
    label: str
    printed_label: str
    data_name: str
    enclosures: tuple[Enclosure, ...]
    stamp: Pair
    node_sha256: str


class Step0(_Validated):
    """Ledger and structure of one act's step 0, with the source node of every identity."""

    act: str
    member: str
    member_sha256: str
    published: date
    policy: Step0Policy = STEP0_POLICY
    ledger: Ledger
    structure: Structure
    sources: tuple[EnactedSource, ...]

    @model_validator(mode="after")
    def _one_node_each(self) -> Self:
        if [s.provision_id for s in self.sources] != [
            i.provision_id for i in self.ledger.identities
        ]:
            raise ValueError("one source per identity, in identity order")
        for source in self.sources:
            _check_source(self, source)
        return self

    def canonical_json(self) -> str:
        """Byte-stable serialization for tests and evidence; not a report schema."""
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )


def _check_source(step0: Step0, source: EnactedSource) -> None:
    digest = node_digest(step0.member, step0.member_sha256, source.data_name, source.printed_label)
    identity = step0.ledger.get(source.provision_id)
    if identity is None or identity.intervals[0].start.sentence_sha256 != digest:
        raise ValueError(f"node digest does not match {source.provision_id}")
    if source.node_sha256 != digest or identity.label != source.label:
        raise ValueError(f"node digest does not match {source.provision_id}")
    membership = step0.structure.membership(source.provision_id)
    if membership is None or membership.current != source.stamp:
        raise ValueError(f"membership of {source.provision_id} is not its node's stamp")


class Step0Refusal(_Validated):
    """Why no step 0 was built: every problem found, in node order."""

    act: str
    member: str
    member_sha256: str
    problems: tuple[Problem, ...]


def node_digest(member: str, member_sha256: str, data_name: str, printed_label: str) -> str:
    """``STEP0_POLICY``: sha256 of the canonical record of one enacted node (not a sentence)."""
    record = {
        "data_name": data_name, "member": member, "member_sha256": member_sha256,
        "policy": STEP0_POLICY, "printed_label": printed_label,
    }  # fmt: skip
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_step0(document: Document, act: str) -> Step0 | Step0Refusal:
    """Step 0 of ``act`` from its enacting member, or every reason it cannot be read."""
    problems = [*_header_problems(document, act), *_node_problems(document)]  # all, in order
    published = document.header.published_at
    if problems or published is None:
        return _refused(document, act, problems)
    sources = [_source(document, act, p) for p in document.provisions]
    try:
        return _assembled(document, act, published.date(), sources)
    except StructureError as error:
        return _refused(document, act, [Problem(node=None, reason=OUTLINE, detail=str(error))])


def verify_step0(document: Document, step0: Step0) -> bool:
    """True when ``document`` rebuilds ``step0`` byte for byte."""
    rebuilt = build_step0(document, step0.act)
    return isinstance(rebuilt, Step0) and rebuilt.canonical_json() == step0.canonical_json()


def _refused(document: Document, act: str, problems: list[Problem]) -> Step0Refusal:
    return Step0Refusal(
        act=act, member=document.member, member_sha256=document.sha256, problems=tuple(problems)
    )


def _header_problems(document: Document, act: str) -> list[Problem]:
    found = []
    if document.header.refid != act:
        found.append(Problem(node=None, reason=NOT_ENACTING, detail=document.header.refid))
    if document.header.published_at is None:
        found.append(Problem(node=None, reason=UNDATED))
    if not document.provisions:
        found.append(Problem(node=None, reason=EMPTY))
    return found


def _node_problems(document: Document) -> list[Problem]:
    keys = [section_key(p.label) for p in document.provisions]
    found = []
    for provision, key in zip(document.provisions, keys, strict=True):
        node = provision.key
        if key is None:
            found.append(Problem(node=node, reason=UNLABELLED))
        elif _exact_key(provision) != key:
            found.append(Problem(node=node, reason=MISLABELLED, detail=provision.label))
        elif keys.count(key) > 1:
            found.append(Problem(node=node, reason=DUPLICATE))
        found.extend(_hierarchy_problems(provision))
    return found


def _hierarchy_problems(provision: Provision) -> list[Problem]:
    """The enclosing sections must read as at most one chapter inside at most one part."""
    node, enclosures = provision.key, provision.enclosures
    if any(e.tag == "article" for e in enclosures):
        return [Problem(node=node, reason=NESTED)]
    kinds = []
    for enclosure in enclosures:
        kind = _kind_of(enclosure.name)
        if kind is None:
            return [Problem(node=node, reason=UNREAD_SECTION, detail=enclosure.name)]
        kinds.append(kind)
    if kinds not in ([], ["chapter"], ["part"], ["chapter", "part"]):
        detail = ">".join(str(e.name) for e in enclosures)
        return [Problem(node=node, reason=UNREAD_SECTION, detail=detail)]
    return []


def _exact_key(provision: Provision) -> str | None:
    """The section key a node states only when its printed label is that label, and its
    ``data-name`` that key: ``section_key`` alone searches a substring (``§ 5 junk``)."""
    key = section_key(provision.label)
    if key is None or not _PRINTED.fullmatch(provision.label) or provision.key != key:
        return None
    return key


def _kind_of(name: str | None) -> Literal["chapter", "part"] | None:
    if name is None:
        return None
    if _CHAPTER_NAME.fullmatch(name.lower()):
        return "chapter"
    return "part" if _PART_NAME.fullmatch(name.lower()) else None


def _source(document: Document, act: str, provision: Provision) -> EnactedSource:
    label = str(section_key(provision.label))
    return EnactedSource(
        provision_id=enacted_id(act, document.member, label), label=label,
        printed_label=provision.label, data_name=provision.key,
        enclosures=provision.enclosures,
        stamp=Pair(chapter=provision.chapter, part=provision.part),
        node_sha256=node_digest(document.member, document.sha256, provision.key, provision.label),
    )  # fmt: skip


def _assembled(
    document: Document, act: str, published: date, sources: list[EnactedSource]
) -> Step0:
    proofs = [(s.label, _node_proof(document, act, published, s)) for s in sources]
    ledger = mint_enacted(act, document.member, published, proofs)
    structure = enacted_structure_by_id(ledger, {s.provision_id: s.stamp for s in sources})
    return Step0(
        act=act, member=document.member, member_sha256=document.sha256, published=published,
        ledger=ledger, structure=structure,
        sources=tuple(sorted(sources, key=lambda s: s.provision_id)),
    )  # fmt: skip


def _node_proof(document: Document, act: str, published: date, source: EnactedSource) -> LabelProof:
    return LabelProof(
        instrument=act, op_id=f"{act}#enacted:{source.label}", member=document.member,
        member_sha256=document.sha256, sentence_sha256=source.node_sha256, effective=None,
        known_from=published, label_minted_at=published,
    )  # fmt: skip


# ----------------------------------------------------------------------- operations


class PlacementEvidence(_Validated):
    """Where an insertion's pair came from; ``inherited`` is never used as a placement.

    ``stated`` is the pair the inserting structure proves, or None when it states none or
    names a stamp that was not read. ``inherited`` is ``_enclosing``'s reading of the
    member that carries the provision, recomputed from the raw ``enclosures``.
    """

    stated: Pair | None
    sentence_sha256: str | None = None
    title_sha256: str | None = None
    chapter_from: Literal["sentence", "title"] | None = None
    inherited: Pair
    enclosures: tuple[Enclosure, ...]


class BuiltOperation(_Validated):
    """One event as a B1 operation, with its group key and placement evidence."""

    op_id: str
    kind: str
    act: str
    member: str
    effective: date
    operation: B1Operation
    placement: PlacementEvidence | None = None


class Unbuilt(_Validated):
    """An event no B1 operation represents, with its provenance and reason."""

    op_id: str
    kind: str
    act: str | None
    member: str
    member_sha256: str | None
    sentence_sha256: str
    reason: str
    timing_status: str
    effective: date | None
    known_from: date | None
    placement: PlacementEvidence | None = None


def build_operation(
    event: Event, documents: Mapping[str, Document]
) -> BuiltOperation | Unbuilt | RenameRefusal:
    """The B1 operation one attributed, dated event proves, or why there is none."""
    if _RENAME_SHAPED.search(event.operation.instruction):
        outcome = _adapted(event, documents)
        return outcome if isinstance(outcome, RenameRefusal) else _built(event, outcome)
    document = documents.get(event.member)
    reason = _gate(event, document)
    if reason is not None or document is None:
        return _unbuilt(event, document, reason or UNKNOWN_MEMBER)
    return _typed(event, document)


def _gate(event: Event, document: Document | None) -> str | None:
    timing = event.timing
    if document is None:
        return UNKNOWN_MEMBER
    if event.operation.act is None:
        return UNBOUND
    if timing.status != "dated":
        return f"timing not proven: {timing.status}: {timing.reason}"
    return None


def _typed(event: Event, document: Document) -> BuiltOperation | Unbuilt:
    op = event.operation
    if "§§" in op.instruction:
        return _unbuilt(event, document, RANGE)
    if op.kind not in _SECTION_KINDS:
        return _unbuilt(event, document, f"{KIND}: {op.kind}")
    if op.address.section is None:
        return _unbuilt(event, document, NO_SECTION)
    return _section_operation(event, document, op.address.section)


def _section_operation(event: Event, document: Document, label: str) -> BuiltOperation | Unbuilt:
    op, proof = event.operation, _proof(event, document)
    if op.kind == "repeal_section":
        return _built(event, Repeal(label=label, proof=proof))
    if op.kind in _FINER:
        return _built(event, Finer(label=label, kind=_FINER[op.kind], proof=proof))
    payload = op.new_provision
    if payload is None or _exact_key(payload) != label:
        return _unbuilt(event, document, UNHEADED)
    if op.kind == "replace_section":
        return _built(event, Replace(label=label, proof=proof, payload_sha256=_digest(payload)))
    return _insertion(event, document, payload, proof)


def _insertion(
    event: Event, document: Document, payload: Provision, proof: LabelProof
) -> BuiltOperation | Unbuilt:
    evidence, problem = _placement(event.operation.stated, payload)
    if problem is not None:
        return _unbuilt(event, document, problem, evidence)
    label = str(event.operation.address.section)
    insertion = Insertion(label=label, proof=proof, placement=evidence.stated)
    return _built(event, insertion, evidence)


def _placement(
    stated: StatedPlacement | None, payload: Provision
) -> tuple[PlacementEvidence, str | None]:
    inherited = _inherited(payload.enclosures)
    if stated is None:
        return PlacementEvidence(
            stated=None, inherited=inherited, enclosures=payload.enclosures
        ), None
    problem = _unread(stated, payload)
    pair = None if problem else Pair(chapter=payload.chapter, part=payload.part)
    evidence = PlacementEvidence(
        stated=pair, sentence_sha256=_sha(stated.sentence),
        title_sha256=None if stated.title is None else _sha(stated.title),
        chapter_from=stated.chapter_from, inherited=inherited, enclosures=payload.enclosures,
    )  # fmt: skip
    return evidence, problem


def _unread(stated: StatedPlacement, payload: Provision) -> str | None:
    """The first stamp the inserting structure names but did not read, if any."""
    if stated.rewrites_part:
        return PART_REWRITE
    if stated.title is not None and chapter_key(stated.title) is None:
        return TITLE_UNREAD
    if _contradicted(stated.sentence, payload.chapter):
        return CHAPTER_CONFLICT
    return _unstated(stated, payload)


def _unstated(stated: StatedPlacement, payload: Provision) -> str | None:
    """A chapter or part the sentence names that no read text stamped."""
    sentence = stated.sentence
    if _NAMES_CHAPTER.search(sentence) and payload.chapter is None:
        return UNREAD_CHAPTER
    if stated.chapter_from == "sentence" and _SEVERAL_CHAPTERS.search(sentence):
        return UNREAD_CHAPTER
    if _NAMES_PART.search(sentence) and payload.part is None:
        return UNREAD_PART
    return UNREAD_CHAPTER if payload.chapter is None and payload.part is None else None


def _contradicted(sentence: str, chapter: str | None) -> bool:
    """A sentence naming exactly one chapter, and a title that stamped another."""
    named = chapter_key(sentence)
    return named is not None and not _SEVERAL_CHAPTERS.search(sentence) and chapter != named


def _inherited(enclosures: tuple[Enclosure, ...]) -> Pair:
    """``document._enclosing``'s reading, from the raw enclosures it read."""
    chapter = part = None
    for enclosure in enclosures:
        name = (enclosure.name or "").lower()
        if enclosure.tag != "section" or not name:
            continue
        if chapter is None and _CHAPTER_NAME.fullmatch(name):
            chapter = name
        elif part is None and _PART_NAME.fullmatch(name):
            part = name
    return Pair(chapter=chapter, part=part)


def _built(
    event: Event, operation: B1Operation, evidence: PlacementEvidence | None = None
) -> BuiltOperation:
    return BuiltOperation(
        op_id=event.operation.id, kind=event.operation.kind, act=str(event.operation.act),
        member=event.member, effective=_proven(event), operation=operation, placement=evidence,
    )  # fmt: skip


def _proven(event: Event) -> date:
    effective = event.timing.dated_on
    if effective is None:  # unreachable: _gate and the B2 adapter build dated events only
        raise CandidateError(f"an undated event is never built: {event.operation.id}")
    return effective


def _unbuilt(
    event: Event, document: Document | None, reason: str, evidence: PlacementEvidence | None = None
) -> Unbuilt:
    op, timing = event.operation, event.timing
    return Unbuilt(
        op_id=op.id, kind=op.kind, act=op.act, member=event.member,
        member_sha256=None if document is None else document.sha256,
        sentence_sha256=_sha(op.instruction), reason=reason, timing_status=timing.status,
        effective=timing.dated_on, known_from=timing.known_from, placement=evidence,
    )  # fmt: skip


# ----------------------------------------------------------------------- candidates

Group = tuple[str, str, date]
Outcome = BuiltOperation | Unbuilt | RenameRefusal


class Candidate(_Validated):
    """One (act, member, proven effective date) group as a B1 step; complete or not."""

    act: str
    member: str
    effective: date
    step: Step
    evidence: tuple[BuiltOperation, ...]
    gaps: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.gaps

    def executable(self) -> Step:
        """The step, only when nothing in its scope is left unrepresented."""
        if self.gaps:
            raise CandidateError(f"candidate is incomplete: {len(self.gaps)} gaps")
        return self.step

    @model_validator(mode="after")
    def _own_group(self) -> Self:
        if (self.step.act, self.step.member, self.step.effective) != (
            self.act, self.member, self.effective,
        ):  # fmt: skip
            raise ValueError("a candidate's step is its own group")
        ids = sorted(op.proof.op_id for op in _operations(self.step))
        if ids != sorted(b.op_id for b in self.evidence):
            raise ValueError("every step operation is one built operation")
        return self


class MemberGap(_Validated):
    """A member whose commencement units no B1 step represents (R8)."""

    member: str
    reason: str


class Build(_Validated):
    candidates: tuple[Candidate, ...] = ()
    unbuilt: tuple[Unbuilt, ...] = ()
    rename_refusals: tuple[RenameRefusal, ...] = ()
    member_gaps: tuple[MemberGap, ...] = ()

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def build_candidates(
    events: Iterable[Event],
    documents: Mapping[str, Document],
    decision_members: frozenset[str] = frozenset(),
) -> Build:
    """Every event built or refused, and one candidate per group of built operations."""
    ordered = sorted(events, key=lambda e: (e.member, e.operation.id, e.operation.act or ""))
    outcomes = [(event, build_operation(event, documents)) for event in ordered]
    groups: defaultdict[Group, list[BuiltOperation]] = defaultdict(list)
    for _, outcome in outcomes:
        if isinstance(outcome, BuiltOperation):
            groups[(outcome.act, outcome.member, outcome.effective)].append(outcome)
    candidates = [_candidate(g, built, outcomes, decision_members) for g, built in groups.items()]
    return Build(
        candidates=tuple(sorted(candidates, key=lambda c: (c.act, c.effective, c.member))),
        unbuilt=tuple(o for _, o in outcomes if isinstance(o, Unbuilt)),
        rename_refusals=tuple(o for _, o in outcomes if isinstance(o, RenameRefusal)),
        member_gaps=tuple(MemberGap(member=m, reason=UNITS) for m in sorted(decision_members)),
    )


def build_evidence(evidence: Evidence) -> Build:
    """``build_candidates`` over one act's collected evidence, bound and unbound."""
    events = [*events_for(evidence), *unattributed_for(evidence)]
    documents = {document.member: document for document in evidence.amenders}
    decisions = frozenset(document.member for document in evidence.decisions)
    return build_candidates(events, documents, decisions)


def _candidate(
    group: Group,
    built: list[BuiltOperation],
    outcomes: list[tuple[Event, Outcome]],
    decision_members: frozenset[str],
) -> Candidate:
    act, member, effective = group
    ops = [b.operation for b in built]
    step = Step(
        act=act, member=member, effective=effective,
        renames=tuple(o for o in ops if isinstance(o, Rename)),
        repeals=tuple(o for o in ops if isinstance(o, Repeal)),
        replaces=tuple(o for o in ops if isinstance(o, Replace)),
        finer=tuple(o for o in ops if isinstance(o, Finer)),
        insertions=tuple(o for o in ops if isinstance(o, Insertion)),
    )  # fmt: skip
    gaps = [g for event, outcome in outcomes if (g := _gap(group, event, outcome)) is not None]
    if member in decision_members:
        gaps.append(UNITS)
    return Candidate(
        act=act, member=member, effective=effective, step=step, evidence=tuple(built),
        gaps=tuple(gaps),
    )  # fmt: skip


def _gap(group: Group, event: Event, outcome: Outcome) -> str | None:
    """What one event of the member leaves unrepresented in the group's scope."""
    act, member, effective = group
    op = event.operation
    if event.member != member or op.act not in {act, None}:
        return None
    if op.act is None:
        return f"{UNBOUND_IN_MEMBER}: {op.id}"
    if isinstance(outcome, BuiltOperation):
        stated = outcome.effective == effective and _states_commencement(event)
        return f"{COMMENCEMENT_STATED}: {op.id}" if stated else None
    return _unrepresented(effective, event, outcome.reason)


def _unrepresented(effective: date, event: Event, reason: str) -> str | None:
    """An event no operation represents is a gap of its own date, and of every date if undated."""
    op, dated = event.operation, event.timing.dated_on
    if dated is None:
        return f"{UNDATED_IN_MEMBER}: {op.id} ({reason})"
    return f"{NOT_BUILT}: {op.id} ({op.kind}): {reason}" if dated == effective else None


def _states_commencement(event: Event) -> bool:
    sentence = _payload_sentence(event)
    return sentence is not None and _DEFAULT_CLAUSE.match(sentence) is not None
