"""ADR-0023 Phase B3: the structure map ``S``, placement history and R3's placement grammar.

Synthetic, straffeloven-shaped acts. Each step is executed through B1's ``apply_step``
with ``S_P``, so acceptance, ``Q`` and ``S_Q`` come from one set of accepted operations.
"""

import json
import random
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.identity import (
    EndProof,
    LabelProof,
    Ledger,
    enacted_id,
    inserted_id,
    mint_enacted,
)
from lovspor.legaltime.step import Insertion, Rename, Repeal, Step, StepResult, apply_step
from lovspor.legaltime.structure import (
    INCOMPLETE,
    NO_STRUCTURE,
    OUTLINE_CONFLICT,
    OUTSIDE,
    UNSTATED,
    ChapterEntry,
    Clause,
    Membership,
    Pair,
    PlacementEvent,
    Structure,
    StructureError,
    _structure,
    enacted_structure,
    paired,
    read_clause,
    stamps_of,
)

ACT = "lov/2005-05-20-28"
OTHER = "lov/2005-06-17-62"
ENACTING = "lti/2005/nl-20050520-028.xml"
MEMBER = "lti/2009/nl-20090619-074.xml"
PUBLISHED = date(2005, 5, 20)
EFFECTIVE = date(2009, 6, 19)
CONFLICT = "conflicting operations within instrument"
KAP1 = Pair(chapter="kap1", part="del1")
KAP16 = Pair(chapter="kap16", part="del2")

# §§ 1-2 in kap1/del1, § 3 in kap2/del1, § 4 in kap3/del2, § 5 in kap7/del2,
# §§ 101-102 directly under del3; kapvii stands in del2 (a roman data-name).
STAMPS = {
    "§1": Pair(chapter="kap1", part="del1"),
    "§2": Pair(chapter="kap1", part="del1"),
    "§3": Pair(chapter="kap2", part="del1"),
    "§4": Pair(chapter="kap3", part="del2"),
    "§5": Pair(chapter="kapvii", part="del2"),
    "§101": Pair(chapter=None, part="del3"),
    "§102": Pair(chapter=None, part="del3"),
}


def _hex(seed: str) -> str:
    return (seed.encode().hex() * 64)[:64]


def _start0(label: str, act: str = ACT) -> LabelProof:
    return LabelProof(
        instrument=act, op_id=f"{act}#enacted:{label}", member=ENACTING,
        member_sha256=_hex("m2005"), sentence_sha256=_hex(label), effective=None,
        known_from=PUBLISHED, label_minted_at=PUBLISHED,
    )  # fmt: skip


def _p(n: int, effective: date = EFFECTIVE) -> LabelProof:
    return LabelProof(
        instrument="lov/2009-06-19-74", op_id=f"lov/2009-06-19-74#1:I:{n}", member=MEMBER,
        member_sha256=_hex(MEMBER), sentence_sha256=_hex(f"s{n:03d}"), effective=effective,
        known_from=EFFECTIVE,
    )  # fmt: skip


def _state(stamps: dict[str, Pair] = STAMPS, act: str = ACT) -> tuple[Ledger, Structure]:
    ledger = mint_enacted(act, ENACTING, PUBLISHED, [(x, _start0(x, act)) for x in stamps])
    return ledger, enacted_structure(ledger, stamps)


def _step(**operations: object) -> Step:
    return Step.model_validate({"act": ACT, "member": MEMBER, "effective": EFFECTIVE, **operations})


def _run(step: Step, stamps: dict[str, Pair] = STAMPS) -> tuple[StepResult, Ledger, Structure]:
    ledger, structure = _state(stamps)
    return apply_step(ledger, step, structure), ledger, structure


def _old(label: str) -> str:
    return enacted_id(ACT, ENACTING, label)


def _new(label: str) -> str:
    return inserted_id(ACT, MEMBER, EFFECTIVE, label)


def _pair(result: StepResult, pid: str) -> Pair | None:
    assert result.structure is not None
    return result.structure.live_map()[pid]


def _why(result: StepResult) -> dict[str, tuple[str, str]]:
    return {r.op_id.rsplit(":", 1)[1]: (r.phase, r.reason) for r in result.refusals}


