"""Fail-closed semantics: a failed operation changes nothing, and an operation whose target is
unknown leaves no provision provable."""

from datetime import date
from pathlib import Path

from lovspor.legaltime import replay, verify
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document, Ledd, parse_document
from lovspor.legaltime.instructions import Address, Operation, _Reader
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


def test_blanket_detail_joins_every_act_level_problem() -> None:
    state = _run(
        _event(_op("renumber", Address())),
        _event(_op("renumber", Address()).model_copy(update={"id": "x#I:1.0"})),
    )
    assert len(state.act_level_problems) == 2
    verification = verify.verify(state, _load("consolidated.xml"))
    assert verification.sections[0].detail == (
        "act-level problem, target unknown: x#I:0.0: renumbering not implied by the operation "
        "before it; x#I:1.0: renumbering not implied by the operation before it"
    )


def test_every_node_carries_a_tuple_change_part_and_its_host() -> None:
    document = _load("enacting-embedded.xml")
    nodes = [node for part in document.parts for node in part.nodes]
    assert nodes and all(isinstance(node.change_part, tuple) for node in nodes)
    assert {node.host_section for node in nodes if node.kind == "ledd"} >= {"§25"}


def test_substitution_operations_carry_their_host_section() -> None:
    reader = _Reader(_load("enacting-embedded.xml"))
    op = reader._substitute(("I", "I:3", "§25"), "lov/2019-06-14-99", "I § 1 og § 2.", ("a", "b"))
    assert op.host_section == "§25" and op.kind == "substitute_term"


def test_a_bokstav_replacement_that_breaks_the_markup_is_refused_not_raised() -> None:
    """utlendingsloven, 2026-11-07: a list item whose text holds a nested article made the
    regex rewrite cut mid-structure and lxml raise; the replay must refuse, never crash."""
    working = _Working(_load("enacting.xml"))
    nested = (
        'Følgende gjelder:<ol class="defaultList"><li data-name="a."><article class="listArticle">'
        '<article class="legalP">bokstav a<article class="legalP">indre</article></article>'
        "</article></li></ol>"
    )
    working.map["§2"].ledd[0] = Ledd(xml=nested, text="Følgende gjelder: bokstav a indre")
    op = _op(
        "replace_bokstav",
        Address(section="§2", ledd=1, bokstav="a"),
        ledd=(Ledd(xml="<b>x", text="x"),),
    )
    assert (
        working.apply_replace_bokstav(op) == "bokstav replacement leaves the ledd markup ill-formed"
    )
    assert working.map["§2"].ledd[0].xml == nested
