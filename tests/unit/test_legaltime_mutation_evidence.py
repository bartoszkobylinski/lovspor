"""Behavioral evidence for the supplied commencement mutation artifact."""

from datetime import date, datetime
from hashlib import sha256

import pytest

from lovspor.legaltime import baseline as b
from lovspor.legaltime import commencement as c
from lovspor.legaltime import instructions as ins
from lovspor.legaltime import replay as r
from lovspor.legaltime import run, scopes, verify
from lovspor.legaltime.document import Ledd, Provision
from lovspor.legaltime.units import Unit
from tests.unit.test_legaltime_baseline import ACT, STAGED, _act, _decision, _evidence
from tests.unit.test_legaltime_mutants import _event, _op
from tests.unit.test_legaltime_remediation import xml_doc

DAY = date(2023, 1, 1)
LATER = date(2024, 1, 1)
PROOF = c.Proof(refid="law", member="member", sha256="a" * 64, quote_sha256="b" * 64)
OTHER = PROOF.model_copy(update={"member": "other"})


def dated(day=DAY, **kw):
    return c.Timing(status="dated", effective=day, **kw)


def statement(day=DAY, **kw):
    return b._Statement(
        when=day, known_from=DAY, published_at=datetime(2023, 1, 1), proof=PROOF, **kw
    )


def test_excepted_dated_header_retains_publication_and_both_proofs():
    act = _act(date_in_force="2021-07-01")
    result = b._Fold(act, {})._excepted(STAGED, "Unntatt § 2.")
    assert result == c.Timing(
        status="unresolved",
        reason="excepted from the act's commencement",
        known_from=date(2021, 6, 18),
        proofs=(
            *c.header_timing(act).proofs,
            c.Proof(
                refid=STAGED.header.refid,
                member=STAGED.member,
                sha256=STAGED.sha256,
                quote_sha256=sha256("Unntatt § 2.".encode()).hexdigest(),
            ),
        ),
    )


def test_statement_fold_preserves_evidence_and_clears_conflicting_date():
    current = dated(known_from=LATER, retroactive=True, proofs=(OTHER,))
    unresolved = c.Timing(status="unresolved", reason="earlier", proofs=(OTHER,))
    assert b._fold_statement(unresolved, statement()) == unresolved
    same = b._fold_statement(current, statement())
    assert same == current.model_copy(update={"proofs": (OTHER, PROOF)})
    conflict = b._fold_statement(current, statement(LATER))
    assert conflict == c.Timing(
        status="unresolved", reason=c.CONFLICT, known_from=LATER, proofs=(OTHER, PROOF)
    )
    for problem, day, reason in [
        ("unreadable", DAY, "unreadable"),
        (None, None, "commencement statement without a readable date"),
    ]:
        result = b._fold_statement(current, statement(day, problem=problem))
        assert result == c.Timing(
            status="unresolved", reason=reason, known_from=LATER, proofs=(OTHER, PROOF)
        )


@pytest.mark.parametrize(
    "when,known,retroactive",
    [(DAY, DAY, False), (DAY, LATER, True), (LATER, DAY, False), (DAY, None, False)],
)
def test_pending_statement_uses_publication_and_retains_nonheader_proofs(when, known, retroactive):
    current = c.Timing(status="pending", proofs=(OTHER, OTHER))
    result = b._fold_statement(current, statement(when).model_copy(update={"known_from": known}))
    assert result == dated(when, known_from=known, retroactive=retroactive, proofs=(OTHER, PROOF))


@pytest.mark.parametrize(
    "roman,number",
    [("I", 1), ("II", 2), ("IV", 4), ("IX", 9), ("XL", 40), ("XC", 90), ("XIV", 14), ("XLIX", 49)],
)
def test_baseline_roman_part_number(roman, number):
    assert b._roman(roman) == number


@pytest.mark.parametrize(
    "unit",
    [
        Unit(section="§1", ledd=1, punktum=1),
        Unit(section="§1", ledd=1, bokstav="a"),
        Unit(section="§1", heading=True),
    ],
)
def test_finer_units_are_not_whole_ledd(unit):
    assert not b._only_ledd(unit)
    comm = b.Commencement(default=dated(), units=(b.UnitTiming(unit=unit, timing=dated()),))
    assert comm.finer_than_ledd("§1")
    assert not comm.finer_than_ledd("§2")
    got = b.unit_state(comm, "§1", (None, ()), DAY)
    assert got.in_force == "unresolved"
    assert got.problems == (f"commencement {b.BELOW_LEDD}",)


