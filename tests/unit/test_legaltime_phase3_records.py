"""Phase 3 (#744): the report records and the replay buckets, value by value."""

from datetime import date

from lovspor.legaltime.baseline import Commencement, UnitTiming
from lovspor.legaltime.commencement import Proof, Timing
from lovspor.legaltime.document import Ledd, Provision
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.replay import ProvisionState, ReplayState, _Working, replay
from lovspor.legaltime.run import _commencement_record, _provision_record, _state_record
from lovspor.legaltime.units import Unit
from lovspor.legaltime.verify import SectionVerdict, _ledd_counts, verify
from tests.unit.test_legaltime_baseline import ACT, _act

PROOF = Proof(
    refid="forskrift/2022-10-14-1739",
    member="lti/2022/x.xml",
    sha256="c" * 64,
    quote_sha256="d" * 64,
)
DATED = Timing(
    status="dated", effective=date(2023, 1, 1), known_from=date(2022, 10, 14), proofs=(PROOF,)
)


def test_commencement_record_lists_default_and_every_unit() -> None:
    commencement = Commencement(
        default=DATED,
        units=(
            UnitTiming(unit=Unit(section="§2-6"), timing=Timing(status="pending")),
            UnitTiming(
                unit=Unit(chapter="kap16"), part="I", timing=Timing(status="unresolved", reason="x")
            ),
        ),
    )
    record = _commencement_record(commencement)
    assert record["default"]["effective"] == "2023-01-01"
    assert record["default"]["proofs"][0]["refid"] == "forskrift/2022-10-14-1739"
    assert record["units"] == [
        {"unit": "§2-6", "part": None, "timing": Timing(status="pending").model_dump(mode="json")},
        {
            "unit": "kap16",
            "part": "I",
            "timing": Timing(status="unresolved", reason="x").model_dump(mode="json"),
        },
    ]


def _state(*provisions: ProvisionState, **lists: tuple) -> ReplayState:
    base = {
        "applied": (), "future": (), "pending": (), "unresolved": (), "act_level_problems": (),
        "withheld": (),
    }  # fmt: skip
    base.update(lists)
    return ReplayState(
        act=ACT, at=date(2023, 1, 1), status="replayed", provisions=provisions, **base
    )  # type: ignore[arg-type]


def test_provision_record_adds_effectiveness_fields_only_when_they_say_something() -> None:
    plain = ProvisionState(key="§1", label="§ 1", heading="", ledd=(Ledd(xml="a", text="a"),))
    flagged = plain.model_copy(
        update={
            "key": "§2",
            "in_force": "in_force",
            "ledd_not_in_force": (4,),
            "effective": date(2023, 1, 1),
        }
    )
    pending = plain.model_copy(update={"key": "§3", "in_force": "pending", "status": "replayed"})
    records = _provision_record(_state(plain, flagged, pending))
    assert records[0] == {
        "key": "§1",
        "status": "replayed",
        "repealed": False,
        "ledd": 1,
        "applied": [],
        "problems": [],
    }
    assert records[1]["ledd_not_in_force"] == [4] and "in_force" not in records[1]
    assert records[2]["in_force"] == "pending" and "ledd_not_in_force" not in records[2]


def test_state_record_carries_the_unit_classes() -> None:
    record = _state_record(
        _state(
            not_in_force=("§5",), pending_commencement=("§2-6",), unresolved_commencement=("§9",)
        )
    )
    assert record["not_in_force"] == ["§5"]
    assert record["pending_commencement"] == ["§2-6"]
    assert record["unresolved_commencement"] == ["§9"]
    assert set(record) == {
        "at", "status", "applied", "future", "pending", "unresolved", "act_level_problems",
        "withheld", "not_in_force", "pending_commencement", "unresolved_commencement", "provisions",
    }  # fmt: skip


