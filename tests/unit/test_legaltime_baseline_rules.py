"""Phase 3 (#744): the fold rules of baseline.py, one branch at a time.

Exact values for every branch the per-unit commencement fold takes, so a
mutant that flips a sign, drops a proof or widens a reach is caught by a test
that names the value it changed.
"""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from lovspor.legaltime.baseline import (
    BELOW_LEDD,
    INSTRUMENT_UNDATED,
    NAMES_NOTHING,
    Commencement,
    UnitState,
    UnitTiming,
    _Fold,
    _fold_statement,
    _in_force,
    _member_sha,
    _ordered,
    _part_name,
    _payload_sentence,
    _roman,
    _Statement,
    _statement,
    addresses_flagged,
    baseline_commencement,
    instrument_statements,
    restructures,
    state_gate,
    unit_state,
)
from lovspor.legaltime.commencement import CONFLICT, Event, Proof, Timing
from lovspor.legaltime.document import Ledd, Provision
from lovspor.legaltime.instructions import Address, Operation
from lovspor.legaltime.units import Unit
from tests.unit.test_legaltime_baseline import ACT, _act, _decision

PROOF_A = Proof(
    refid="lov/2015-06-19-65", member="lti/2015/a.xml", sha256="a" * 64, quote_sha256="1" * 64
)
PROOF_B = Proof(
    refid="forskrift/2008-03-07-225",
    member="lti/2008/b.xml",
    sha256="b" * 64,
    quote_sha256="2" * 64,
)


def _timing(status: str, when: date | None = None, proofs: tuple[Proof, ...] = ()) -> Timing:
    return Timing(status=status, effective=when, proofs=proofs)  # type: ignore[arg-type]


def _statement_of(
    when: date | None, known: date | None = date(2015, 6, 19), problem: str | None = None
) -> _Statement:
    return _Statement(
        when=when, known_from=known, published_at=datetime(2015, 6, 19, 15, 20), proof=PROOF_A,
        problem=problem,
    )  # fmt: skip


# ----------------------------------------------------------------- small helpers


@pytest.mark.parametrize(
    "numeral, value",
    [
        ("I", 1),
        ("II", 2),
        ("IV", 4),
        ("V", 5),
        ("IX", 9),
        ("X", 10),
        ("XIV", 14),
        ("XL", 40),
        ("XC", 90),
        ("C", 100),
    ],
)
def test_roman_numerals(numeral: str, value: int) -> None:
    assert _roman(numeral) == value
    assert _part_name(numeral) == f"del{value}"
    assert _part_name(numeral.lower()) == f"del{value}"


def test_in_force_states() -> None:
    at = date(2023, 1, 1)
    assert _in_force(_timing("dated", date(2023, 1, 1)), at) == ("in_force", date(2023, 1, 1))
    assert _in_force(_timing("dated", date(2022, 12, 31)), at) == ("in_force", date(2022, 12, 31))
    assert _in_force(_timing("dated", date(2023, 1, 2)), at) == (
        "not_yet_in_force",
        date(2023, 1, 2),
    )
    assert _in_force(_timing("pending"), at) == ("pending", None)
    assert _in_force(_timing("unresolved"), at) == ("unresolved", None)
    assert _in_force(Timing(status="dated", effective=None), at) == ("unresolved", None)


def test_state_gate_values() -> None:
    assert (
        state_gate(UnitState(in_force="in_force", effective=None, flagged=(), problems=())) is None
    )
    assert (
        state_gate(UnitState(in_force="pending", effective=None, flagged=(), problems=()))
        == "withheld"
    )
    assert (
        state_gate(
            UnitState(
                in_force="not_yet_in_force", effective=date(2030, 1, 1), flagged=(), problems=()
            )
        )
        == "withheld"
    )
    assert (
        state_gate(
            UnitState(in_force="unresolved", effective=None, flagged=(), problems=("a", "b"))
        )
        == "a; b"
    )


# ------------------------------------------------------------- instrument statements


