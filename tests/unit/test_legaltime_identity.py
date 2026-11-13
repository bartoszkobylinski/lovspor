"""ADR-0023 Phase A: replay-internal provision identity and label history (#861).

The fixtures model straffeloven 2005's label chain from the ADR-0023 evidence file
(pinned members named beside each); digests are synthetic hex, because these tests
exercise the label layer, not the archive. Nothing here touches replay output.
"""

import json
import os
import random
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.legaltime.errors import LegalTimeError
from lovspor.legaltime.identity import (
    EndProof,
    IdentityError,
    Insertion,
    LabelProof,
    Ledger,
    Rename,
    Repeal,
    Step,
    apply_step,
    enacted_id,
    inserted_id,
    mint_enacted,
)

ACT = "lov/2005-05-20-28"
ENACTING = "lti/2005/nl-20050520-028.xml"
L2008 = "lti/2008/nl-20080307-004.xml"
L2009 = "lti/2009/nl-20090619-074.xml"
L2015 = "lti/2015/nl-20150619-065.xml"
PUBLISHED = date(2005, 5, 20)


def _hex(seed: str) -> str:
    return (seed.encode().hex() * 64)[:64]


def _start0(label: str) -> LabelProof:
    return LabelProof(
        instrument=ACT, op_id=f"{ACT}#enacted:{label}", member=ENACTING,
        member_sha256=_hex("m2005"), sentence_sha256=_hex(label), effective=None,
        known_from=PUBLISHED, label_minted_at=PUBLISHED,
    )  # fmt: skip


def _proof(member: str, op: str, effective: date, refid: str) -> LabelProof:
    return LabelProof(
        instrument=refid, op_id=f"{refid}#{op}", member=member, member_sha256=_hex(member),
        sentence_sha256=_hex(op), effective=effective, known_from=effective,
    )  # fmt: skip


def _end(proof: LabelProof, ended_by: str) -> EndProof:
    return EndProof(**proof.model_dump(), ended_by=ended_by)


def _enacted(*labels: str) -> Ledger:
    return mint_enacted(ACT, ENACTING, PUBLISHED, [(label, _start0(label)) for label in labels])


P2008 = _proof(L2008, "0:I:2.0", date(2008, 3, 7), "lov/2008-03-07-4")
P2009 = _proof(L2009, "1:I:9.0", date(2009, 6, 19), "lov/2009-06-19-74")


def _step_2008(ledger: Ledger) -> Ledger:
    """§ 101 i lov 20. mai 2005 nr. 28 blir § 401; Annen del inserts a new § 101."""
    old = enacted_id(ACT, ENACTING, "§101")
    step = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        renames=(Rename(source=old, target="§401", proof=P2008),),
        insertions=(Insertion(label="§101", proof=P2008),),
    )  # fmt: skip
    result = apply_step(ledger, step)
    assert result.refusals == ()
    return result.ledger


def _step_2009(ledger: Ledger) -> Ledger:
    """Nåværende § 401 blir § 411."""
    chain = enacted_id(ACT, ENACTING, "§101")
    step = Step(
        act=ACT, member=L2009, effective=date(2009, 6, 19),
        renames=(Rename(source=chain, target="§411", proof=P2009),),
    )  # fmt: skip
    result = apply_step(ledger, step)
    assert result.refusals == ()
    return result.ledger


# ------------------------------------------------------------------ 1. identity (R1)


def test_the_enacted_and_inserted_ids_are_exactly_the_adrs_examples() -> None:
    assert enacted_id(ACT, ENACTING, "§101") == (
        "lov/2005-05-20-28|enacted:lti/2005/nl-20050520-028.xml|§101"
    )
    assert inserted_id(ACT, L2008, date(2008, 3, 7), "§101") == (
        "lov/2005-05-20-28|lti/2008/nl-20080307-004.xml|2008-03-07|§101"
    )