def test_replay_state_lists_each_unit_class_from_the_provisions() -> None:
    commencement = Commencement(
        default=DATED,
        units=(
            UnitTiming(
                unit=Unit(section="§2-1"), timing=Timing(status="dated", effective=date(2024, 1, 1))
            ),
            UnitTiming(unit=Unit(section="§15-7"), timing=Timing(status="unresolved", reason="x")),
        ),
    )
    state = replay(_act(), commencement, [], date(2023, 6, 1))
    assert state.not_in_force == ("§2-1",)
    assert state.pending_commencement == ()
    assert state.unresolved_commencement == ("§15-7",)
    by = {p.key: p for p in state.provisions}
    assert (by["§2-1"].in_force, by["§2-1"].effective, by["§2-1"].status) == (
        "not_yet_in_force",
        date(2024, 1, 1),
        "replayed",
    )
    assert (by["§15-7"].status, by["§15-7"].problems) == ("unresolved", ("commencement x",))


def test_inserted_provisions_take_their_unit_state_from_chapter_and_parts() -> None:
    commencement = Commencement(
        default=DATED,
        units=(
            UnitTiming(
                unit=Unit(chapter="kap16"),
                part="I",
                timing=Timing(status="dated", effective=date(2030, 1, 1)),
            ),
        ),
    )
    working = _Working(_act(), commencement, date(2023, 6, 1))
    new = Provision(
        key="§101", label="§ 101", heading="", ledd=(Ledd(xml="a", text="a"),), chapter="kap16"
    )
    op = Operation(
        id="i",
        kind="insert_section",
        act=ACT,
        address=Address(section="§101"),
        instruction="",
        part="I",
        new_provision=new,
    )
    assert working.apply_insert_section(op) is None
    assert working.map["§101"].unit.in_force == "not_yet_in_force"
    elsewhere = Operation(
        id="j",
        kind="insert_section",
        act=ACT,
        address=Address(section="§102"),
        instruction="",
        part="II",
        new_provision=new.model_copy(update={"key": "§102"}),
    )
    assert working.apply_insert_section(elsewhere) is None
    assert (
        working.map["§102"].unit.in_force == "in_force"
    )  # part II does not reach "del I kapittel 16"
    assert working.order.index("§101") < working.order.index("§102")
    bare = _Working(_act())
    assert bare.unit_state("§1", None, ()).in_force == "in_force"


def test_ledd_counts_classes_and_denominator() -> None:
    oracle = {
        "§1": Provision(key="§1", label="§ 1", heading="", ledd=(Ledd(xml="a", text="a"),) * 3),
        "§2": Provision(key="§2", label="§ 2", heading="", ledd=(Ledd(xml="a", text="a"),) * 2),
        "§3": Provision(key="§3", label="§ 3", heading="", ledd=(Ledd(xml="a", text="a"),)),
    }
    replayed = {
        "§1": ProvisionState(
            key="§1", label="§ 1", heading="", ledd=oracle["§1"].ledd, ledd_not_in_force=(3,)
        ),
        "§2": ProvisionState(key="§2", label="§ 2", heading="", ledd=oracle["§2"].ledd),
    }
    sections = [
        SectionVerdict(key="§1", verdict="exact_raw"),
        SectionVerdict(key="§2", verdict="text_differs"),
        SectionVerdict(key="§3", verdict="only_in_oracle"),
    ]
    assert _ledd_counts(sections, replayed, oracle) == {
        "ledd_not_in_force": 1,
        "ledd_proven": 2,
        "ledd_text_differs": 2,
        "oracle_ledd": 5,
    }


def test_verify_marks_not_in_force_and_counts_behind_the_blanket() -> None:
    act = _act()
    commencement = Commencement(
        default=DATED,
        units=(
            UnitTiming(
                unit=Unit(section="§2-1"), timing=Timing(status="dated", effective=date(2030, 1, 1))
            ),
        ),
    )
    state = replay(act, commencement, [], date(2023, 6, 1))
    verdicts = {s.key: s for s in verify(state, act).sections}
    assert verdicts["§2-1"].verdict == "not_in_force"
    assert verdicts["§2-1"].detail == "not_yet_in_force; commencement 2030-01-01"
    assert verdicts["§15-7"].verdict == "exact_raw"
    blanketed = state.model_copy(update={"act_level_problems": ("op: unsupported instruction",)})
    verification = verify(blanketed, act)
    assert verification.counts == {"unresolved": 2}
    assert verification.counts_before_blanket == {"exact_raw": 1, "not_in_force": 1}
    assert verify(state, act).counts_before_blanket == {}