def test_fold_statement_branches() -> None:
    assert _fold_statement(_timing("unresolved"), _statement_of(date(2015, 10, 1))) == _timing(
        "unresolved"
    )
    broken = _fold_statement(
        _timing("pending", proofs=(PROOF_B,)), _statement_of(None, problem=INSTRUMENT_UNDATED)
    )
    assert (broken.status, broken.reason, broken.effective, broken.proofs) == (
        "unresolved",
        INSTRUMENT_UNDATED,
        None,
        (PROOF_B, PROOF_A),
    )
    undated = _fold_statement(_timing("pending"), _statement_of(None))
    assert undated.reason == "commencement statement without a readable date"
    dated = _fold_statement(_timing("pending", proofs=(PROOF_B,)), _statement_of(date(2015, 10, 1)))
    assert (dated.status, dated.effective, dated.known_from, dated.retroactive) == (
        "dated",
        date(2015, 10, 1),
        date(2015, 6, 19),
        False,
    )
    assert dated.proofs == (PROOF_A,)  # the header's proof (first) is replaced by the instrument's
    retro = _fold_statement(_timing("pending"), _statement_of(date(2015, 1, 1)))
    assert retro.retroactive is True
    unknown = _fold_statement(_timing("pending"), _statement_of(date(2015, 10, 1), known=None))
    assert (unknown.known_from, unknown.retroactive) == (None, False)
    agreed = _fold_statement(
        _timing("dated", date(2015, 10, 1), (PROOF_B,)), _statement_of(date(2015, 10, 1))
    )
    assert (agreed.status, agreed.proofs) == ("dated", (PROOF_B, PROOF_A))
    conflict = _fold_statement(
        _timing("dated", date(2008, 3, 7), (PROOF_B,)), _statement_of(date(2015, 10, 1))
    )
    assert (conflict.status, conflict.reason, conflict.effective) == ("unresolved", CONFLICT, None)
    assert (conflict.retroactive, conflict.proofs) == (False, (PROOF_B, PROOF_A))


def _event(
    kind: str, text: str | None, status: str = "dated", with_sha: bool = True, **address: object
) -> Event:
    ledd = (Ledd(xml=text, text=text),) if text is not None and kind != "replace_section" else ()
    new = (
        Provision(
            key="§411", label="§ 411", heading="", ledd=(Ledd(xml=text, text=text),) if text else ()
        )
        if kind == "replace_section"
        else None
    )
    op = Operation(
        id="b#1", kind=kind, act=ACT, address=Address(**address), instruction="x", part="",  # type: ignore[arg-type]
        ledd=ledd, new_provision=new,
    )  # fmt: skip
    proofs = (
        (
            Proof(
                refid="lov/2015-06-19-65",
                member="lti/2015/a.xml",
                sha256="a" * 64,
                quote_sha256="0" * 64,
            ),
        )
        if with_sha
        else ()
    )
    timing = Timing(
        status=status, effective=date(2015, 6, 19) if status == "dated" else None, proofs=proofs
    )  # type: ignore[arg-type]
    return Event(
        sequence=1,
        law="lov/2015-06-19-65",
        member="lti/2015/a.xml",
        operation=op,
        timing=timing,
        published_at=datetime(2015, 6, 19, 15, 20),
    )


def test_payload_sentence_by_operation_kind() -> None:
    assert (
        _payload_sentence(
            _event("replace_ledd", "Loven trer i kraft 1. oktober 2015. Fra samme tid.")
        )
        == "Loven trer i kraft 1. oktober 2015."
    )
    assert (
        _payload_sentence(_event("insert_ledd", "Loven trer i kraft straks."))
        == "Loven trer i kraft straks."
    )
    assert (
        _payload_sentence(_event("replace_section", "Loven trer i kraft 1. oktober 2015."))
        == "Loven trer i kraft 1. oktober 2015."
    )
    assert _payload_sentence(_event("replace_section", None)) is None
    assert _payload_sentence(_event("replace_ledd", None)) is None
    assert _payload_sentence(_event("repeal_ledd", "Loven trer i kraft 1. oktober 2015.")) is None
    assert (
        _payload_sentence(_event("replace_punktum", "Loven trer i kraft 1. oktober 2015.")) is None
    )