def _unchanged(result: StepResult, ledger: Ledger, structure: Structure) -> bool:
    """I4: a refused step leaves every label, pair and history byte-identical."""
    assert result.structure is not None
    same_ledger = result.ledger.canonical_json() == ledger.canonical_json()
    return same_ledger and result.structure.canonical_json() == structure.canonical_json()


# ------------------------------------------------------------------- R3 closed grammar


@pytest.mark.parametrize(
    ("clause", "read"),
    [
        ("som innleder tredje del", Clause(part="del3")),
        ("som innleder  første del", Clause(part="del1")),
        ("som innleder annen del", Clause(part="del2")),
        ("under kapittel 7", Clause(chapter="kap7")),
        ("under kapittel 9 A", Clause(chapter="kap9a")),
        ("under kapittel VII", Clause(chapter="kapvii")),
    ],
)
def test_the_closed_grammar_reads_a_part_or_a_chapter(clause: str, read: Clause) -> None:
    assert read_clause(clause) == read


@pytest.mark.parametrize(
    "clause",
    [
        "som innleder tredje og fjerde del",
        "som innleiar tredje del",
        "som innleder del 3",
        "som innleder sjuende kapittel",
        "under kapitlene 3 og 4",
        "under kapittel",
        "under kapittel 3 i annen del",
        "i kapittel 3",
        "som innleder tullete del",
    ],
)
def test_any_other_clause_is_outside_the_grammar(clause: str) -> None:
    assert read_clause(clause) is None


# --------------------------------------------------------------------------- step 0


def test_step_0_stamps_each_identity_and_states_the_outline() -> None:
    ledger, structure = _state()
    paired(ledger, structure)
    assert structure.live_map()[_old("§101")] == Pair(chapter=None, part="del3")
    assert {e.chapter: e.part for e in structure.outline} == {
        "kap1": "del1", "kap2": "del1", "kap3": "del2", "kapvii": "del2",
    }  # fmt: skip
    kap1 = structure.enclosing("kap1")
    assert kap1 is not None and kap1.witness == _old("§1")  # the least id stating it
    assert kap1.proof == _start0("§1")
    assert structure.parts() == {"del1", "del2", "del3"}


@pytest.mark.parametrize(
    ("stamps", "message"),
    [
        ({k: v for k, v in STAMPS.items() if k != "§3"}, "disagree"),
        ({**STAMPS, "§9": Pair(chapter="kap1", part="del1")}, "disagree"),
    ],
)
def test_step_0_refuses_a_missing_or_extra_stamp(stamps: dict[str, Pair], message: str) -> None:
    ledger, _ = _state()
    with pytest.raises(StructureError, match=message):
        enacted_structure(ledger, stamps)


def test_step_0_refuses_a_chapter_stated_under_two_parts() -> None:
    stamps = {**STAMPS, "§2": Pair(chapter="kap1", part="del2")}
    with pytest.raises(StructureError, match="kap1 is stated under del1 and del2"):
        _state(stamps)


def test_step_0_stamps_enacted_identities_only() -> None:
    ledger, structure = _state()
    result = apply_step(ledger, _step(insertions=(Insertion(label="§9", proof=_p(1)),)), structure)
    with pytest.raises(StructureError, match="enacted identities only"):
        enacted_structure(result.ledger, {**STAMPS, "§9": Pair(chapter="kap1", part="del1")})


# ----------------------------------------------------------------- part-only clauses


def test_a_part_only_clause_on_an_empty_chapter_places_the_identity() -> None:
    """Chain A: § 401 (enacted § 101, empty chapter) blir § 411 som innleder tredje del."""
    rename = Rename(source="§101", target="§411", proof=_p(1), placement="som innleder tredje del")
    result, _, structure = _run(_step(renames=(rename,)))
    assert result.refusals == ()
    assert result.ledger.holder("§411") is not None
    assert _pair(result, _old("§101")) == Pair(chapter=None, part="del3")
    assert result.structure is not None
    membership = result.structure.membership(_old("§101"))
    assert membership is not None
    enacted, placed = membership.events
    assert enacted == structure.membership(_old("§101")).events[0]  # type: ignore[union-attr]
    assert (placed.cause, placed.clause, placed.proof) == (
        "placed",
        "som innleder tredje del",
        _p(1),
    )


