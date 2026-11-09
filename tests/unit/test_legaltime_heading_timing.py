"""Heading/body separation must survive the full commencement decision fold."""

from datetime import date
from hashlib import sha256

import pytest

from lovspor.legaltime.commencement import resolve_timings
from lovspor.legaltime.instructions import Address, Operation
from tests.unit.test_legaltime_phase2_commencement import LAW, _decision, _law


@pytest.mark.parametrize("item", ["bokstav a", "nr. 3"])
@pytest.mark.parametrize("excepted", [True, False])
def test_body_item_decision_does_not_control_heading_timing(item: str, excepted: bool) -> None:
    """Enforce the heading/body contract stated in commit e645ea4dfed."""
    law = _law("Loven trer i kraft fra den tid Kongen bestemmer.")
    sentence = (
        f"Loven trer i kraft 1. januar 2023 med unntak av § 1 {item}."
        if excepted
        else f"Følgende trer i kraft 1. januar 2023: Endringsloven del I § 1 {item}."
    )
    decision = _decision(
        "forskrift/2022-12-01-999",
        "Delt ikraftsetting av lov 18. desember 2020 nr. 999",
        "2023-01-01",
        sentence,
        (LAW,),
    )
    heading = Operation(
        id="heading",
        kind="replace_heading",
        act="lov/2019-06-14-99",
        address=Address(section="§1"),
        instruction="§ 1 overskriften skal lyde:",
        part="I",
    )
    body = heading.model_copy(
        update={
            "id": "body",
            "kind": "replace_bokstav",
            "address": Address(section="§1", bokstav="a" if item == "bokstav a" else "3"),
        }
    )
    timings = resolve_timings(law, (), (heading, body), (decision,))
    dated, pending = ("heading", "body") if excepted else ("body", "heading")
    assert timings[dated].status == "dated"
    assert timings[dated].dated_on == date(2023, 1, 1)
    proof = timings[dated].proofs[-1]
    assert (proof.refid, proof.member, proof.sha256) == (
        decision.header.refid,
        decision.member,
        decision.sha256,
    )
    assert proof.quote_sha256 == sha256(sentence.encode("utf-8")).hexdigest()
    assert timings[pending].status == "pending"
    assert timings[pending].effective is None
