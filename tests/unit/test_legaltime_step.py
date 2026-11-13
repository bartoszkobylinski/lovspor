"""ADR-0023 Phase B1: R4's two-phase acceptance and R5's component atomicity (#861).

Fixtures are straffeloven 2005 (lov/2005-05-20-28) and atomenergiloven's cycle; every
operation has its own op id and sentence digest, so a refusal's provenance is checkable.
"""

import random
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lovspor.legaltime.identity import (
    IdentityError,
    LabelProof,
    Ledger,
    enacted_id,
    inserted_id,
    mint_enacted,
)
from lovspor.legaltime.step import (
    Finer,
    Insertion,
    Rename,
    Repeal,
    Replace,
    Replacement,
    Step,
    StepResult,
    apply_step,
)

ACT = "lov/2005-05-20-28"
OTHER = "lov/2005-06-17-62"
ENACTING = "lti/2005/nl-20050520-028.xml"
MEMBER = "lti/2008/nl-20080307-004.xml"
PUBLISHED = date(2005, 5, 20)
EFFECTIVE = date(2008, 3, 7)


def _hex(seed: str) -> str:
    return (seed.encode().hex() * 64)[:64]


def _start0(act: str, label: str) -> LabelProof:
    return LabelProof(
        instrument=act, op_id=f"{act}#enacted:{label}", member=ENACTING,
        member_sha256=_hex("m2005"), sentence_sha256=_hex(label), effective=None,
        known_from=PUBLISHED, label_minted_at=PUBLISHED,
    )  # fmt: skip


def _p(n: int) -> LabelProof:
    """The proof of the n-th sentence of the 2008 instrument."""
    return LabelProof(
        instrument="lov/2008-03-07-4", op_id=f"lov/2008-03-07-4#0:I:{n}", member=MEMBER,
        member_sha256=_hex(MEMBER), sentence_sha256=_hex(f"s{n:03d}"), effective=EFFECTIVE,
        known_from=EFFECTIVE,
    )  # fmt: skip


def _ledger(*labels: str, act: str = ACT) -> Ledger:
    return mint_enacted(act, ENACTING, PUBLISHED, [(x, _start0(act, x)) for x in labels])


def _step(**operations: object) -> Step:
    return Step.model_validate({"act": ACT, "member": MEMBER, "effective": EFFECTIVE, **operations})


def _old(label: str) -> str:
    return enacted_id(ACT, ENACTING, label)


def _new(label: str) -> str:
    return inserted_id(ACT, MEMBER, EFFECTIVE, label)


def _why(result: StepResult) -> dict[str, tuple[str, str]]:
    """op id → (phase, reason) for every refusal."""
    return {r.op_id: (r.phase, r.reason) for r in result.refusals}


def _rename(n: int, source: str, target: str) -> Rename:
    return Rename(source=source, target=target, proof=_p(n))


# ------------------------------------------- 1. an unrelated component is still applied


def test_a_valid_component_survives_an_unrelated_refusal() -> None:
    ledger = _ledger("§1", "§2", "§5")
    step = _step(renames=(_rename(1, "§1", "§2"), _rename(2, "§5", "§6")))
    result = apply_step(ledger, step)
    assert _why(result) == {_p(1).op_id: ("relational", "label collision")}
    assert result.accepted == (_p(2).op_id,)
    assert result.ledger.live_map() == {"§1": _old("§1"), "§2": _old("§2"), "§6": _old("§5")}
    assert result.ledger.get(_old("§1")) == ledger.get(_old("§1"))  # byte-identical


# ---------------------------- 2. an intrinsic refusal creates no edge and claims no label


def test_an_intrinsically_refused_rename_vacates_nothing_so_its_refill_collides() -> None:
    ledger = _ledger("§101")
    partial = Rename(source="§101", target="§401", proof=_p(1), partial=True)
    step = _step(renames=(partial,), insertions=(Insertion(label="§101", proof=_p(2)),))
    result = apply_step(ledger, step)
    assert _why(result) == {
        _p(1).op_id: ("intrinsic", "partial rename: extent not in the source"),
        _p(2).op_id: ("relational", "label collision"),
    }
    assert result.ledger == ledger


