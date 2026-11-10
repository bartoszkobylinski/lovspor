"""Phase 4A (#858, #859, #861): ADR-0020 Decision 7's blanket sees every unknown-target operation.

Two legs were missing. An operation with no target whose timing is ``unresolved`` may be
in effect at D (its date is unknown, not future), so from its ``known_from`` on (from the
start, when that is unknown) it is an act-level problem (#858). An operation the amending
law states without a recognisable act may belong to the replayed act, so wherever it would
be in effect it keeps the state unproven (#859). Neither is ever applied, and neither
names, narrows or guesses a target.
"""

from datetime import date, datetime
from pathlib import Path

import pytest

from lovspor.legaltime import replay, verify
from lovspor.legaltime.archive import Evidence
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.replay import Event, ReplayState
from lovspor.legaltime.run import build_report
from lovspor.legaltime.verify import PROVEN

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
ACT = "lov/2019-06-14-99"
ENACTED = Timing(status="dated", effective=date(2021, 1, 1))
AT = date(2023, 1, 1)
UNRESOLVED = Timing(status="unresolved", reason="commencement sentence outside the grammar")


def _load(name: str) -> Document:
    return parse_document((FIXTURES / name).read_bytes(), f"lti/x/{name}")


def _op(kind: str, address: Address, act: str | None = ACT, op_id: str = "x#I:0.0") -> Operation:
    return Operation(
        id=op_id, kind=kind, act=act, address=address, instruction="i", part="I",  # type: ignore[arg-type]
    )  # fmt: skip


def _event(op: Operation, timing: Timing) -> Event:
    return Event(
        sequence=0, law="lov/2022-01-01-1", member="lti/2022/nl-20220101-001.xml", operation=op,
        timing=timing, published_at=datetime(2022, 1, 1, 12, 0),
    )  # fmt: skip


def _state(*events: Event, at: date = AT) -> ReplayState:
    return replay.replay(_load("enacting.xml"), ENACTED, list(events), at)


def _proven(state: ReplayState) -> set[str]:
    verification = verify.verify(state, _load("consolidated.xml"))
    return {s.key for s in verification.sections if s.verdict in PROVEN}


def test_the_clean_replay_proves_sections_so_the_blanket_is_observable() -> None:
    assert _proven(_state())


# --- #858: unknown target, unresolved timing ------------------------------------------------


@pytest.mark.parametrize("kind", ["unsupported", "renumber"])
def test_an_unknown_target_with_unknown_timing_and_knowledge_blankets_every_state(
    kind: str,
) -> None:
    event = _event(_op(kind, Address()), UNRESOLVED)
    for at in (date(2021, 1, 1), AT):
        state = _state(event, at=at)
        assert state.act_level_problems == (
            "x#I:0.0: target unknown, commencement commencement sentence outside the grammar",
        )
        assert state.unresolved == (("x#I:0.0", "commencement sentence outside the grammar"),)
        assert _proven(state) == set()


def test_an_unknown_target_with_unknown_timing_blankets_from_its_known_from_on() -> None:
    timing = UNRESOLVED.model_copy(update={"known_from": date(2022, 6, 1)})
    event = _event(_op("unsupported", Address()), timing)
    assert _state(event, at=date(2022, 5, 31)).act_level_problems == ()
    assert _proven(_state(event, at=date(2022, 5, 31)))
    for at in (date(2022, 6, 1), AT):
        assert len(_state(event, at=at).act_level_problems) == 1
        assert _proven(_state(event, at=at)) == set()


def test_a_named_target_with_unknown_timing_stays_on_its_own_section() -> None:
    state = _state(_event(_op("repeal_section", Address(section="§1")), UNRESOLVED))
    assert state.act_level_problems == ()
    one = next(p for p in state.provisions if p.key == "§1")
    assert one.problems == ("x#I:0.0: commencement commencement sentence outside the grammar",)


@pytest.mark.parametrize(
    "timing",
    [
        Timing(status="pending", known_from=date(2022, 1, 1)),
        Timing(status="dated", effective=date(2024, 1, 1), known_from=date(2022, 1, 1)),
    ],
)
def test_an_unknown_target_not_in_effect_at_d_is_listed_not_blanketed(timing: Timing) -> None:
    state = _state(_event(_op("unsupported", Address()), timing))
    assert state.act_level_problems == ()
    assert _proven(state)


# --- #859: unattributed operations -----------------------------------------------------------