def test_statement_reading() -> None:
    event = _event("replace_ledd", "Loven trer i kraft 1. oktober 2015.")
    read = _statement(event, "Loven trer i kraft 1. oktober 2015.")
    assert read is not None
    assert (read.when, read.known_from, read.problem) == (
        date(2015, 10, 1),
        date(2015, 6, 19),
        None,
    )
    assert read.proof.refid == "lov/2015-06-19-65" and read.proof.sha256 == "a" * 64
    assert read.proof.quote_sha256 != "0" * 64 and len(read.proof.quote_sha256) == 64
    assert _statement(event, "Loven trer i kraft 1. oktober 2015, med unntak av § 2.") is None
    assert _statement(event, "Forskrifter gitt i medhold av loven gjelder.") is None
    assert _statement(event, "Loven gjelder fra den tid Kongen bestemmer.") is None
    straks = _statement(event, "Loven trer i kraft straks.")
    assert straks is not None and straks.when == date(2015, 6, 19)
    both = _statement(event, "Loven trer i kraft straks 1. oktober 2015.")
    assert both is not None and both.when == date(2015, 10, 1)
    cited = _statement(event, "Loven trer i kraft fra den dag lov 22. mai 1902 nr. 10 oppheves.")
    assert (
        cited is not None
        and cited.when is None
        and cited.problem == "commencement statement without a readable date"
    )
    assert (
        _statement(
            _event("replace_ledd", "x", with_sha=False), "Loven trer i kraft 1. oktober 2015."
        )
        is None
    )
    undated = _statement(
        _event("replace_ledd", "x", status="pending"), "Loven trer i kraft 1. oktober 2015."
    )
    assert undated is not None and (undated.when, undated.problem) == (None, INSTRUMENT_UNDATED)
    assert _member_sha(_event("replace_ledd", "x", with_sha=False)) is None


def test_instrument_statements_only_for_the_act() -> None:
    mine = _event("replace_ledd", "Loven trer i kraft 1. oktober 2015.")
    other = mine.model_copy(
        update={"operation": mine.operation.model_copy(update={"act": "lov/1902-05-22-10"})}
    )
    silent = _event("replace_ledd", "Første ledd gjelder tilsvarende.")
    found = instrument_statements(ACT, [other, silent, mine])
    assert [s.when for s in found] == [date(2015, 10, 1)]


def test_ordered_by_publication_then_member() -> None:
    early = _statement_of(date(2015, 10, 1)).model_copy(
        update={"published_at": datetime(2010, 1, 1), "proof": PROOF_B}
    )
    late = _statement_of(date(2015, 10, 1))
    unstamped = _statement_of(date(2015, 10, 1)).model_copy(update={"published_at": None})
    assert [s.proof.member for s in _ordered([], [late, early, unstamped])] == [  # type: ignore[misc]
        "lti/2015/a.xml", "lti/2008/b.xml", "lti/2015/a.xml",
    ]  # fmt: skip


# -------------------------------------------------------------- the state of a unit


def _op(kind: str, **address: object) -> Operation:
    new = (
        Provision(
            key="§1",
            label="§ 1",
            heading="",
            ledd=(Ledd(xml="a", text="a"),) * address.pop("count", 0),
        )
        if kind == "replace_section"
        else None
    )  # type: ignore[operator]
    return Operation(
        id="x",
        kind=kind,
        act=ACT,
        address=Address(**address),
        instruction="",
        part="I",
        new_provision=new,
    )  # type: ignore[arg-type]


def test_addresses_flagged() -> None:
    assert addresses_flagged(_op("replace_ledd", section="§1", ledd=4), (4,), 4) is True
    assert (
        addresses_flagged(_op("replace_ledd", section="§1", ledd=4), (4,), 3) is False
    )  # not there yet
    assert addresses_flagged(_op("replace_ledd", section="§1", ledd=3, ledd_end=5), (4,), 5) is True
    assert addresses_flagged(_op("replace_ledd", section="§1", ledd=5), (4,), 5) is False
    assert addresses_flagged(_op("replace_section", section="§1"), (4,), 5) is False
    assert addresses_flagged(_op("replace_ledd", section="§1", ledd=4), (), 5) is False