def test_unit_state_reports_ledd_problem_and_chapter_part_gate():
    comm = b.Commencement(
        default=dated(),
        units=(
            b.UnitTiming(unit=Unit(chapter="kap2"), part="I", timing=dated(LATER)),
            b.UnitTiming(
                unit=Unit(section="§2-1", ledd=1),
                timing=c.Timing(status="unresolved", reason="conflict"),
            ),
        ),
    )
    got = b.unit_state(comm, "§2-1", ("kap2", ("del1",)), DAY)
    assert got.in_force == "not_yet_in_force"
    assert got.effective == LATER
    assert got.problems == ("ledd 1 commencement conflict",)
    unit = b.UnitState(in_force="unresolved", effective=None, flagged=(), problems=("one", "two"))
    assert b.state_gate(unit) == "one; two"


@pytest.mark.parametrize("kind", ["insert_ledd", "repeal_ledd", "move_ledd"])
@pytest.mark.parametrize("start,expected", [(None, True), (2, True), (3, True), (4, False)])
def test_restructuring_boundary(kind, start, expected):
    assert b.restructures(_op(kind, ins.Address(section="§1", ledd=start)), (3, 9), 3) is expected
    assert not b.restructures(_op("replace_ledd", ins.Address(section="§1", ledd=start)), (3,), 3)


def test_dated_without_effective_day_is_unresolved():
    assert b._in_force(c.Timing(status="dated"), DAY) == ("unresolved", None)


def test_provision_report_preserves_nondefault_commencement():
    comm = b.Commencement(
        default=dated(),
        units=(
            b.UnitTiming(unit=Unit(section="§2-1"), timing=dated(LATER)),
            b.UnitTiming(unit=Unit(section="§15-7", ledd=2), timing=dated(LATER)),
        ),
    )
    state = r.replay(_act(), comm, [], DAY)
    records = run._provision_record(state)
    assert records == [
        {
            "key": "§2-1",
            "status": "replayed",
            "repealed": False,
            "ledd": 1,
            "applied": [],
            "problems": [],
            "in_force": "not_yet_in_force",
        },
        {
            "key": "§15-7",
            "status": "replayed",
            "repealed": False,
            "ledd": 3,
            "applied": [],
            "problems": [],
            "ledd_not_in_force": [2],
        },
    ]
    assert state.not_in_force == ("§2-1",)
    pending = comm.model_copy(
        update={
            "units": (b.UnitTiming(unit=Unit(section="§2-1"), timing=c.Timing(status="pending")),)
        }
    )
    assert r.replay(_act(), pending, [], DAY).pending_commencement == ("§2-1",)


def test_commencement_report_is_json_native_and_has_stable_keys():
    comm = b.Commencement(
        default=dated(),
        units=(b.UnitTiming(unit=Unit(section="§1"), part="I", timing=dated(LATER)),),
    )
    assert run._commencement_record(comm) == {
        "default": dated().model_dump(mode="json"),
        "units": [{"unit": "§1", "part": "I", "timing": dated(LATER).model_dump(mode="json")}],
    }
    report = run.build_report(_evidence(_act(), (), (STAGED,)), [DAY], None, [])
    assert report["commencement"] == run._commencement_record(
        b.baseline_commencement(_act(), (STAGED,), [])
    )


def test_unresolved_unit_freezes_as_unresolved_without_text_problems():
    source = _act().provisions[0]
    unit = b.UnitState(in_force="unresolved", effective=None, flagged=(), problems=())
    assert r._Provision(source, unit).freeze(source.key).status == "unresolved"


def test_working_without_date_has_no_commencement_gate():
    working = r._Working(_act(), b.Commencement(default=c.Timing(status="pending")))
    assert all(p.freeze(k).in_force == "in_force" for k, p in working.map.items())


def test_scope_chapter_narrowing_uses_placement():
    scope = scopes.Scope(part="I", units=(Unit(chapter="kap2"),), when=DAY, quote="q")
    op = _op("repeal_section", ins.Address(section="§2-1"))
    assert scope.reach(op, {"§2-1": "kap2"}) == "covers"
    assert scope.reach(op, {"§2-1": "kap3"}) == "disjoint"


@pytest.mark.parametrize("tail", ["nonsense § 2", "lov ukjent", "§ 1 nonsense"])
def test_scope_item_rejects_unreadable_tail(tail):
    assert scopes._item_units(tail) is None