def test_an_intrinsically_refused_rename_claims_no_target() -> None:
    """A partial rename into § 9 does not collide with a valid rename into § 9."""
    ledger = _ledger("§3", "§4")
    partial = Rename(source="§3", target="§9", proof=_p(1), partial=True)
    result = apply_step(ledger, _step(renames=(partial, _rename(2, "§4", "§9"))))
    assert _why(result) == {_p(1).op_id: ("intrinsic", "partial rename: extent not in the source")}
    assert result.ledger.live_map() == {"§3": _old("§3"), "§9": _old("§4")}


@pytest.mark.parametrize(
    ("rename", "reason"),
    [
        (Rename(source="§7", target="§8", proof=_p(1)), "rename source missing"),
        (Rename(source="§1", target="§1", proof=_p(1)), "rename to the same label"),
        (
            Rename(source="§1", target="§2", proof=_p(1),
                   replacement=Replacement(headed="§3", payload_sha256=_hex("x"))),
            "replacement not headed by the target label",
        ),
        (
            Rename(source="§1", target="§2", proof=_p(1),
                   replacement=Replacement(headed=None, payload_sha256=_hex("x"))),
            "replacement not headed by the target label",
        ),
    ],
)  # fmt: skip
def test_intrinsic_rename_refusals(rename: Rename, reason: str) -> None:
    ledger = _ledger("§1")
    result = apply_step(ledger, _step(renames=(rename,)))
    assert _why(result) == {_p(1).op_id: ("intrinsic", reason)}
    assert result.ledger == ledger


def test_a_rename_with_a_replacement_headed_by_its_target_is_accepted() -> None:
    replaced = Rename(
        source="§1", target="§2", proof=_p(1),
        replacement=Replacement(headed="§2", payload_sha256=_hex("x")),
    )  # fmt: skip
    result = apply_step(_ledger("§1"), _step(renames=(replaced,)))
    assert result.refusals == ()
    assert result.ledger.live_map() == {"§2": _old("§1")}


@pytest.mark.parametrize("kind", ["repeals", "replaces", "finer"])
def test_an_operation_on_a_label_nobody_holds_is_target_provision_missing(kind: str) -> None:
    op = {
        "repeals": Repeal(label="§7", proof=_p(1)),
        "replaces": Replace(label="§7", proof=_p(1), payload_sha256=_hex("x")),
        "finer": Finer(label="§7", kind="ledd", proof=_p(1)),
    }[kind]
    result = apply_step(_ledger("§1"), _step(**{kind: (op,)}))
    assert _why(result) == {_p(1).op_id: ("intrinsic", "target provision missing")}


# --------------------------------------------- 3. one source twice, one target twice


def test_two_renames_of_one_source_are_conflicting_renames() -> None:
    result = apply_step(
        _ledger("§1"), _step(renames=(_rename(1, "§1", "§8"), _rename(2, "§1", "§9")))
    )
    assert _why(result) == {
        _p(1).op_id: ("relational", "conflicting renames"),
        _p(2).op_id: ("relational", "conflicting renames"),
    }


def test_three_writes_to_one_target_all_collide() -> None:
    step = _step(
        renames=(_rename(1, "§1", "§9"),),
        insertions=(Insertion(label="§9", proof=_p(2)), Insertion(label="§9", proof=_p(3))),
    )
    result = apply_step(_ledger("§1"), step)
    assert {r.reason for r in result.refusals} == {"label collision"}
    assert len(result.refusals) == 3
    assert result.ledger == _ledger("§1")


# ------------------------------ 4. rename with repeal, replacement or a finer operation