def test_restructures() -> None:
    assert (
        restructures(_op("insert_ledd", section="§1", ledd=4), (4,), 3) is False
    )  # creates the flagged ledd
    assert restructures(_op("insert_ledd", section="§1", ledd=4), (4,), 4) is True
    assert restructures(_op("insert_ledd", section="§1", ledd=5), (4,), 4) is False
    assert restructures(_op("repeal_ledd", section="§1", ledd=2), (4,), 4) is True
    assert restructures(_op("move_ledd", section="§1", ledd=1), (4,), 4) is True
    assert restructures(_op("insert_ledd", section="§1"), (4,), 4) is True  # no number at all
    assert restructures(_op("replace_section", section="§1", count=4), (4,), 4) is False
    assert restructures(_op("replace_section", section="§1", count=5), (4,), 4) is True
    assert restructures(_op("replace_ledd", section="§1", ledd=1), (4,), 4) is False
    assert restructures(_op("replace_punktum", section="§1", ledd=4, punktum=1), (4,), 4) is False


def test_unit_state_values() -> None:
    commencement = Commencement(
        default=_timing("dated", date(2023, 1, 1)),
        units=(
            UnitTiming(
                unit=Unit(section="§15-7", ledd=4), timing=_timing("dated", date(2023, 7, 1))
            ),
            UnitTiming(unit=Unit(section="§15-7", ledd=6), timing=_timing("pending")),
            UnitTiming(
                unit=Unit(section="§15-7", ledd=5), timing=Timing(status="unresolved", reason="x")
            ),
            UnitTiming(
                unit=Unit(section="§3-2", punktum=1), timing=_timing("dated", date(2027, 1, 1))
            ),
            UnitTiming(
                unit=Unit(section="§9-1"),
                timing=Timing(status="unresolved", reason="conflicting commencement decisions"),
            ),
            UnitTiming(
                unit=Unit(chapter="kap2"), part="I", timing=_timing("dated", date(2024, 1, 1))
            ),
        ),
    )
    at = date(2023, 2, 1)
    plain = unit_state(commencement, "§1-1", (None, ("del1",)), at)
    assert (plain.in_force, plain.effective, plain.flagged, plain.problems) == (
        "in_force",
        date(2023, 1, 1),
        (),
        (),
    )
    flagged = unit_state(commencement, "§15-7", ("kap15", ("del15",)), at)
    assert (flagged.in_force, flagged.flagged) == ("in_force", (4, 6))
    assert flagged.problems == ("ledd 5 commencement x",)
    assert unit_state(commencement, "§15-7", ("kap15", ()), date(2023, 7, 1)).flagged == (6,)
    finer = unit_state(commencement, "§3-2", ("kap3", ()), at)
    assert (finer.in_force, finer.problems) == ("unresolved", (f"commencement {BELOW_LEDD}",))
    broken = unit_state(commencement, "§9-1", ("kap9", ()), at)
    assert (broken.in_force, broken.problems) == (
        "unresolved",
        ("commencement conflicting commencement decisions",),
    )
    chaptered = unit_state(commencement, "§2-1", ("kap2", ("del1",)), at)
    assert (chaptered.in_force, chaptered.effective) == ("not_yet_in_force", date(2024, 1, 1))
    by_label = unit_state(commencement, "§2-1", ("kap2", ("", "I")), at)
    assert by_label.in_force == "not_yet_in_force"  # the inserting part's own label reaches it
    unreached = unit_state(commencement, "§2-1", ("kap2", ("del2",)), at)
    assert (unreached.in_force, unreached.effective) == ("in_force", date(2023, 1, 1))
    other_chapter = unit_state(commencement, "§3-1", ("kap3", ("del1",)), at)
    assert other_chapter.in_force == "in_force"
    assert commencement.finer_than_ledd("§3-2") and not commencement.finer_than_ledd("§15-7")
    assert set(commencement.ledd_timings("§15-7")) == {4, 5, 6}