@pytest.mark.parametrize(
    ("source", "clause"),
    [
        ("§4", "som innleder annen del"),  # chapter kap3 lies in the same part: none is kept
        ("§4", "som innleder tredje del"),  # chapter in another part
        ("§101", "som innleder femte del"),  # a part the structure never states
    ],
)
def test_a_part_only_clause_without_a_proven_empty_chapter_refuses_everything(
    source: str, clause: str
) -> None:
    rename = Rename(source=source, target="§411", proof=_p(1), placement=clause)
    result, ledger, structure = _run(_step(renames=(rename,)))
    assert _why(result) == {"1": ("intrinsic", INCOMPLETE)}
    assert _unchanged(result, ledger, structure)  # label not moved, no pair, no history


def test_a_part_only_clause_on_an_unresolved_pair_is_incomplete() -> None:
    ledger, structure = _state()
    inserted = apply_step(
        ledger, _step(insertions=(Insertion(label="§9", proof=_p(1)),)), structure
    )
    assert inserted.structure is not None
    later = Step(
        act=ACT, member="lti/2010/nl-20100101-001.xml", effective=date(2010, 1, 1),
        renames=(Rename(source="§9", target="§10", placement="som innleder tredje del",
                        proof=_p(2).model_copy(update={
                            "member": "lti/2010/nl-20100101-001.xml",
                            "effective": date(2010, 1, 1)})),),
    )  # fmt: skip
    result = apply_step(inserted.ledger, later, inserted.structure)
    assert [(r.phase, r.reason) for r in result.refusals] == [("intrinsic", INCOMPLETE)]


# -------------------------------------------------------------- chapter-only clauses


def test_a_chapter_only_clause_takes_the_part_the_outline_encloses() -> None:
    rename = Rename(source="§3", target="§14", proof=_p(1), placement="under kapittel 3")
    result, _, _ = _run(_step(renames=(rename,)))
    assert result.refusals == ()
    assert _pair(result, _old("§3")) == Pair(chapter="kap3", part="del2")  # move and rename


def test_a_roman_chapter_is_found_only_where_the_outline_holds_it() -> None:
    rename = Rename(source="§3", target="§14", proof=_p(1), placement="under kapittel VII")
    result, _, _ = _run(_step(renames=(rename,)))
    assert _pair(result, _old("§3")) == Pair(chapter="kapvii", part="del2")


@pytest.mark.parametrize(
    ("clause", "reason"),
    [("under kapittel 9", INCOMPLETE), ("under kapittel 3 og 4", OUTSIDE)],
)
def test_an_unknown_chapter_or_an_unread_clause_refuses_the_whole_operation(
    clause: str, reason: str
) -> None:
    rename = Rename(source="§3", target="§14", proof=_p(1), placement=clause)
    result, ledger, structure = _run(_step(renames=(rename,)))
    assert _why(result) == {"1": ("intrinsic", reason)}
    assert _unchanged(result, ledger, structure)


def test_a_clause_is_read_against_the_outline_before_the_step() -> None:
    """A chapter the same step's insertion first states is not in ``S_P``'s outline."""
    insertion = Insertion(label="§20", proof=_p(1), placement=Pair(chapter="kap9", part="del2"))
    rename = Rename(source="§3", target="§14", proof=_p(2), placement="under kapittel 9")
    result, _, _ = _run(_step(renames=(rename,), insertions=(insertion,)))
    assert _why(result) == {"2": ("intrinsic", INCOMPLETE)}
    assert _pair(result, _new("§20")) == Pair(chapter="kap9", part="del2")


def test_without_s_a_placement_is_never_accepted() -> None:
    ledger, _ = _state()
    rename = Rename(source="§101", target="§411", proof=_p(1), placement="som innleder tredje del")
    result = apply_step(ledger, _step(renames=(rename,)))
    assert _why(result) == {"1": ("intrinsic", NO_STRUCTURE)}
    assert result.structure is None and result.ledger == ledger