def payload_event(
    kind="insert_ledd", text="Loven trer i kraft straks.", published=datetime(2023, 1, 1)
):
    op = _op(kind, ins.Address(section="§1", ledd=1), (text, "Second."))
    timing = dated(proofs=(PROOF,))
    return _event(op, timing).model_copy(
        update={"law": "law", "member": "member", "published_at": published}
    )


@pytest.mark.parametrize("kind", ["insert_ledd", "replace_ledd", "replace_section"])
def test_instrument_payload_first_sentence(kind):
    event = payload_event(kind, "Loven trer i kraft 1. januar 2023. Second.")
    if kind == "replace_section":
        provision = Provision(key="§1", label="§ 1", heading="", ledd=event.operation.ledd)
        event = event.model_copy(
            update={
                "operation": event.operation.model_copy(
                    update={"new_provision": provision, "ledd": ()}
                )
            }
        )
    assert b._payload_sentence(event) == "Loven trer i kraft 1. januar 2023."
    empty = event.model_copy(
        update={"operation": event.operation.model_copy(update={"ledd": (), "new_provision": None})}
    )
    assert b._payload_sentence(empty) is None
    unrelated = event.model_copy(
        update={"operation": event.operation.model_copy(update={"kind": "insert_section"})}
    )
    assert b._payload_sentence(unrelated) is None


def test_instrument_member_proof_requires_both_identity_fields():
    event = payload_event()
    decoys = (
        PROOF.model_copy(update={"member": "elsewhere"}),
        PROOF.model_copy(update={"refid": "other"}),
    )
    assert b._member_sha(event.model_copy(update={"timing": dated(proofs=decoys)})) is None
    assert (
        b._member_sha(event.model_copy(update={"timing": dated(proofs=(*decoys, PROOF))}))
        == "a" * 64
    )


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("Loven trer i kraft straks.", DAY),
        ("Loven trer i kraft 1. januar 2024.", LATER),
        ("Loven trer i kraft straks 1. januar 2024.", LATER),
        ("Loven trer i kraft en ukjent dag.", None),
    ],
)
@pytest.mark.parametrize("published", [None, datetime(2023, 1, 1)])
def test_statement_date_and_publication_are_independent(sentence, expected, published):
    got = b._statement(payload_event(published=published), sentence)
    assert got is not None
    assert got.when == expected
    assert got.known_from == (DAY if published else None)
    assert got.published_at == published
    assert got.problem == (None if expected else "commencement statement without a readable date")
    assert got.proof == PROOF.model_copy(
        update={"quote_sha256": sha256(sentence.encode()).hexdigest()}
    )


def test_foreign_instrument_does_not_hide_later_target_statement():
    event = payload_event()
    foreign = event.model_copy(
        update={"operation": event.operation.model_copy(update={"act": "other"})}
    )
    assert b.instrument_statements(event.operation.act, [foreign, event]) == [
        b._statement(event, "Loven trer i kraft straks.")
    ]


def test_decisions_and_statements_are_ordered_by_publication_before_member():
    early = statement().model_copy(update={"published_at": datetime(2020, 1, 1), "proof": OTHER})
    late = statement().model_copy(update={"published_at": datetime(2025, 1, 1)})
    absent = statement().model_copy(update={"published_at": None})
    assert b._ordered([], [late, absent, early]) == [absent, early, late]
    read = c._Decision(STAGED, _act(), True)
    older = c._Decision(
        STAGED.model_copy(
            update={
                "header": STAGED.header.model_copy(update={"published_at": datetime(2020, 1, 1)})
            }
        ),
        _act(),
        True,
    )
    assert b._ordered([read, older], []) == [older, read]


@pytest.mark.parametrize(
    "scope",
    [
        scopes.Scope(whole=True, when=DAY, quote="q"),
        scopes.Scope(remainder=True, when=DAY, quote="q"),
        scopes.Scope(own_sections=frozenset({"§2-1"}), when=DAY, quote="q"),
    ],
)
def test_partial_scope_names_act_and_dates_own_sections(scope):
    fold = b._Fold(_act(), {})
    assert fold._names_act(scope)
    fold._scope(STAGED, scope)
    result = fold.result()
    if scope.own_sections:
        assert result.units[0].unit == Unit(section="§2-1")
        assert result.units[0].timing.effective == DAY
    else:
        assert result.default.effective == DAY