# ------------------------------------------------------------------ the fold itself


def test_excepted_unit_keeps_the_header_state_with_the_exception_as_proof() -> None:
    act = _act()
    fold = _Fold(act, {})
    kept = fold._excepted(
        _decision(
            "forskrift/2022-10-14-1739",
            "Delt ikraftsetting av lov 18. juni 2021 nr. 97",
            "2023-01-01",
            "x",
            "2022-10-14 14:20",
        ),
        "quote",
    )
    assert kept.status == "pending" and kept.known_from == date(2021, 6, 18)
    assert [p.refid for p in kept.proofs] == [ACT, "forskrift/2022-10-14-1739"]
    assert kept.proofs[-1].quote_sha256 == __import__("hashlib").sha256(b"quote").hexdigest()
    dated_header = _Fold(_act(date_in_force="2021-07-01"), {})
    refused = dated_header._excepted(
        _decision(
            "forskrift/2022-10-14-1739",
            "Delt ikraftsetting av lov 18. juni 2021 nr. 97",
            "2023-01-01",
            "x",
            "2022-10-14 14:20",
        ),
        "quote",
    )
    assert (refused.status, refused.reason) == (
        "unresolved",
        "excepted from the act's commencement",
    )


def test_fold_collects_chapters_and_sections_from_act_and_map() -> None:
    fold = _Fold(_act(), {"§9A-1": "kap9a"})
    assert fold.chapters == {"kap2", "kap15", "kap9a"}
    assert fold.sections >= {"§2-1", "§15-7", "§9A-1"}
    assert fold.chapter_of["§15-7"] == "kap15" and fold.chapter_of["§9A-1"] == "kap9a"


def test_full_conditional_and_unreadable_decisions_fold_into_the_default() -> None:
    act = _act()
    full = _decision(
        "forskrift/2022-10-14-1",
        "Ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern",
        "2023-01-01",
        "Loven trer i kraft 1. januar 2023.",
        "2022-10-14 10:00",
    )
    dated = baseline_commencement(act, (full,), [])
    assert (dated.default.status, dated.default.effective, dated.units) == (
        "dated",
        date(2023, 1, 1),
        (),
    )
    conditional = _decision(
        "forskrift/2022-10-14-2",
        "Ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern",
        "Når EØS-avtalen trer i kraft",
        "Loven trer i kraft når EØS-avtalen trer i kraft.",
        "2022-10-14 10:00",
    )
    waiting = baseline_commencement(act, (conditional,), [])
    assert waiting.default.status == "pending" and waiting.default.reason
    unreadable = _decision(
        "forskrift/2022-10-14-3",
        "Ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern",
        "om kort tid",
        "Loven trer i kraft om kort tid.",
        "2022-10-14 10:00",
    )
    refused = baseline_commencement(act, (unreadable,), [])
    assert refused.default.status == "unresolved" and refused.default.reason


def test_a_partial_read_without_a_single_scope_names_nothing_of_the_act() -> None:
    """``_Decision`` marks a scopeless partial read unreadable before the fold sees it; the fold
    still answers for the shape on its own: no scope names anything, so every unit is unsettled
    with the decision as proof."""
    fold = _Fold(_act(), {})
    decision = _decision(
        "forskrift/2022-10-14-1741",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97 om barnevern (barnevernsloven)",
        "2023-01-01",
        "x",
        "2022-10-14 14:40",
    )
    read = SimpleNamespace(scopes=(), unreadable=None, document=decision)
    fold._partial(read)  # type: ignore[arg-type]
    assert (fold.default.status, fold.default.reason) == ("unresolved", NAMES_NOTHING)
    assert fold.default.proofs[-1].refid == "forskrift/2022-10-14-1741"
    assert all(t.reason == NAMES_NOTHING for t in fold.units.values())