@pytest.mark.parametrize(
    "rival",
    [
        {"repeals": (Repeal(label="§1", proof=_p(2)),)},
        {"replaces": (Replace(label="§1", proof=_p(2), payload_sha256=_hex("y")),)},
        {"finer": (Finer(label="§1", kind="ledd", proof=_p(2)),)},
        {"finer": (Finer(label="§1", kind="heading", proof=_p(2)),)},
    ],
)
def test_a_rename_and_any_other_operation_on_its_identity_conflict(
    rival: dict[str, object],
) -> None:
    ledger = _ledger("§1")
    result = apply_step(ledger, _step(renames=(_rename(1, "§1", "§9"),), **rival))
    reason = ("relational", "conflicting operations within instrument")
    assert _why(result) == {_p(1).op_id: reason, _p(2).op_id: reason}
    assert result.ledger == ledger


def test_a_replacement_and_a_finer_operation_on_one_section_conflict() -> None:
    step = _step(
        replaces=(Replace(label="§1", proof=_p(1), payload_sha256=_hex("y")),),
        finer=(Finer(label="§1", kind="punktum", proof=_p(2)),),
    )
    assert {r.reason for r in apply_step(_ledger("§1"), step).refusals} == {
        "conflicting operations within instrument"
    }


def test_finer_operations_alone_on_one_section_keep_d7_order_and_are_accepted() -> None:
    step = _step(
        finer=(
            Finer(label="§1", kind="ledd", proof=_p(1)),
            Finer(label="§1", kind="bokstav", proof=_p(2)),
        )
    )
    result = apply_step(_ledger("§1"), step)
    assert result.refusals == ()
    assert result.accepted == (_p(1).op_id, _p(2).op_id)
    assert result.ledger == _ledger("§1")  # text effects are the replay's, not the label layer's


def test_replacing_a_section_and_inserting_its_label_is_a_collision() -> None:
    step = _step(
        replaces=(Replace(label="§1", proof=_p(1), payload_sha256=_hex("y")),),
        insertions=(Insertion(label="§1", proof=_p(2)),),
    )
    result = apply_step(_ledger("§1"), step)
    assert _why(result) == {_p(2).op_id: ("relational", "label collision")}
    assert result.accepted == (_p(1).op_id,)


# ----------------------------- 5, 6. rename-out and refill; repeal and refill (straffeloven)


def test_rename_out_and_insertion_under_the_vacated_label() -> None:
    """2008: § 101 blir § 401, and a new kap. 16 § 101 is inserted."""
    step = _step(
        renames=(_rename(1, "§101", "§401"),), insertions=(Insertion(label="§101", proof=_p(2)),)
    )
    result = apply_step(_ledger("§101"), step)
    assert result.refusals == ()
    assert result.ledger.live_map() == {"§101": _new("§101"), "§401": _old("§101")}
    moved = result.ledger.get(_old("§101"))
    assert moved is not None
    assert moved.intervals[-1].start == _p(1)  # proof fidelity: the rename's own sentence


@pytest.mark.parametrize("refill", ["insertion", "rename"])
def test_repeal_and_refill_of_one_label(refill: str) -> None:
    """atomenergiloven: § 19 oppheves, and a different identity takes § 19."""
    ledger = _ledger("§19", "§20")
    other = (
        {"insertions": (Insertion(label="§19", proof=_p(2)),)}
        if refill == "insertion"
        else {"renames": (_rename(2, "§20", "§19"),)}
    )
    result = apply_step(ledger, _step(repeals=(Repeal(label="§19", proof=_p(1)),), **other))
    assert result.refusals == ()
    holder = _new("§19") if refill == "insertion" else _old("§20")
    assert result.ledger.live_map()["§19"] == holder
    repealed = result.ledger.get(_old("§19"))
    assert repealed is not None
    assert not repealed.live
    assert repealed.former_keys[-1].ended_by == "repeal"


# ---------------------------------------------------------------- 7. chains and cycles


def test_a_chain_moves_every_identity_by_its_pre_step_label() -> None:
    step = _step(renames=(_rename(1, "§2", "§3"), _rename(2, "§1", "§2")))
    result = apply_step(_ledger("§1", "§2"), step)
    assert result.ledger.live_map() == {"§2": _old("§1"), "§3": _old("§2")}