# ----------------------------------------------------------- renames keep identities


def test_a_rename_without_placement_keeps_its_own_stamps_not_the_labels_holder() -> None:
    """§ 6's previous holder (§ 3, kap2) is repealed; § 1 (kap1) takes § 3 and stays in kap1."""
    step = _step(
        renames=(Rename(source="§1", target="§3", proof=_p(1)),),
        repeals=(Repeal(label="§3", proof=_p(2)),),
    )
    result, _, _ = _run(step)
    assert result.refusals == ()
    assert result.ledger.holder("§3").provision_id == _old("§1")  # type: ignore[union-attr]
    assert _pair(result, _old("§1")) == Pair(chapter="kap1", part="del1")
    membership = result.structure.membership(_old("§1"))  # type: ignore[union-attr]
    assert membership is not None and len(membership.events) == 1  # no placement event


def test_a_cycle_moves_identities_and_their_pairs_together() -> None:
    renames = (
        Rename(source="§1", target="§2", proof=_p(1)),
        Rename(source="§2", target="§3", proof=_p(2), placement="under kapittel 3"),
        Rename(source="§3", target="§1", proof=_p(3)),
    )
    result, _, _ = _run(_step(renames=renames))
    assert result.refusals == ()
    holders = {label: result.ledger.holder(label).provision_id for label in ("§1", "§2", "§3")}  # type: ignore[union-attr]
    assert holders == {"§1": _old("§3"), "§2": _old("§1"), "§3": _old("§2")}
    assert _pair(result, _old("§1")) == Pair(chapter="kap1", part="del1")
    assert _pair(result, _old("§2")) == Pair(chapter="kap3", part="del2")
    assert _pair(result, _old("§3")) == Pair(chapter="kap2", part="del1")


# ------------------------------------------------------------- insertion and repeal


def test_an_insertion_takes_its_stated_pair_and_registers_a_new_chapter() -> None:
    insertion = Insertion(label="§20", proof=_p(1), placement=Pair(chapter="kap16", part="del2"))
    result, _, _ = _run(_step(insertions=(insertion,)))
    assert result.structure is not None
    entry = result.structure.enclosing("kap16")
    assert entry == ChapterEntry(chapter="kap16", part="del2", witness=_new("§20"), proof=_p(1))
    (event,) = result.structure.membership(_new("§20")).events  # type: ignore[union-attr]
    assert (event.cause, event.pair, event.proof) == ("inserted", insertion.placement, _p(1))


@pytest.mark.parametrize(
    "stated",
    [None, Pair(chapter="kap16", part=None)],  # nothing stated; a part unknown in a parted act
)
def test_an_insertion_whose_pair_is_not_complete_is_unresolved_never_guessed(
    stated: Pair | None,
) -> None:
    result, _, _ = _run(_step(insertions=(Insertion(label="§20", proof=_p(1), placement=stated),)))
    assert result.refusals == () and result.ledger.holder("§20") is not None  # label layer
    assert _pair(result, _new("§20")) is None
    (event,) = result.structure.membership(_new("§20")).events  # type: ignore[union-attr]
    assert event.unresolved == UNSTATED
    assert result.structure.enclosing("kap16") is None  # type: ignore[union-attr]


def test_in_an_act_without_parts_a_chapter_alone_is_complete() -> None:
    stamps = {"§1": Pair(chapter="kap1"), "§2": Pair()}
    insertion = Insertion(label="§20", proof=_p(1), placement=Pair(chapter="kap2"))
    result, _, _ = _run(_step(insertions=(insertion,)), stamps)
    assert _pair(result, _new("§20")) == Pair(chapter="kap2", part=None)


def test_an_insertion_against_the_outline_is_refused() -> None:
    insertion = Insertion(label="§20", proof=_p(1), placement=Pair(chapter="kap1", part="del2"))
    result, ledger, structure = _run(_step(insertions=(insertion,)))
    assert _why(result) == {"1": ("intrinsic", OUTLINE_CONFLICT)}
    assert _unchanged(result, ledger, structure)