@pytest.mark.parametrize(
    ("act", "member", "label"),
    [
        ("lov/2005-05-20-28|x", ENACTING, "§101"),  # a separator inside a component
        ("forskrift/2008-03-07-225", ENACTING, "§101"),  # not an act refid
        (ACT, "nl-20050520-028.xml", "§101"),  # not an archive member path
        (ACT, ENACTING, "101"),  # not a section label
        (ACT, ENACTING, "§ 101"),  # a printed label, not the engine's key
        (ACT, ENACTING, ""),
    ],
)
def test_a_malformed_id_component_is_refused(act: str, member: str, label: str) -> None:
    with pytest.raises(IdentityError):
        enacted_id(act, member, label)


def test_identity_errors_are_legal_time_errors() -> None:
    assert issubclass(IdentityError, LegalTimeError)


# ------------------------------------------------------------- 4. step 0 (R1, R2, I3)


def test_step_0_mints_without_an_effective_date() -> None:
    ledger = _enacted("§101", "§102")
    first = ledger.get(enacted_id(ACT, ENACTING, "§101"))
    assert first is not None
    start = first.intervals[0].start
    assert (start.effective, start.label_minted_at) == (None, PUBLISHED)
    assert first.text_member == ENACTING
    assert first.label == "§101"
    assert first.former_keys == ()


def test_step_0_refuses_a_proof_carrying_an_effective_date() -> None:
    dated = _start0("§101").model_copy(update={"effective": PUBLISHED})
    with pytest.raises(IdentityError):
        mint_enacted(ACT, ENACTING, PUBLISHED, [("§101", dated)])


def test_step_0_refuses_a_placement_other_than_the_publication_date() -> None:
    elsewhere = _start0("§101").model_copy(update={"label_minted_at": date(2015, 10, 1)})
    with pytest.raises(IdentityError):
        mint_enacted(ACT, ENACTING, PUBLISHED, [("§101", elsewhere)])


def test_a_later_proof_must_be_dated_and_carries_no_placement() -> None:
    with pytest.raises(ValidationError):
        LabelProof.model_validate({**P2008.model_dump(), "effective": None})
    with pytest.raises(ValidationError):
        LabelProof.model_validate({**P2008.model_dump(), "label_minted_at": date(2008, 3, 7)})


def test_step_0_refuses_one_label_enacted_twice() -> None:
    with pytest.raises(IdentityError):
        _enacted("§101", "§101")


# ------------------------------------------- 5-6. reuse and lineage (R1, R2, R4(b))


def test_the_reused_101_is_a_new_identity_and_the_chain_keeps_its_lineage() -> None:
    ledger = _step_2009(_step_2008(_enacted("§101", "§102")))
    chain = ledger.get(enacted_id(ACT, ENACTING, "§101"))
    reused = ledger.holder("§101")
    assert chain is not None and reused is not None
    assert reused.provision_id == inserted_id(ACT, L2008, date(2008, 3, 7), "§101")
    assert reused.provision_id != chain.provision_id
    assert reused.text_member == L2008
    assert reused.former_keys == ()
    assert chain.label == "§411"
    assert [(k.label, k.ended_by, k.instrument, k.effective) for k in chain.former_keys] == [
        ("§101", "rename", "lov/2008-03-07-4", date(2008, 3, 7)),
        ("§401", "rename", "lov/2009-06-19-74", date(2009, 6, 19)),
    ]
    assert ledger.holder("§411") == chain
    assert ledger.holder("§401") is None  # vacated, never redirected (R9, I5)


def test_a_cycle_within_one_step_is_one_simultaneous_assignment() -> None:
    """atomenergiloven: 21→22→23→25→21, every source read before the step."""
    ledger = mint_enacted(
        ACT, ENACTING, PUBLISHED, [(f"§{n}", _start0(f"§{n}")) for n in (21, 22, 23, 25)]
    )
    moves = {21: 22, 22: 23, 23: 25, 25: 21}
    step = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        renames=tuple(
            Rename(source=enacted_id(ACT, ENACTING, f"§{s}"), target=f"§{t}", proof=P2008)
            for s, t in moves.items()
        ),
    )  # fmt: skip
    result = apply_step(ledger, step)
    assert result.refusals == ()
    held = {label: pid.rsplit("|", 1)[1] for label, pid in result.ledger.live_map().items()}
    assert held == {"§22": "§21", "§23": "§22", "§25": "§23", "§21": "§25"}


