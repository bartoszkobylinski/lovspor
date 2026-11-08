"""Phase 3 (#744): replay against a per-unit commencement (ADR-0020 Decision 4).

States immediately before and after each relevant date of barnevernsloven's
staged commencement, on a synthetic act with the decision's own sentences.
"""

from datetime import date, datetime

import pytest

from lovspor.legaltime.baseline import Commencement, UnitTiming, baseline_commencement
from lovspor.legaltime.commencement import Event, Timing
from lovspor.legaltime.document import Ledd, Provision
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.replay import RESTRUCTURED, replay
from lovspor.legaltime.units import Unit
from tests.unit.test_legaltime_baseline import ACT, STAGED, _act

ACT_DOC = _act()
COMMENCEMENT = baseline_commencement(ACT_DOC, (STAGED,), [])


def _dated(when: date, known: date | None = None) -> Timing:
    return Timing(status="dated", effective=when, known_from=known or when)


def _event(seq: int, op: Operation, when: date) -> Event:
    return Event(
        sequence=seq, law=f"lov/{when.isoformat()}-1", member=f"lti/{seq}.xml", operation=op,
        timing=_dated(when), published_at=datetime(when.year, when.month, when.day),
    )  # fmt: skip


def _ledd(text: str) -> Ledd:
    return Ledd(xml=text, text=text)


def _op(seq: int, kind: str, **fields: object) -> Operation:
    address = fields.pop("address", Address())
    return Operation(
        id=f"op{seq}", kind=kind, act=ACT, address=address, instruction=f"{kind} {seq}",  # type: ignore[arg-type]
        part="I", **fields,  # type: ignore[arg-type]
    )  # fmt: skip


INSERT_LEDD_4 = _event(
    1,
    _op(1, "insert_ledd", address=Address(section="§15-7", ledd=4), ledd=(_ledd("Fire."),)),
    date(2022, 6, 30),
)
NEW_2_6 = _event(
    2,
    _op(
        2,
        "insert_section",
        address=Address(section="§2-6"),
        new_provision=Provision(key="§2-6", label="§ 2-6", heading="", ledd=(_ledd("Ny."),)),
    ),
    date(2022, 3, 25),
)
REPLACE_LEDD_4 = _event(
    3,
    _op(3, "replace_ledd", address=Address(section="§15-7", ledd=4), ledd=(_ledd("Fire b."),)),
    date(2023, 3, 1),
)
EVENTS = [INSERT_LEDD_4, NEW_2_6, REPLACE_LEDD_4]


def test_the_day_before_the_act_commences_nothing_is_in_force_and_dated_operations_wait() -> None:
    state = replay(ACT_DOC, COMMENCEMENT, EVENTS, date(2022, 12, 31))
    assert state.status == "not_yet_in_force"
    assert state.provisions == ()
    assert set(state.withheld) == {"op1", "op2"} and state.future == ("op3",)


def test_on_the_commencement_day_the_act_is_in_force_less_its_excepted_units() -> None:
    state = replay(ACT_DOC, COMMENCEMENT, EVENTS, date(2023, 1, 1))
    assert state.status == "replayed"
    by = {p.key: p for p in state.provisions}
    assert (by["§2-1"].in_force, by["§2-1"].effective) == ("in_force", date(2023, 1, 1))
    assert "§2-6" not in by and "op2" in state.withheld  # excepted, pending: never inserted
    assert state.applied == ("op1",)  # the fourth ledd exists from 2023-01-01 …
    assert len(by["§15-7"].ledd) == 4
    assert by["§15-7"].ledd_not_in_force == (4,)  # … but is not in force until 2023-07-01
    assert by["§15-7"].in_force == "in_force" and by["§15-7"].status == "replayed"
    assert state.not_in_force == () and state.pending_commencement == ()


def test_an_operation_on_a_ledd_not_yet_in_force_waits_until_its_day() -> None:
    before = replay(ACT_DOC, COMMENCEMENT, EVENTS, date(2023, 6, 30))
    assert "op3" in before.withheld and "op3" not in before.applied
    assert {p.key: p.ledd_not_in_force for p in before.provisions}["§15-7"] == (4,)
    after = replay(ACT_DOC, COMMENCEMENT, EVENTS, date(2023, 7, 1))
    provision = {p.key: p for p in after.provisions}["§15-7"]
    assert "op3" in after.applied and provision.ledd[3].text == "Fire b."
    assert provision.ledd_not_in_force == ()


def test_a_restructuring_of_a_provision_with_a_flagged_ledd_is_refused_not_applied() -> None:
    rewrite = _event(
        4,
        _op(
            4,
            "replace_section",
            address=Address(section="§15-7"),
            new_provision=Provision(
                key="§15-7", label="§ 15-7", heading="", ledd=tuple(_ledd(str(i)) for i in range(5))
            ),
        ),
        date(2023, 3, 1),
    )
    state = replay(ACT_DOC, COMMENCEMENT, [INSERT_LEDD_4, rewrite], date(2023, 4, 1))
    assert ("op4", RESTRUCTURED) in state.unresolved
    provision = {p.key: p for p in state.provisions}["§15-7"]
    assert provision.status == "unresolved" and len(provision.ledd) == 4  # text untouched
    same_count = rewrite.model_copy(
        update={
            "operation": rewrite.operation.model_copy(
                update={
                    "new_provision": rewrite.operation.new_provision.model_copy(  # type: ignore[union-attr]
                        update={"ledd": tuple(_ledd(str(i)) for i in range(4))}
                    )
                }
            )
        }
    )
    kept = replay(ACT_DOC, COMMENCEMENT, [INSERT_LEDD_4, same_count], date(2023, 4, 1))
    assert "op4" in kept.applied
    assert {p.key: p.ledd_not_in_force for p in kept.provisions}["§15-7"] == (4,)


