"""Fail-closed semantics: a failed operation changes nothing, and an operation whose target is
unknown leaves no provision provable."""

from datetime import date
from pathlib import Path

from lovspor.legaltime import replay, verify
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.replay import Event, ReplayState, _Working

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
ENACTED = Timing(status="dated", effective=date(2021, 1, 1))


def _load(name: str) -> Document:
    return parse_document((FIXTURES / name).read_bytes(), f"lti/x/{name}")


def _op(kind: str, address: Address, **extra: object) -> Operation:
    return Operation(
        id="x#I:0.0", kind=kind, act="lov/2019-06-14-99", address=address, instruction="i",  # type: ignore[arg-type]
        part="I", **extra,  # type: ignore[arg-type]
    )  # fmt: skip


def _event(op: Operation) -> Event:
    timing = Timing(status="dated", effective=date(2022, 1, 1))
    return Event(sequence=0, law="x", member="m", operation=op, timing=timing, published_at=None)


def _run(*events: Event) -> ReplayState:
    return replay.replay(_load("enacting.xml"), ENACTED, list(events), date(2023, 1, 1))


def test_substitution_that_misses_one_location_changes_none_of_them() -> None:
    working = _Working(_load("enacting.xml"))
    before = [ledd.text for ledd in working.map["§1"].ledd]
    op = _op(
        "substitute_term", Address(), locations="§ 1, § 2", term_old="prøving", term_new="testing"
    )
    assert working.apply_substitute_term(op) == "term not found in ['§2']"
    assert [ledd.text for ledd in working.map["§1"].ledd] == before


def test_an_act_level_problem_leaves_no_section_provable() -> None:
    state = _run(_event(_op("renumber", Address())))
    assert state.act_level_problems == (
        "x#I:0.0: renumbering not implied by the operation before it",
    )
    verification = verify.verify(state, _load("consolidated.xml"))
    assert set(verification.counts) == {"unresolved"}
    assert verification.act_level_problems == state.act_level_problems
    assert all(
        s.detail == "act-level problem, target unknown: x#I:0.0: renumbering not implied by the "
        "operation before it"
        for s in verification.sections
    )


def test_without_act_level_problems_sections_keep_their_own_verdicts() -> None:
    state = _run()
    verification = verify.verify(state, _load("consolidated.xml"))
    assert verification.act_level_problems == ()
    assert "unresolved" not in verification.counts or verification.counts["unresolved"] < len(
        verification.sections
    )


def test_an_operation_that_fails_marks_only_its_target_and_leaves_the_text() -> None:
    state = _run(_event(_op("repeal_ledd", Address(section="§1", ledd=9))))
    one = next(p for p in state.provisions if p.key == "§1")
    assert one.status == "unresolved" and len(one.ledd) == 2
    assert all(p.status == "replayed" for p in state.provisions if p.key != "§1")
    assert state.act_level_problems == ()