# --------------------------------------------------- 7. repeal and reuse (R2, R4(d))


def test_a_repeal_closes_the_final_label_and_a_reuse_mints_a_fresh_identity() -> None:
    p2015 = _proof(L2015, "0::5.0", date(2015, 6, 19), "lov/2015-06-19-65")
    old = enacted_id(ACT, ENACTING, "§102")
    step = Step(
        act=ACT, member=L2015, effective=date(2015, 6, 19),
        repeals=(Repeal(label="§102", proof=p2015),),
        insertions=(Insertion(label="§102", proof=p2015),),
    )  # fmt: skip
    ledger = apply_step(_enacted("§101", "§102"), step).ledger
    ended = ledger.get(old)
    assert ended is not None and ended.label is None and not ended.live
    assert [(k.label, k.ended_by) for k in ended.former_keys] == [("§102", "repeal")]
    fresh = ledger.holder("§102")
    assert fresh is not None
    assert fresh.provision_id == inserted_id(ACT, L2015, date(2015, 6, 19), "§102")
    assert fresh.former_keys == ()


# ---------------------------------------------- 3, 8, 9. refusals fail closed (R5, I4)


def _refused(ledger: Ledger, step: Step) -> tuple[str, ...]:
    result = apply_step(ledger, step)
    assert result.ledger == ledger  # byte-identical: nothing applied
    assert result.ledger.canonical_json() == ledger.canonical_json()
    return tuple(sorted({r.reason for r in result.refusals}))


def test_an_insertion_onto_a_held_label_is_a_collision_and_overwrites_nothing() -> None:
    step = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        insertions=(Insertion(label="§101", proof=P2008),),
    )  # fmt: skip
    assert _refused(_enacted("§101"), step) == ("label collision",)


def test_two_targets_onto_one_label_are_a_collision() -> None:
    ledger = _enacted("§1", "§2")
    step = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        renames=(
            Rename(source=enacted_id(ACT, ENACTING, "§1"), target="§9", proof=P2008),
            Rename(source=enacted_id(ACT, ENACTING, "§2"), target="§9", proof=P2008),
        ),
    )  # fmt: skip
    assert _refused(ledger, step) == ("label collision",)


def test_a_missing_rename_source_and_a_missing_repeal_target_are_refused() -> None:
    missing = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        renames=(Rename(source=enacted_id(ACT, ENACTING, "§7"), target="§8", proof=P2008),),
        repeals=(Repeal(label="§6", proof=P2008),),
    )  # fmt: skip
    assert _refused(_enacted("§1"), missing) == ("rename source missing", "repeal target missing")


def test_two_renames_of_one_source_are_conflicting() -> None:
    source = enacted_id(ACT, ENACTING, "§1")
    step = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        renames=(
            Rename(source=source, target="§8", proof=P2008),
            Rename(source=source, target="§9", proof=P2008),
        ),
    )  # fmt: skip
    assert _refused(_enacted("§1"), step) == ("conflicting renames",)


def test_an_undated_step_mints_nothing() -> None:
    step = Step(
        act=ACT, member=L2008, effective=None,
        insertions=(Insertion(label="§9", proof=P2008),),
    )  # fmt: skip
    assert _refused(_enacted("§1"), step) == ("undated step",)


def test_a_rejected_step_leaves_its_insertion_unminted() -> None:
    ledger = _enacted("§101")
    step = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        insertions=(Insertion(label="§101", proof=P2008),),
    )  # fmt: skip
    result = apply_step(ledger, step)
    assert result.ledger.get(inserted_id(ACT, L2008, date(2008, 3, 7), "§101")) is None
    (refusal,) = result.refusals
    assert (refusal.op_id, refusal.member, refusal.asserted_label) == (P2008.op_id, L2008, "§101")
    assert refusal.sentence_sha256 == P2008.sentence_sha256