def test_two_insertions_stating_one_new_chapter_under_two_parts_are_both_refused() -> None:
    a = Insertion(label="§20", proof=_p(1), placement=Pair(chapter="kap16", part="del2"))
    b = Insertion(label="§21", proof=_p(2), placement=Pair(chapter="kap16", part="del3"))
    result, ledger, structure = _run(_step(insertions=(a, b)))
    reason = ("relational", "conflicting placements within instrument")
    assert _why(result) == {"1": reason, "2": reason}
    assert _unchanged(result, ledger, structure)


def test_repeal_and_refill_end_one_membership_and_start_another() -> None:
    step = _step(
        repeals=(Repeal(label="§4", proof=_p(1)),),
        insertions=(Insertion(label="§4", proof=_p(2), placement=KAP1),),
    )  # fmt: skip
    result, _, structure = _run(step)
    assert result.refusals == () and result.structure is not None
    old = result.structure.membership(_old("§4"))
    assert old is not None and not old.live and old.current is None
    assert old.events == structure.membership(_old("§4")).events  # type: ignore[union-attr]
    assert old.removed == EndProof(**_p(1).model_dump(), ended_by="repeal")
    assert _old("§4") not in result.structure.live_map()
    assert _pair(result, _new("§4")) == Pair(chapter="kap1", part="del1")
    paired(result.ledger, result.structure)


# --------------------------------------------------------- relational and components


def test_two_placements_of_one_identity_conflict_within_the_instrument() -> None:
    renames = (
        Rename(source="§3", target="§14", proof=_p(1), placement="under kapittel 3"),
        Rename(source="§3", target="§15", proof=_p(2), placement="under kapittel 1"),
    )
    result, ledger, structure = _run(_step(renames=renames))
    assert _why(result) == {"1": ("relational", CONFLICT), "2": ("relational", CONFLICT)}
    assert _unchanged(result, ledger, structure)


def test_two_plain_renames_of_one_source_stay_conflicting_renames() -> None:
    renames = (
        Rename(source="§3", target="§14", proof=_p(1)),
        Rename(source="§3", target="§15", proof=_p(2), placement="under kapittel 3"),
        Rename(source="§3", target="§16", proof=_p(3)),
    )
    result, _, _ = _run(_step(renames=renames))
    assert {r for _, r in _why(result).values()} == {"conflicting renames"}


def test_a_refused_component_leaves_a_valid_independent_one_applied() -> None:
    renames = (
        Rename(source="§1", target="§2", proof=_p(1)),  # § 2 is kept: a collision
        Rename(source="§101", target="§411", proof=_p(2), placement="som innleder tredje del"),
    )
    result, _, structure = _run(_step(renames=renames))
    assert _why(result) == {"1": ("relational", "label collision")}
    assert result.accepted == (_p(2).op_id,)
    assert _pair(result, _old("§101")) == Pair(chapter=None, part="del3")
    after = result.structure.membership(_old("§1"))  # type: ignore[union-attr]
    assert after == structure.membership(_old("§1"))


def test_an_intrinsic_placement_refusal_creates_no_edge() -> None:
    """The refused rename vacates nothing, so the refill of § 3 meets its holder (R5)."""
    step = _step(
        renames=(Rename(source="§3", target="§14", proof=_p(1), placement="under kapittel 9"),),
        insertions=(Insertion(label="§3", proof=_p(2)),),
    )
    result, ledger, structure = _run(step)
    assert _why(result) == {
        "1": ("intrinsic", INCOMPLETE), "2": ("relational", "label collision"),
    }  # fmt: skip
    assert _unchanged(result, ledger, structure)


def test_an_undated_step_changes_no_structure() -> None:
    rename = Rename(
        source="§101", target="§411", placement="som innleder tredje del",
        proof=_p(1).model_copy(update={"effective": None, "label_minted_at": EFFECTIVE}),
    )  # fmt: skip
    ledger, structure = _state()
    undated = Step(act=ACT, member=MEMBER, effective=None, renames=(rename,))
    result = apply_step(ledger, undated, structure)
    assert _why(result) == {"1": ("intrinsic", "undated step")}
    assert _unchanged(result, ledger, structure)