def test_a_unit_whose_commencement_is_unresolved_has_no_replayed_text() -> None:
    unresolved = Commencement(
        default=_dated(date(2023, 1, 1)),
        units=(
            UnitTiming(
                unit=Unit(section="§15-7"),
                timing=Timing(status="unresolved", reason="conflicting commencement decisions"),
            ),
        ),
    )
    state = replay(ACT_DOC, unresolved, [REPLACE_LEDD_4], date(2024, 1, 1))
    provision = {p.key: p for p in state.provisions}["§15-7"]
    assert (provision.status, provision.in_force) == ("unresolved", "unresolved")
    assert provision.problems[0] == "commencement conflicting commencement decisions"
    assert ("op3", "commencement conflicting commencement decisions") in state.unresolved
    assert state.unresolved_commencement == ("§15-7",)


def test_a_bare_timing_is_the_act_wide_special_case() -> None:
    state = replay(ACT_DOC, _dated(date(2023, 1, 1)), EVENTS, date(2023, 4, 1))
    assert set(state.applied) == {"op1", "op2", "op3"}
    assert all(p.in_force == "in_force" and p.ledd_not_in_force == () for p in state.provisions)


def test_a_term_substitution_reaching_a_ledd_not_in_force_waits_for_it() -> None:
    """codex-tests on PR #848: a substitution names its ledd in ``locations``, not its address."""
    listed = _event(
        11,
        _op(
            11,
            "substitute_term",
            locations="§ 15-7 fjerde ledd",
            term_old="Fire",
            term_new="Endret",
        ),
        date(2023, 3, 1),
    )
    before = replay(ACT_DOC, COMMENCEMENT, [INSERT_LEDD_4, listed], date(2023, 6, 30))
    assert before.withheld == ("op11",) and before.applied == ("op1",)
    assert {p.key: p for p in before.provisions}["§15-7"].ledd[3].text == "Fire."
    on_day = replay(ACT_DOC, COMMENCEMENT, [INSERT_LEDD_4, listed], date(2023, 7, 1))
    assert on_day.applied == ("op1", "op11") and on_day.withheld == ()
    assert {p.key: p for p in on_day.provisions}["§15-7"].ledd[3].text == "Endret."
    whole = listed.model_copy(
        update={"operation": listed.operation.model_copy(update={"locations": "§ 15-7"})}
    )
    assert replay(ACT_DOC, COMMENCEMENT, [INSERT_LEDD_4, whole], date(2023, 6, 30)).withheld == (
        "op11",
    )  # no ledd listed: every ledd, the flagged one among them
    beside = listed.model_copy(
        update={
            "operation": listed.operation.model_copy(update={"locations": "§ 15-7 første ledd"})
        }
    )
    state = replay(ACT_DOC, COMMENCEMENT, [INSERT_LEDD_4, beside], date(2023, 6, 30))
    assert "op11" not in state.withheld  # a ledd in force beside the flagged one


@pytest.mark.parametrize("locations", ["§ 2-1, § 15-7", "§ 15-7, § 2-1"])
@pytest.mark.parametrize("pending", [False, True])
def test_multi_section_substitution_waits_atomically_for_every_target(
    locations: str, pending: bool
) -> None:
    """Decision 4 gates the whole operation, regardless of location order."""
    day = date(2023, 7, 1)
    commencement = Commencement(
        default=_dated(date(2023, 1, 1)),
        units=(
            UnitTiming(
                unit=Unit(section="§15-7"),
                timing=Timing(status="pending") if pending else _dated(day),
            ),
        ),
    )
    baseline = ACT_DOC.model_copy(
        update={
            "provisions": tuple(
                p.model_copy(update={"ledd": (_ledd("Felles ord."),)}) for p in ACT_DOC.provisions
            )
        }
    )
    event = _event(
        12,
        _op(12, "substitute_term", locations=locations, term_old="ord", term_new="tekst"),
        date(2023, 3, 1),
    )
    before = replay(baseline, commencement, [event], date(2023, 6, 30))
    assert before.withheld == ("op12",)
    assert before.applied == () and before.unresolved == ()
    assert all(p.ledd == (_ledd("Felles ord."),) and p.applied == () for p in before.provisions)
    assert before.pending_commencement == (("§15-7",) if pending else ())
    assert before.not_in_force == (() if pending else ("§15-7",))

    on_day = replay(baseline, commencement, [event], day)
    assert on_day.withheld == (("op12",) if pending else ())
    assert on_day.applied == (() if pending else ("op12",))
    expected = "Felles ord." if pending else "Felles tekst."
    assert all(p.ledd == (_ledd(expected),) for p in on_day.provisions)


def test_staged_replay_is_repeatable_without_mutating_inputs() -> None:
    """A later query must not leak applied edits or cleared flags into an earlier date."""
    documents = ACT_DOC.model_dump_json(), COMMENCEMENT.model_dump_json()
    events = [e.model_dump_json() for e in EVENTS]
    before = replay(ACT_DOC, COMMENCEMENT, EVENTS, date(2023, 6, 30)).model_dump_json()
    replay(ACT_DOC, COMMENCEMENT, EVENTS, date(2023, 7, 1))
    assert replay(ACT_DOC, COMMENCEMENT, EVENTS, date(2023, 6, 30)).model_dump_json() == before
    assert (ACT_DOC.model_dump_json(), COMMENCEMENT.model_dump_json()) == documents
    assert [e.model_dump_json() for e in EVENTS] == events