def test_an_unattributed_insertion_in_effect_keeps_the_state_unproven_and_is_never_applied() -> (
    None
):
    insertion = _op("insert_section", Address(section="§5"), act=None)
    dated = Timing(status="dated", effective=date(2022, 1, 1), known_from=date(2022, 1, 1))
    state = _state(_event(insertion, dated))
    assert state.act_level_problems == (
        "x#I:0.0: unattributed operation, act unknown (lti/2022/nl-20220101-001.xml)",
    )
    assert "§5" not in {p.key for p in state.provisions}
    assert state.applied == () and state.unresolved == ()
    verification = verify.verify(state, _load("consolidated.xml"))
    assert set(verification.counts) == {"unresolved"}
    assert verification.act_level_problems == state.act_level_problems


def test_an_unattributed_operation_with_unknown_timing_follows_the_known_from_rule() -> None:
    op = _op("unsupported", Address(), act=None)
    unknown = _state(_event(op, UNRESOLVED))
    assert unknown.act_level_problems == (
        "x#I:0.0: unattributed operation, act unknown (lti/2022/nl-20220101-001.xml); "
        "commencement commencement sentence outside the grammar",
    )
    later = UNRESOLVED.model_copy(update={"known_from": date(2024, 1, 1)})
    assert _state(_event(op, later)).act_level_problems == ()


@pytest.mark.parametrize(
    "timing",
    [
        Timing(status="pending", known_from=date(2022, 1, 1)),
        Timing(status="dated", effective=date(2024, 1, 1), known_from=date(2022, 1, 1)),
    ],
)
def test_an_unattributed_operation_not_in_effect_at_d_leaves_the_state_alone(
    timing: Timing,
) -> None:
    state = _state(_event(_op("insert_section", Address(section="§5"), act=None), timing))
    assert state.act_level_problems == () and state.pending == () and state.future == ()
    assert _proven(state)


def test_an_unattributed_operation_blankets_a_state_whose_act_is_not_yet_in_force() -> None:
    """Recorded on every state it may reach; it is never filed as a withheld operation."""
    dated = Timing(status="dated", effective=date(2020, 6, 1), known_from=date(2020, 6, 1))
    state = _state(
        _event(_op("insert_section", Address(section="§5"), act=None), dated), at=date(2020, 7, 1)
    )
    assert state.status == "not_yet_in_force"
    assert state.withheld == ()
    assert len(state.act_level_problems) == 1


def _amender(unattributed: bool) -> Document:
    intro = "I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer:"
    lead = "" if not unattributed else '<article class="defaultP">Ny § 5 skal lyde:</article>'
    xml = (
        '<html><body><header><dl><dd class="refid">lov/2022-01-01-1</dd>'
        '<dd class="dateInForce">2022-01-01</dd>'
        '<dd class="dateOfPublication">2022-01-01 12:00</dd><dd class="title">Lov om endring</dd>'
        "</dl></header><main>"
        f'<section class="section"><h2>I</h2>{lead}</section>'
        f'<section class="section"><h2>II</h2><article class="legalP">{intro}</article>'
        '<article class="defaultP">§ 4 oppheves.</article></section>'
        "</main></body></html>"
    )
    return parse_document(xml.encode(), "lti/2022/nl-20220101-001.xml")


def _report(unattributed: bool) -> dict[str, object]:
    baseline = _load("enacting.xml")
    baseline = baseline.model_copy(
        update={"header": baseline.header.model_copy(update={"date_in_force": "2021-01-01"})}
    )
    evidence = Evidence(
        act=ACT, archives=(), baseline=baseline, amenders=(_amender(unattributed),), decisions=()
    )
    return build_report(evidence, [AT], _load("consolidated.xml"), [])


def test_the_report_carries_the_unattributed_operation_its_state_and_its_verification() -> None:
    clean, flagged = _report(unattributed=False), _report(unattributed=True)
    assert clean["unattributed_events"] == []
    (record,) = flagged["unattributed_events"]  # type: ignore[misc]
    assert record["operation"]["id"] == "lov/2022-01-01-1#0:I:0.0"
    assert record["member"] == "lti/2022/nl-20220101-001.xml"
    assert record["timing"]["status"] == "dated"
    (state,) = flagged["states"]  # type: ignore[misc]
    assert state["act_level_problems"] == [
        "lov/2022-01-01-1#0:I:0.0: unattributed operation, act unknown "
        "(lti/2022/nl-20220101-001.xml)"
    ]
    (verification,) = flagged["verification"]  # type: ignore[misc]
    assert set(verification["counts"]) == {"unresolved"}
    (clean_state,) = clean["states"]  # type: ignore[misc]
    assert clean_state["act_level_problems"] == []
    assert [e["operation"]["id"] for e in flagged["events"]] == [  # type: ignore[index]
        e["operation"]["id"]
        for e in clean["events"]  # type: ignore[index]
    ]