def test_reconciliation_removes_retroactive_date_on_conflict():
    fold = b._Fold(_act(), {})
    fold.default = dated(LATER, proofs=(OTHER,))
    key = (Unit(section="§2-1"), None)
    fold.units[key] = dated(known_from=LATER, retroactive=True, proofs=(PROOF,))
    got = fold.result().units[0].timing
    assert got == c.Timing(
        status="unresolved", reason=c.CONFLICT, known_from=LATER, proofs=(PROOF, OTHER)
    )


def test_unreadable_decision_unsettles_existing_units_with_its_reason():
    fold = b._Fold(_act(), {})
    fold.units[(Unit(section="§2-1"), None)] = dated()
    doc = _decision(
        "forskrift/2025-01-01-1",
        "Ikraftsetting av lov 18. juni 2021 nr. 97",
        "",
        "Loven trer i kraft en ukjent dag.",
        "2025-01-01 12:00",
    )
    read = c._Decision(doc, _act(), True)
    assert read.unreadable is not None
    fold.decision(read)
    result = fold.result()
    assert result.default.status == "unresolved"
    assert result.default.reason == read.unreadable
    assert result.units[0].timing.reason == read.unreadable
    assert result.units[0].timing.proofs[-1].member == doc.member


def test_inline_substitution_has_one_document_prefix_and_requires_target_act():
    sentence = "Ordet «gammel» endres til «ny» i § 1."
    doc = xml_doc(
        '<section class="section"><h2>I</h2><article class="defaultP">'
        "I lov 18. juni 2021 nr. 97 gjøres følgende endringer:</article>"
        '<article class="defaultP">' + sentence + "</article></section>"
    )
    ops = ins.interpret(doc).operations
    assert len(ops) == 1
    assert ops[0].kind == "substitute_term"
    assert ops[0].id.count("#") == 1
    assert ops[0].act == ACT
    orphan = xml_doc(
        '<section class="section"><h2>I</h2><article class="defaultP">'
        + sentence
        + "</article></section>"
    )
    assert ins.interpret(orphan).operations == ()


@pytest.mark.parametrize("pairs", [((1, 3), (2, 4)), ((1, 2), (3, 4))])
def test_renumbering_is_not_insertion_unless_contiguous_and_up_one(pairs):
    op = _op("replace_ledd", ins.Address(section="§1", ledd=1), ("New.",))
    assert ins._as_insertion(op, ins.Shift(section="§1", unit="ledd", pairs=pairs)) is None


@pytest.mark.parametrize("part", ["del1", None])
def test_existing_provision_keeps_chapter_and_part_commencement(part):
    source = _act().provisions[0].model_copy(update={"part": part})
    doc = _act().model_copy(update={"provisions": (source,)})
    comm = b.Commencement(
        default=dated(),
        units=(
            b.UnitTiming(
                unit=Unit(chapter="kap2"), part="I" if part else None, timing=dated(LATER)
            ),
        ),
    )
    result = r.replay(doc, comm, [], DAY)
    assert result.provisions[0].in_force == "not_yet_in_force"
    assert result.not_in_force == ("§2-1",)


@pytest.mark.parametrize("basis", ["section", "chapter", "own_part", "amending_part"])
def test_inserted_provision_uses_its_own_commencement(basis):
    unit = Unit(section="§3") if basis == "section" else Unit(chapter="kap3")
    part = {"own_part": "II", "amending_part": "I"}.get(basis)
    comm = b.Commencement(
        default=dated(), units=(b.UnitTiming(unit=unit, part=part, timing=dated(LATER)),)
    )
    new = Provision(
        key="§3",
        label="§ 3",
        heading="",
        ledd=(Ledd(text="New.", xml="New."),),
        chapter="kap3",
        part="del2",
    )
    op = _op("insert_section", ins.Address(section="§3"), new_provision=new).model_copy(
        update={"act": ACT, "part": "i"}
    )
    working = r._Working(_act(), comm, DAY)
    assert r._unit_gate(op, working) == "withheld"
    assert working.apply_insert_section(op) is None
    frozen = working.map["§3"].freeze("§3")
    assert frozen.in_force == "not_yet_in_force"
    assert frozen.effective == LATER