def test_the_atomenergiloven_cycle_is_one_simultaneous_assignment() -> None:
    moves = {21: 22, 22: 23, 23: 25, 25: 21}
    renames = tuple(_rename(n, f"§{s}", f"§{t}") for n, (s, t) in enumerate(moves.items()))
    result = apply_step(_ledger("§21", "§22", "§23", "§25"), _step(renames=renames))
    assert result.refusals == ()
    assert {k: v.rsplit("|", 1)[1] for k, v in result.ledger.live_map().items()} == {
        "§22": "§21",
        "§23": "§22",
        "§25": "§23",
        "§21": "§25",
    }


def test_a_collision_anywhere_on_a_cycle_refuses_the_whole_cycle() -> None:
    moves = {21: 22, 22: 23, 23: 25, 25: 21}
    renames = tuple(_rename(n, f"§{s}", f"§{t}") for n, (s, t) in enumerate(moves.items()))
    clash = Insertion(label="§23", proof=_p(9))
    ledger = _ledger("§21", "§22", "§23", "§25")
    result = apply_step(ledger, _step(renames=renames, insertions=(clash,)))
    assert len(result.refusals) == 5
    assert {r.reason for r in result.refusals} == {"label collision", "refused with its component"}
    assert result.ledger == ledger


# --------------------------------------- 8. ambiguous old and new labels in one instrument


def test_a_finer_operation_on_a_renamed_and_refilled_label_refuses_the_component() -> None:
    """ADR R4: "§ 401 annet ledd skal lyde" beside § 401 renamed away and refilled."""
    ledger = _ledger("§401")
    step = _step(
        renames=(_rename(1, "§401", "§411"),),
        insertions=(Insertion(label="§401", proof=_p(2)),),
        finer=(Finer(label="§401", kind="ledd", proof=_p(3)),),
    )
    result = apply_step(ledger, step)
    assert _why(result) == {
        _p(1).op_id: ("relational", "conflicting operations within instrument"),
        _p(2).op_id: ("relational", "refused with its component"),
        _p(3).op_id: ("relational", "conflicting operations within instrument"),
    }
    assert result.ledger == ledger


def test_a_text_operation_on_a_rename_target_is_ambiguous() -> None:
    ledger = _ledger("§1", "§2")
    step = _step(
        renames=(_rename(1, "§1", "§2"),),
        replaces=(Replace(label="§2", proof=_p(2), payload_sha256=_hex("y")),),
    )
    result = apply_step(ledger, step)
    assert _why(result) == {
        _p(1).op_id: ("relational", "label collision"),
        _p(2).op_id: ("relational", "label ambiguous within instrument"),
    }


# ----------------------------------------------- 9. several rounds, never reconsidered


def test_a_refused_repeal_turns_its_refill_into_a_collision_in_the_next_round() -> None:
    ledger = _ledger("§4")
    step = _step(
        repeals=(Repeal(label="§4", proof=_p(1)),),
        finer=(Finer(label="§4", kind="ledd", proof=_p(2)),),
        insertions=(Insertion(label="§4", proof=_p(3)),),
    )
    result = apply_step(ledger, step)
    assert _why(result) == {
        _p(1).op_id: ("relational", "conflicting operations within instrument"),
        _p(2).op_id: ("relational", "conflicting operations within instrument"),
        _p(3).op_id: ("relational", "label collision"),
    }
    assert result.ledger == ledger


def test_a_conflict_whose_peer_is_also_refused_is_not_reconsidered() -> None:
    """Both renames of § 1 are refused; neither returns once the other is gone."""
    step = _step(renames=(_rename(1, "§1", "§8"), _rename(2, "§1", "§9")))
    result = apply_step(_ledger("§1"), step)
    assert result.accepted == ()
    assert result.ledger == _ledger("§1")


# ----------------------------- 10. permutations, no partial writes, proof fidelity