def test_minting_an_existing_id_again_is_a_hard_failure() -> None:
    """I2: the same step applied twice would allocate one id twice."""
    ledger = _step_2008(_enacted("§101", "§102"))
    repeal = Step(
        act=ACT, member=L2015, effective=date(2015, 6, 19),
        repeals=(Repeal(label="§101", proof=_proof(L2015, "0::1.0", date(2015, 6, 19), "x")),),
    )  # fmt: skip
    vacated = apply_step(ledger, repeal).ledger
    again = Step(
        act=ACT, member=L2008, effective=date(2008, 3, 7),
        insertions=(Insertion(label="§101", proof=P2008),),
    )  # fmt: skip
    with pytest.raises(IdentityError):
        apply_step(vacated, again)


@pytest.mark.parametrize(
    "field",
    ["member_sha256", "sentence_sha256"],
)
def test_a_malformed_digest_is_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        LabelProof.model_validate({**P2008.model_dump(), field: "not-a-digest"})


def test_a_proof_from_another_member_or_date_is_refused_by_the_step() -> None:
    with pytest.raises(ValidationError):
        Step(
            act=ACT, member=L2009, effective=date(2008, 3, 7),
            insertions=(Insertion(label="§9", proof=P2008),),
        )  # fmt: skip
    with pytest.raises(ValidationError):
        Step(
            act=ACT, member=L2008, effective=date(2009, 6, 19),
            insertions=(Insertion(label="§9", proof=P2008),),
        )  # fmt: skip


def test_a_ledger_holding_one_label_twice_cannot_be_built() -> None:
    """I1 is a property of every ledger, not only of what apply_step produces."""
    a = _enacted("§1").identities[0]
    b = a.model_copy(update={"provision_id": enacted_id(ACT, ENACTING, "§2")})
    with pytest.raises(ValidationError):
        Ledger(act=ACT, identities=(a, b))


def test_a_vacated_label_is_never_forwarded_to_its_former_holder() -> None:
    ledger = _step_2009(_step_2008(_enacted("§101")))
    assert ledger.holder("§401") is None
    assert "§401" not in ledger.live_map()


# ------------------------------------- 2, 10. determinism, immutability, serialization


def test_permuting_a_steps_operations_gives_the_same_ledger() -> None:
    base = mint_enacted(
        ACT, ENACTING, PUBLISHED, [(f"§{n}", _start0(f"§{n}")) for n in (21, 22, 23, 25)]
    )
    renames = [
        Rename(source=enacted_id(ACT, ENACTING, f"§{s}"), target=f"§{t}", proof=P2008)
        for s, t in {21: 22, 22: 23, 23: 25, 25: 21}.items()
    ]
    outputs = set()
    for seed in range(8):
        shuffled = renames[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - input-order shuffle, not security
        step = Step(act=ACT, member=L2008, effective=date(2008, 3, 7), renames=tuple(shuffled))
        outputs.add(apply_step(base, step).ledger.canonical_json())
    assert len(outputs) == 1


def test_enacted_labels_in_any_order_give_the_same_ledger() -> None:
    labels = [f"§{n}" for n in range(1, 30)]
    shuffled = labels[:]
    random.Random(7).shuffle(shuffled)  # noqa: S311 - input-order shuffle, not security
    assert _enacted(*labels).canonical_json() == _enacted(*shuffled).canonical_json()


def test_the_canonical_serialization_is_byte_stable_across_processes() -> None:
    code = (
        "from tests.unit.test_legaltime_identity import _enacted, _step_2008, _step_2009\n"
        "print(_step_2009(_step_2008(_enacted('§101', '§102'))).canonical_json(), end='')\n"
    )
    root = Path(__file__).resolve().parents[2]
    runs = {
        subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
        ).stdout
        for seed in (0, 1, 12345)
    }  # fmt: skip
    local = _step_2009(_step_2008(_enacted("§101", "§102"))).canonical_json()
    assert runs == {local}
    parsed = json.loads(local)
    assert list(parsed) == sorted(parsed)


def test_snapshots_are_immutable() -> None:
    ledger = _enacted("§1")
    with pytest.raises(ValidationError):
        ledger.act = "lov/2000-01-01-1"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ledger.identities[0].intervals[0].label = "§2"  # type: ignore[misc]
    assert isinstance(ledger.identities, tuple)
    assert isinstance(ledger.identities[0].intervals, tuple)