# ---------------------------------------------------------------- act isolation (R8)


def test_a_structure_of_another_act_is_never_paired() -> None:
    ledger, _ = _state()
    _, foreign = _state(act=OTHER)
    with pytest.raises(StructureError, match="a structure of"):
        apply_step(ledger, _step(), foreign)


def test_a_structure_naming_other_identities_is_a_hard_failure() -> None:
    ledger, structure = _state()
    smaller, _ = _state({k: v for k, v in STAMPS.items() if k != "§1"})
    with pytest.raises(StructureError, match="name other identities"):
        paired(smaller, structure)
    assert ledger.act == structure.act


# ------------------------------------------------------------------ malformed proofs


def _event(**update: object) -> dict[str, object]:
    base = {"cause": "inserted", "pair": Pair(chapter="kap1", part="del1"), "proof": _p(1)}
    return {**base, **update}


@pytest.mark.parametrize(
    "fields",
    [
        _event(pair=None),  # no pair and no reason
        _event(unresolved="why"),  # a pair and a reason
        _event(cause="placed"),  # a placement without its clause
        _event(clause="under kapittel 1"),  # a clause on a minting
        _event(cause="enacted"),  # step 0 with an effective date
        _event(cause="placed", clause="x", pair=None, unresolved="why"),  # an unproven placement
    ],
)
def test_a_malformed_placement_event_is_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlacementEvent.model_validate(fields)