def _mixed() -> tuple[Ledger, dict[str, list[object]]]:
    ledger = _ledger("§1", "§2", "§4", "§5", "§101")
    ops: dict[str, list[object]] = {
        "renames": [_rename(1, "§101", "§401"), _rename(2, "§1", "§2"), _rename(3, "§5", "§6")],
        "insertions": [Insertion(label="§101", proof=_p(4)), Insertion(label="§7", proof=_p(5))],
        "repeals": [Repeal(label="§4", proof=_p(6))],
        "finer": [Finer(label="§5", kind="ledd", proof=_p(7))],
    }
    return ledger, ops


def test_permuting_operations_changes_neither_ledger_nor_refusals() -> None:
    ledger, ops = _mixed()
    seen = set()
    for seed in range(12):
        shuffled = {k: random.Random(seed).sample(v, len(v)) for k, v in ops.items()}  # noqa: S311 - test permutations, not security
        result = apply_step(ledger, _step(**{k: tuple(v) for k, v in shuffled.items()}))
        seen.add((result.ledger.canonical_json(), result.refusals, result.accepted))
    assert len(seen) == 1


def test_a_refused_identity_keeps_its_history_byte_identical() -> None:
    ledger, ops = _mixed()
    result = apply_step(ledger, _step(**{k: tuple(v) for k, v in ops.items()}))
    for pid in (_old("§1"), _old("§2"), _old("§5")):  # § 1→§ 2 collides; § 5 has two fates
        assert result.ledger.get(pid) == ledger.get(pid)
    assert result.ledger.live_map()["§401"] == _old("§101")
    assert result.ledger.live_map()["§101"] == _new("§101")
    assert "§4" not in result.ledger.live_map()


def test_a_refusal_carries_its_operations_provenance() -> None:
    result = apply_step(_ledger("§1", "§2"), _step(renames=(_rename(1, "§1", "§2"),)))
    (refusal,) = result.refusals
    assert (refusal.op_id, refusal.kind, refusal.member, refusal.sentence_sha256) == (
        _p(1).op_id, "rename", MEMBER, _p(1).sentence_sha256,
    )  # fmt: skip
    assert (refusal.address, refusal.asserted_label) == ("§1", "§2")


# --------------------------------------------- 11. steps of distinct acts never meet


def test_a_step_of_one_act_cannot_touch_another_acts_ledger() -> None:
    with pytest.raises(
        IdentityError, match="^a step on lov/2005-05-20-28 applied to lov/2005-06-17-62$"
    ):
        apply_step(
            _ledger("§1", act=OTHER), _step(insertions=(Insertion(label="§9", proof=_p(1)),))
        )


def test_one_label_inserted_in_two_acts_gets_two_identities() -> None:
    mine = apply_step(_ledger("§1"), _step(insertions=(Insertion(label="§9", proof=_p(1)),)))
    other_step = _step(insertions=(Insertion(label="§9", proof=_p(1)),)).model_copy(
        update={"act": OTHER}
    )
    theirs = apply_step(_ledger("§1", act=OTHER), other_step)
    assert mine.ledger.live_map()["§9"] != theirs.ledger.live_map()["§9"]
    assert theirs.ledger.live_map()["§9"].startswith(f"{OTHER}|")


# -------------------------------------- 12. an invalid or undated operation mints nothing


def test_an_undated_step_refuses_every_operation_and_mints_nothing() -> None:
    proof = _p(1).model_copy(update={"effective": None, "label_minted_at": EFFECTIVE})
    step = Step(
        act=ACT, member=MEMBER, effective=None, insertions=(Insertion(label="§9", proof=proof),)
    )
    ledger = _ledger("§1")
    result = apply_step(ledger, step)
    assert _why(result) == {_p(1).op_id: ("intrinsic", "undated step")}
    assert result.ledger == ledger
    assert result.accepted == ()