def test_verification_counts_accumulate_after_missing_and_unproven_sections():
    source = _act().provisions[1]
    sections = [verify.SectionVerdict(key="missing", verdict="only_in_oracle")]
    replayed, oracle = {}, {}
    for key, verdict, flags in [
        ("§1", "text_differs", ()),
        ("§2", "text_differs", ()),
        ("§3", "exact_raw", (2,)),
        ("§4", "exact_raw", (2,)),
    ]:
        oracle[key] = source.model_copy(update={"key": key})
        replayed[key] = r._Provision(
            oracle[key], b.UnitState(in_force="in_force", effective=DAY, flagged=flags, problems=())
        ).freeze(key)
        sections.append(verify.SectionVerdict(key=key, verdict=verdict))
    assert verify._ledd_counts(sections, replayed, oracle) == {
        "oracle_ledd": 12,
        "ledd_text_differs": 6,
        "ledd_proven": 4,
        "ledd_not_in_force": 2,
    }


def test_conditional_full_decision_retains_pending_evidence():
    doc = _decision(
        "forskrift/2022-01-01-1",
        "Ikraftsetting av lov 18. juni 2021 nr. 97",
        "Når personvernforordningen trer i kraft",
        "",
        "2022-01-01 12:00",
    )
    read = c._Decision(doc, _act(), True)
    assert read.conditional
    fold = b._Fold(_act(), {})
    before = fold.default
    fold.decision(read)
    assert fold.default.status == "pending"
    assert fold.default.reason == c.CONDITIONAL
    assert fold.default.proofs[:-1] == before.proofs
    assert fold.default.proofs[-1].member == doc.member


def test_unreadable_partial_scope_stops_later_decisions_and_keeps_unit_proofs():
    unreadable = _decision(
        "forskrift/2024-01-01-1",
        "Delt ikraftsetting av lov 18. juni 2021 nr. 97",
        "2024-01-01",
        "Loven trer i kraft med unntak av ukjente bestemmelser.",
        "2024-01-01 12:00",
    )
    later = _decision(
        "forskrift/2025-01-01-1",
        "Ikraftsetting av lov 18. juni 2021 nr. 97",
        "2025-01-01",
        "Loven trer i kraft 1. januar 2025.",
        "2025-01-01 12:00",
    )
    read = c._Decision(unreadable, _act(), True)
    assert read.partial and not read.scopes
    assert read.unreadable == b.OUTSIDE
    result = b.baseline_commencement(_act(), (STAGED, unreadable, later), [])
    assert result.default.status == "unresolved"
    assert result.default.reason == b.OUTSIDE
    assert result.default.proofs[-1].member == unreadable.member
    assert all(e.timing.reason == b.OUTSIDE for e in result.units)
    assert all(e.timing.proofs[-1].member == unreadable.member for e in result.units)


def test_statement_citation_is_removed_without_masking_adjacent_date():
    sentence = "Loven trer i kraft lov 1. januar 20201. juli 2022."
    got = b._statement(payload_event(), sentence)
    assert got is not None and got.when == date(2022, 7, 1)


def test_inline_scope_citation_keeps_both_section_units():
    got = scopes._item_units("lov 18. juni 2021 nr. 97 § 2 og § 3")
    assert got == scopes._Item(act=ACT, units=(Unit(section="§2"), Unit(section="§3")))
    assert scopes._item_units("lov ukjent § 1 og lov 18. juni 2021 nr. 97 § 2") is None


def test_absent_part_does_not_match_part_forty():
    comm = b.Commencement(
        default=dated(),
        units=(b.UnitTiming(unit=Unit(chapter="kap2"), part="XXXX", timing=dated(LATER)),),
    )
    doc = _act()
    result = r.replay(doc, comm, [], DAY)
    assert result.provisions[0].in_force == "in_force"
    new = Provision(key="§2-2", label="§ 2-2", heading="", ledd=(), chapter="kap2")
    op = _op("insert_section", ins.Address(section="§2-2"), new_provision=new)
    working = r._Working(doc, comm, DAY)
    assert r._unit_gate(op, working) is None
    assert working.apply_insert_section(op) is None
    assert working.map["§2-2"].freeze("§2-2").in_force == "in_force"


def test_chapter_unit_does_not_borrow_placement_from_unrelated_section_key():
    fold = b._Fold(_act(), {"XXXX": "kap2"})
    fold.units[(Unit(chapter="kap2"), None)] = dated(LATER)
    fold.units[(Unit(chapter="kap3"), None)] = dated()
    result = {entry.unit: entry.timing for entry in fold.result().units}
    assert result[Unit(chapter="kap3")] == dated()