@pytest.mark.parametrize(
    "pair", [{"chapter": "Kapittel 1"}, {"part": "tredje"}, {"chapter": "kap"}]
)
def test_a_stamp_outside_the_data_name_form_is_refused(pair: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Pair.model_validate(pair)


def test_a_membership_must_start_at_its_minting_and_end_only_by_repeal() -> None:
    placed = PlacementEvent(cause="placed", pair=Pair(part="del3"), proof=_p(1), clause="c")
    minted = PlacementEvent.model_validate(_event())
    with pytest.raises(ValidationError, match="starts at its minting"):
        Membership(provision_id="x", events=(placed,))
    with pytest.raises(ValidationError, match="only placement events follow"):
        Membership(provision_id="x", events=(minted, minted))
    with pytest.raises(ValidationError, match="only a repeal"):
        Membership(provision_id="x", events=(minted,),
                   removed=EndProof(**_p(2).model_dump(), ended_by="rename"))  # fmt: skip


def test_a_structure_whose_pair_the_outline_does_not_state_is_refused() -> None:
    _, structure = _state()
    stray = PlacementEvent.model_validate(_event(pair=Pair(chapter="kap99", part="del1")))
    with pytest.raises(ValidationError, match="outline does not state"):
        structure.model_copy(
            update={
                "memberships": (
                    *structure.memberships,
                    Membership(provision_id="z|", events=(stray,)),
                )
            }
        )
    with pytest.raises(ValidationError, match="unique"):
        structure.model_copy(update={"outline": (*structure.outline, structure.outline[0])})


# ------------------------------------------------------ invariants I1-I4, I6, I7


def _mixed() -> list[object]:
    return [
        ("renames", Rename(source="§1", target="§2", proof=_p(1))),
        ("renames", Rename(source="§2", target="§1", proof=_p(2), placement="under kapittel 2")),
        ("renames", Rename(source="§101", target="§411", proof=_p(3),
                           placement="som innleder tredje del")),
        ("repeals", Repeal(label="§4", proof=_p(4))),
        ("insertions", Insertion(label="§4", proof=_p(5), placement=KAP16)),
        ("insertions", Insertion(label="§101", proof=_p(6))),
        ("renames", Rename(source="§3", target="§2", proof=_p(7), placement="under kapittel 1")),
    ]  # fmt: skip


def _shuffled(seed: int) -> Step:
    ops = _mixed()
    random.Random(seed).shuffle(ops)  # noqa: S311 - test permutation
    grouped: dict[str, list[object]] = {}
    for field, op in ops:  # type: ignore[misc]
        grouped.setdefault(field, []).append(op)
    return _step(**{k: tuple(v) for k, v in grouped.items()})


def _serialized(result: StepResult) -> str:
    assert result.structure is not None
    refusals = sorted(r.model_dump_json() for r in result.refusals)
    return "\n".join([result.ledger.canonical_json(), result.structure.canonical_json(), *refusals])


@pytest.mark.parametrize("seed", range(12))
def test_document_order_never_changes_labels_pairs_or_history(seed: int) -> None:
    """I6/I7: every permutation, and every rerun, serializes byte-identically."""
    baseline = _serialized(_run(_shuffled(0))[0])
    assert _serialized(_run(_shuffled(seed))[0]) == baseline
    assert _serialized(_run(_shuffled(seed))[0]) == baseline


def test_the_mixed_step_holds_i1_to_i3_and_conserves_every_proof() -> None:
    result, _, _ = _run(_shuffled(0))
    assert _why(result) == {
        "1": ("relational", "label collision"), "7": ("relational", "label collision"),
        "2": ("relational", "refused with its component"),  # § 2's placement goes with it
    }  # fmt: skip
    assert result.accepted == tuple(_p(n).op_id for n in (3, 4, 5, 6))
    assert _pair(result, _old("§101")) == Pair(chapter=None, part="del3")
    assert _pair(result, _new("§101")) is None  # the refill states no pair: unresolved
    assert _pair(result, _new("§4")) == Pair(chapter="kap16", part="del2")
    assert _pair(result, _old("§2")) == Pair(chapter="kap1", part="del1")  # refused: kept
    structure = result.structure
    assert structure is not None
    paired(result.ledger, structure)  # one set of identities, live alike
    labels = [i.label for i in result.ledger.identities if i.live]
    assert len(labels) == len(set(labels))  # I1
    ids = [m.provision_id for m in structure.memberships]
    assert len(ids) == len(set(ids)) == len(result.ledger.identities)  # I2: one membership each
    accepted = set(result.accepted)
    for membership in structure.memberships:
        for event in membership.events[1:]:
            assert event.proof.op_id in accepted  # I3: no placement without its sentence
    placed = [e for m in structure.memberships for e in m.events if e.cause == "placed"]
    sent = {
        op.proof.op_id: op.proof for _, op in _mixed() if isinstance(op, Rename) and op.placement
    }  # type: ignore[union-attr]
    assert all(e.proof == sent[e.proof.op_id] for e in placed)  # proofs conserved verbatim


# ------------------------------------------------- research primitives: not wired in


@pytest.mark.structural
def test_only_the_b1_step_and_the_research_adapter_reach_the_structure_map() -> None:
    """No replay, report or surface module imports ``S``: replay output cannot change."""
    root = Path(__file__).resolve().parents[2] / "src" / "lovspor"
    importers = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if p.name != "structure.py" and _imports(p, "lovspor.legaltime.structure")
    )
    assert importers == ["legaltime/step.py"]
    reaching_step = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if p.name != "step.py" and _imports(p, "lovspor.legaltime.step")
    )
    assert reaching_step == ["legaltime/rename_research.py"]


def _imports(path: Path, module: str) -> bool:
    text = path.read_text(encoding="utf-8")
    return f"from {module} import" in text or f"import {module}" in text


# --------------------------------------------------------- step 0 from a parsed member


def _enacting(sections: str) -> Document:
    xml = (
        '<html><body><header><dl><dd class="refid">lov/2005-05-20-28</dd>'
        '<dd class="dateInForce">2005-05-20</dd>'
        '<dd class="dateOfPublication">2005-05-20</dd>'
        '<dd class="title">Lov om straff</dd>'
        f"</dl></header><main>{sections}</main></body></html>"
    )
    return parse_document(xml.encode(), ENACTING)


def _article(label: str) -> str:
    return (
        f'<article class="legalArticle" data-name="{label.replace(" ", "")}">'
        f'<h3><span class="legalArticleValue">{label}</span></h3>'
        '<p class="legalP">Tekst.</p></article>'
    )