def test_a_refused_insertion_allocates_no_id() -> None:
    result = apply_step(_ledger("§1"), _step(insertions=(Insertion(label="§1", proof=_p(1)),)))
    assert result.ledger.get(_new("§1")) is None


# ------------------------------------------------------------------ property: random steps

_LABELS = ["§1", "§2", "§3", "§4", "§5"]
_label = st.sampled_from(_LABELS)
_op = st.one_of(
    st.tuples(st.just("rename"), _label, _label),
    st.tuples(st.just("repeal"), _label, _label),
    st.tuples(st.just("finer"), _label, _label),
    st.tuples(st.just("insertion"), _label, _label),
)


def _build(drawn: list[tuple[str, str, str]]) -> Step:
    return _build_numbered(list(enumerate(drawn)))


def _build_numbered(numbered: list[tuple[int, tuple[str, str, str]]]) -> Step:
    """One operation per drawn sentence; each keeps the proof of its own number."""
    ops: dict[str, list[object]] = {"renames": [], "repeals": [], "finer": [], "insertions": []}
    for n, (kind, a, b) in numbered:
        proof = _p(n)
        if kind == "rename":
            ops["renames"].append(Rename(source=a, target=b, proof=proof))
        elif kind == "repeal":
            ops["repeals"].append(Repeal(label=a, proof=proof))
        elif kind == "finer":
            ops["finer"].append(Finer(label=a, kind="ledd", proof=proof))
        else:
            ops["insertions"].append(Insertion(label=a, proof=proof))
    return _step(**{k: tuple(v) for k, v in ops.items()})


@settings(max_examples=300, deadline=None)
@given(held=st.sets(_label), drawn=st.lists(_op, max_size=7))
def test_every_operation_is_accepted_or_refused_once_and_the_ledger_stays_valid(
    held: set[str], drawn: list[tuple[str, str, str]]
) -> None:
    ledger = _ledger(*sorted(held))
    result = apply_step(ledger, _build(drawn))  # a Q breaking I1 would raise here
    decided = [*result.accepted, *(r.op_id for r in result.refusals)]
    assert sorted(decided) == sorted(_p(n).op_id for n in range(len(drawn)))
    moved = {i.provision_id for i in result.ledger.identities} - {
        i.provision_id for i in ledger.identities
    }
    inserted_ok = {
        r
        for r in result.accepted
        if r in {_p(n).op_id for n, d in enumerate(drawn) if d[0] == "insertion"}
    }
    assert len(moved) == len(inserted_ok)  # only accepted insertions mint
    # A relational refusal blocks its identity: no accepted operation reaches it. (An
    # intrinsic refusal blocks nothing: "§ 1 blir § 1" beside "§ 1 blir § 2" leaves
    # the second one valid, R5.)
    blocked = {
        r.address
        for r in result.refusals
        if r.phase == "relational" and r.kind in {"rename", "repeal"}
    }
    for label in blocked:
        pid = ledger.live_map()[str(label)]
        assert result.ledger.get(pid) == ledger.get(pid)


@settings(max_examples=200, deadline=None)
@given(
    held=st.sets(_label),
    drawn=st.lists(_op, max_size=7),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_any_document_order_of_a_steps_sentences_gives_one_result(
    held: set[str], drawn: list[tuple[str, str, str]], seed: int
) -> None:
    """I6: permuting the sentences of a random step (each keeps its own proof) changes nothing.

    A ``Step`` holds one tuple per operation kind, so order across kinds is not an input;
    this permutes the whole sentence list, which reorders every kind's tuple at once.
    """
    ledger = _ledger(*sorted(held))
    numbered = list(enumerate(drawn))
    shuffled = random.Random(seed).sample(numbered, len(numbered))  # noqa: S311 - test permutation
    first = apply_step(ledger, _build_numbered(numbered))
    again = apply_step(ledger, _build_numbered(shuffled))
    assert (first.ledger.canonical_json(), first.accepted, first.refusals) == (
        again.ledger.canonical_json(), again.accepted, again.refusals,
    )  # fmt: skip