def test_stamps_are_read_exactly_as_enclosing_reads_the_enacting_member() -> None:
    document = _enacting(
        '<section data-name="del1"><section data-name="kap1">'
        f"{_article('§ 1')}</section></section>"
        f'<section data-name="del3">{_article("§ 101")}</section>'
        f"{_article('§ 200')}"
    )
    assert stamps_of(document) == {
        "§1": Pair(chapter="kap1", part="del1"),
        "§101": Pair(chapter=None, part="del3"),
        "§200": Pair(),
    }


def test_a_member_printing_one_label_twice_has_no_stamps() -> None:
    document = _enacting(_article("§ 1") + _article("§ 1"))
    with pytest.raises(StructureError, match="has no one key"):
        stamps_of(document)


def test_a_part_only_insertion_takes_the_part_it_states() -> None:
    insertion = Insertion(label="§20", proof=_p(1), placement=Pair(part="del4"))
    result, _, _ = _run(_step(insertions=(insertion,)))
    assert _pair(result, _new("§20")) == Pair(part="del4")
    assert result.structure is not None and "del4" in result.structure.parts()


def test_an_inconsistent_structure_is_the_replays_hard_failure() -> None:
    _, structure = _state()
    twice = (*structure.memberships, structure.memberships[0])
    with pytest.raises(StructureError, match="unique"):
        _structure(ACT, twice, structure.outline)


def _renumbered(seed: int) -> Step:
    """The mixed step in another document order, op ids re-derived from the new positions."""
    ops = _mixed()
    random.Random(seed).shuffle(ops)  # noqa: S311 - test permutation
    grouped: dict[str, list[object]] = {}
    for position, (field, op) in enumerate(ops):  # type: ignore[misc]
        proof = op.proof.model_copy(update={"op_id": f"lov/2009-06-19-74#1:I:{100 + position}"})  # type: ignore[attr-defined]
        grouped.setdefault(field, []).append(op.model_copy(update={"proof": proof}))  # type: ignore[attr-defined]
    return _step(**{k: tuple(v) for k, v in grouped.items()})


def _stripped(value: object) -> object:
    if isinstance(value, dict):
        return {k: _stripped(v) for k, v in value.items() if k != "op_id"}
    if isinstance(value, list):
        return [_stripped(v) for v in value]
    return value


def _projection(result: StepResult) -> str:
    """I6's projection: op ids dropped, refusals sorted by sentence digest."""
    assert result.structure is not None
    refusals = sorted(
        (_stripped(r.model_dump(mode="json")) for r in result.refusals),
        key=lambda r: json.dumps(r, sort_keys=True),
    )
    body = {
        "ledger": _stripped(json.loads(result.ledger.canonical_json())),
        "structure": _stripped(json.loads(result.structure.canonical_json())),
        "refusals": refusals,
    }
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


@pytest.mark.parametrize("seed", range(8))
def test_the_i6_projection_survives_renumbered_op_ids(seed: int) -> None:
    assert _projection(_run(_renumbered(seed))[0]) == _projection(_run(_renumbered(0))[0])


def test_an_insertion_into_a_known_chapter_takes_the_outlines_part() -> None:
    """The sentence names the chapter, not the part: the outline encloses it (R3)."""
    insertion = Insertion(label="§20", proof=_p(1), placement=Pair(chapter="kap3"))
    result, _, _ = _run(_step(insertions=(insertion,)))
    assert result.refusals == ()
    assert _pair(result, _new("§20")) == Pair(chapter="kap3", part="del2")


def test_a_chapter_step_0_reads_outside_every_part_is_a_stated_empty_part() -> None:
    """``_enclosing`` stamps a chapter standing outside every part with an empty part.

    That is the enacting structure's own statement, so a chapter-only clause or an
    insertion naming that chapter gets (chapter, empty), even in an act with parts.
    """
    stamps = {**STAMPS, "§6": Pair(chapter="kap0")}
    rename = Rename(source="§3", target="§14", proof=_p(1), placement="under kapittel 0")
    insertion = Insertion(label="§20", proof=_p(2), placement=Pair(chapter="kap0"))
    result, _, _ = _run(_step(renames=(rename,), insertions=(insertion,)), stamps)
    assert result.refusals == ()
    assert _pair(result, _old("§3")) == _pair(result, _new("§20")) == Pair(chapter="kap0")
