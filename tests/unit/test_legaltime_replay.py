"""Replay and verification on the synthetic act: every operation class, every date class."""

from datetime import date, datetime
from pathlib import Path

import pytest

from lovspor.legaltime.commencement import Timing, resolve_timings
from lovspor.legaltime.document import Document, Ledd, parse_document
from lovspor.legaltime.instructions import Address, Operation, OpKind, interpret
from lovspor.legaltime.replay import Event, ReplayState, _locations, replay
from lovspor.legaltime.verify import verify

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
ENACTED = Timing(status="dated", effective=date(2021, 1, 1))


def load(name: str) -> Document:
    return parse_document((FIXTURES / name).read_bytes(), f"lti/x/{name}")


def events_for(amending: Document, decisions: tuple[Document, ...] = ()) -> list[Event]:
    result = interpret(amending)
    timings = resolve_timings(amending, result.clauses, result.operations, decisions)
    return [
        Event(
            sequence=i,
            law=amending.header.refid,
            member=amending.member,
            operation=op,
            timing=timings[op.id],
            published_at=amending.header.published_at,
        )
        for i, op in enumerate(result.operations)
        if op.act == "lov/2019-06-14-99"
    ]


def state_at(at: date, decisions: tuple[Document, ...] = ()) -> ReplayState:
    return replay(load("enacting.xml"), ENACTED, events_for(load("amending.xml"), decisions), at)


def texts(state: ReplayState, key: str) -> list[str]:
    return [ledd.text for p in state.provisions if p.key == key for ledd in p.ledd]


ACT = "lov/2019-06-14-99"
OMISSION = " \u2013 \u2013 \u2013"
DATED = Timing(status="dated", effective=date(2022, 1, 1))
ENACTED_TEXT = [
    "Loven gjelder prøving av fylkesmannen. Den gjelder også retting.",
    "Annet ledd første punktum. Annet ledd annet punktum.",
]


def _op(
    kind: OpKind, address: Address, ledd: tuple[str, ...] = (), **extra: str | None
) -> Operation:
    return Operation(
        id=extra.pop("id", None) or "x#I:0.0", kind=kind, act=ACT, address=address,
        instruction=extra.pop("instruction", None) or "i", part="I",
        ledd=tuple(Ledd(xml=t, text=t) for t in ledd), **extra,
    )  # fmt: skip


def _event(
    op: Operation, timing: Timing = DATED, sequence: int = 0, published: datetime | None = None
) -> Event:
    return Event(
        sequence=sequence, law="x", member="m", operation=op, timing=timing, published_at=published
    )


def _run(events: list[Event], at: date = date(2023, 1, 1)) -> ReplayState:
    return replay(load("enacting.xml"), ENACTED, events, at)


def test_before_the_act_commenced_there_is_no_text() -> None:
    state = state_at(date(2020, 12, 31))
    assert state.status == "not_yet_in_force"
    assert state.provisions == ()


def test_unproven_baseline_commencement_is_an_explicit_outcome() -> None:
    state = replay(load("enacting.xml"), Timing(status="pending"), [], date(2025, 1, 1))
    assert state.status == "unresolved_baseline"
    assert state.provisions == ()


def test_state_at_commencement_carries_the_amendments_already_in_force() -> None:
    state = state_at(date(2021, 1, 1))
    assert state.status == "replayed"
    assert state.applied == (
        "lov/2020-12-18-999#1:I:1.0",
        "lov/2020-12-18-999#1:I:3.0",
        "lov/2020-12-18-999#1:I:7.0",
        "lov/2020-12-18-999#1:I:8.0",
    )
    assert texts(state, "§1") == [
        "Loven gjelder prøving av fylkesmannen. Den gjelder også retting.",
        "Annet ledd første punktum. Annet ledd nytt annet punktum.",
        "Tredje ledd er nytt.",
    ]
    assert [p.key for p in state.provisions] == ["§1", "§2", "§3", "§4"]
    repealed = next(p for p in state.provisions if p.key == "§4")
    assert repealed.repealed and repealed.heading == "(Opphevet)" and repealed.ledd == ()
    assert state.pending == ("lov/2020-12-18-999#1:I:5.0",)
    assert state.future == ()
    assert state.unresolved == ()


def test_retroactive_amendment_applies_at_its_legal_date_not_its_publication() -> None:
    state = replay(
        load("enacting.xml"), ENACTED, events_for(load("amending.xml")), date(2020, 12, 18)
    )
    assert state.status == "not_yet_in_force"
    state = state_at(date(2021, 1, 1))
    assert "lov/2020-12-18-999#1:I:1.0" in state.applied
    assert state.provisions[0].applied == (
        "lov/2020-12-18-999#1:I:1.0",
        "lov/2020-12-18-999#1:I:3.0",
    )


def test_partial_decision_dates_the_insertion_and_the_future_is_listed() -> None:
    decisions = (load("resolution-partial.xml"),)
    before = state_at(date(2021, 12, 31), decisions)
    assert before.future == ("lov/2020-12-18-999#1:I:5.0",)
    assert [p.key for p in before.provisions] == ["§1", "§2", "§3", "§4"]
    after = state_at(date(2022, 1, 1), decisions)
    assert [p.key for p in after.provisions] == ["§1", "§2", "§2a", "§3", "§4"]
    assert after.future == () and after.pending == ()
    assert texts(after, "§2a") == ["Innskutt første ledd."]


def test_final_state_matches_the_oracle_up_to_editorial_markup() -> None:
    sub = _op(
        "substitute_term", Address(), id="lov/2021-05-07-34#I:5", instruction="§ 1",
        term_old="fylkesmannen", term_new="statsforvalteren", locations="§ 1",
    )  # fmt: skip
    rename = _event(
        sub, Timing(status="dated", effective=date(2021, 6, 1)), 99, datetime(2021, 5, 7)
    )
    decisions = (load("resolution-partial.xml"),)
    events = [*events_for(load("amending.xml"), decisions), rename]
    state = _run(events, date(2026, 11, 6))
    result = verify(state, load("consolidated.xml"))
    assert result.counts == {"exact_normalized": 3, "exact_raw": 2}
    assert {s.key: s.verdict for s in result.sections} == {
        "§1": "exact_normalized",
        "§2": "exact_normalized",
        "§2a": "exact_raw",
        "§3": "exact_normalized",
        "§4": "exact_raw",
    }
    assert result.notes_without_replay_event == ()
    assert result.replay_events_without_note == ()
    assert result.oracle_sha256 == load("consolidated.xml").sha256
    assert {s.key: s.oracle_notes for s in result.sections}["§2a"] == 1


def test_unsupported_operation_marks_its_target_unresolved_not_guessed() -> None:
    op = _op(
        "unsupported", Address(section="§2"), instruction="§ 2 skal lyde slik Kongen bestemmer"
    )
    state = _run([_event(op)])
    target = next(p for p in state.provisions if p.key == "§2")
    assert target.status == "unresolved"
    assert target.problems == (
        "x#I:0.0: unsupported instruction: § 2 skal lyde slik Kongen bestemmer",
    )
    assert state.unresolved == (
        ("x#I:0.0", "unsupported instruction: § 2 skal lyde slik Kongen bestemmer"),
    )
    assert texts(state, "§2") == [
        "Følgende gjelder: bokstav a bokstav b Bokstav b gjelder ikke private."
    ]
    result = verify(state, load("consolidated.xml"))
    assert {s.key: s.verdict for s in result.sections}["§2"] == "unresolved"


def test_unresolved_commencement_marks_targets_and_applies_nothing() -> None:
    op = _op("repeal_section", Address(section="§3"), instruction="§ 3 oppheves.")
    state = _run(
        [_event(op, Timing(status="unresolved", reason="dateInForce outside the grammar"))]
    )
    target = next(p for p in state.provisions if p.key == "§3")
    assert target.status == "unresolved" and not target.repealed
    assert state.unresolved == (("x#I:0.0", "dateInForce outside the grammar"),)


def test_act_level_problem_when_an_unsupported_operation_names_no_section() -> None:
    op = _op("unsupported", Address(), instruction="Lovens tittel skal lyde:")
    state = _run([_event(op)])
    assert state.act_level_problems == (
        "x#I:0.0: unsupported instruction: Lovens tittel skal lyde:",
    )
    assert all(p.status == "replayed" for p in state.provisions)


@pytest.mark.parametrize(
    ("kind", "address", "ledd_texts", "expected"),
    [
        ("repeal_ledd", Address(section="§1", ledd=2), (), ENACTED_TEXT[:1]),
        (
            "repeal_punktum",
            Address(section="§1", ledd=1, punktum=2),
            (),
            ["Loven gjelder prøving av fylkesmannen.", ENACTED_TEXT[1]],
        ),
        (
            "insert_punktum",
            Address(section="§1", ledd=1, punktum=1),
            ("Null.",),
            [f"Null. {ENACTED_TEXT[0]}", ENACTED_TEXT[1]],
        ),
        ("replace_ledd", Address(section="§1", ledd=1, ledd_end=2), ("A.", "B."), ["A.", "B."]),
        ("insert_ledd", Address(section="§1", ledd=1), ("Først.",), ["Først.", *ENACTED_TEXT]),
    ],
)
def test_ledd_and_punktum_operations(
    kind: OpKind, address: Address, ledd_texts: tuple[str, ...], expected: list[str]
) -> None:
    state = _run([_event(_op(kind, address, ledd_texts))])
    assert state.unresolved == ()
    assert texts(state, "§1") == expected


@pytest.mark.parametrize(
    ("kind", "address", "ledd_count", "reason"),
    [
        ("replace_ledd", Address(section="§1", ledd=5), 1, "target ledd range does not exist"),
        ("replace_ledd", Address(section="§1", ledd=1), 2, "2 ledd supplied for 1 addressed"),
        (
            "replace_punktum",
            Address(section="§1", ledd=1, punktum=4),
            1,
            "punktum 4-4: 2 existing, 1 supplied",
        ),
        (
            "replace_punktum",
            Address(section="§2", punktum=1),
            1,
            "punktum operation on a ledd that carries a list",
        ),
        (
            "replace_punktum",
            Address(section="§1", punktum=1),
            1,
            "punktum without ledd on a provision with several ledd",
        ),
        (
            "replace_section",
            Address(section="§9"),
            0,
            "target provision missing or no replacement text",
        ),
        (
            "move_ledd",
            Address(section="§1", ledd=1),
            0,
            "source ledd or destination provision missing",
        ),
        (
            "replace_bokstav",
            Address(section="§2", ledd=1, bokstav="z"),
            1,
            "bokstav z matched 0 list items",
        ),
    ],
)
def test_operations_that_do_not_fit_the_text_fail_closed(
    kind: OpKind, address: Address, ledd_count: int, reason: str
) -> None:
    state = _run([_event(_op(kind, address, ("T.",) * ledd_count))])
    assert state.unresolved == (("x#I:0.0", reason),)
    assert texts(state, "§1") == ENACTED_TEXT


def test_bokstav_replacement_and_move_ledd() -> None:
    bokstav = _op("replace_bokstav", Address(section="§2", ledd=1, bokstav="b"), ("bokstav bravo",))
    move = _op(
        "move_ledd",
        Address(section="§1", ledd=2, destination_ledd=1),
        id="x#I:1.0",
        destination="§3",
    )
    state = _run([_event(bokstav), _event(move, sequence=1)])
    assert state.unresolved == ()
    assert texts(state, "§2") == [
        "Følgende gjelder: bokstav a bokstav bravo Bokstav b gjelder ikke private."
    ]
    assert texts(state, "§1") == ENACTED_TEXT[:1]
    assert texts(state, "§3") == [ENACTED_TEXT[1], "Loven gjelder fra den tid Kongen bestemmer."]


def test_same_day_events_apply_in_publication_then_document_order() -> None:
    later = _op("replace_ledd", Address(section="§1", ledd=1), ("Later.",), id="b#I:0.0")
    earlier = _op("replace_ledd", Address(section="§1", ledd=1), ("Earlier.",), id="a#I:0.0")
    events = [
        _event(later, sequence=0, published=datetime(2021, 12, 20, 12)),
        _event(earlier, sequence=1, published=datetime(2021, 12, 10, 9)),
    ]
    state = _run(events, date(2022, 1, 1))
    assert state.applied == ("a#I:0.0", "b#I:0.0")
    assert texts(state, "§1")[0] == "Later."


def test_replay_is_deterministic() -> None:
    decisions = (load("resolution-partial.xml"),)
    first, second = state_at(date(2024, 1, 1), decisions), state_at(date(2024, 1, 1), decisions)
    assert first == second
    assert first.model_dump() == second.model_dump()


def test_pending_operations_are_recorded_on_their_target_provisions() -> None:
    op = _op("replace_section", Address(section="§2"), instruction="§ 2 skal lyde:")
    state = _run([_event(op, Timing(status="pending"))])
    target = next(p for p in state.provisions if p.key == "§2")
    assert target.status == "replayed"
    assert target.pending == ("x#I:0.0",)
    assert state.pending == ("x#I:0.0",)
    result = verify(state, load("consolidated.xml"))
    assert {s.key: s.pending for s in result.sections}["§2"] == ("x#I:0.0",)


def test_oracle_omission_marker_is_an_editorial_class_not_a_difference() -> None:
    oracle = load("consolidated.xml")
    omitted = oracle.model_copy(
        update={
            "provisions": tuple(
                p.model_copy(
                    update={
                        "ledd": tuple(
                            ledd.model_copy(update={"text": ledd.text + OMISSION})
                            for ledd in p.ledd
                        )
                    }
                )
                if p.key == "§2"
                else p
                for p in oracle.provisions
            )
        }
    )
    state = _run([], date(2023, 1, 1))
    verdicts = {s.key: s.verdict for s in verify(state, omitted).sections}
    assert verdicts["§2"] == "editorial_omission"
    assert verdicts["§4"] == "text_differs"


def test_omitted_tail_spanning_whole_ledd_is_still_editorial() -> None:
    oracle = load("consolidated.xml")
    trimmed = oracle.model_copy(
        update={
            "provisions": tuple(
                p.model_copy(
                    update={
                        "ledd": (p.ledd[0].model_copy(update={"text": p.ledd[0].text + OMISSION}),)
                    }
                )
                if p.key == "§1"
                else p
                for p in oracle.provisions
            )
        }
    )
    state = _run([_event(_op("insert_ledd", Address(section="§1", ledd=3), ("Ekstra.",)))])
    assert texts(state, "§1")[0] != trimmed.provisions[0].ledd[0].text
    sub = _op(
        "substitute_term", Address(), term_old="fylkesmannen", term_new="statsforvalteren",
        locations="§ 1",
    )  # fmt: skip
    state = _run([_event(sub)])
    verdicts = {s.key: s.verdict for s in verify(state, trimmed).sections}
    assert verdicts["§1"] == "editorial_omission"


def test_oracle_annex_keys_are_counted_apart_from_missing_sections() -> None:
    oracle = load("consolidated.xml")
    annex = oracle.provisions[0].model_copy(update={"key": "gdpr/a1"})
    extended = oracle.model_copy(update={"provisions": (*oracle.provisions, annex)})
    result = verify(_run([]), extended)
    assert result.counts["oracle_annex"] == 1
    assert {s.key: s.verdict for s in result.sections}["gdpr/a1"] == "oracle_annex"


def test_verification_partitions_sections_and_reports_both_note_mismatches() -> None:
    baseline = load("enacting.xml")
    first = baseline.provisions[0].model_copy(update={"notes": ("Amended by law x",)})
    extra = first.model_copy(update={"key": "§99", "notes": ()})
    oracle = baseline.model_copy(update={"provisions": (first, baseline.provisions[1], extra)})
    state = _run([_event(_op("insert_ledd", Address(section="§2", ledd=2), ("New.",)))])
    result = verify(state, oracle)
    assert {s.key: s.verdict for s in result.sections} == {
        "§1": "exact_raw",
        "§2": "text_differs",
        "§3": "only_in_replay",
        "§4": "only_in_replay",
        "§99": "only_in_oracle",
    }
    assert sum(result.counts.values()) == len(result.sections) == 5
    assert result.notes_without_replay_event == ("§1",)
    assert result.replay_events_without_note == ("§2",)


@pytest.mark.parametrize("change", ["heading", "text", "extra_ledd"])
def test_omission_marker_does_not_hide_a_difference_in_retained_text(change: str) -> None:
    baseline = load("enacting.xml")
    provision = baseline.provisions[0]
    ledd = provision.ledd[0].model_copy(update={"text": provision.ledd[0].text + OMISSION})
    heading = provision.heading
    if change == "heading":
        heading = "Different heading"
    elif change == "text":
        ledd = ledd.model_copy(update={"text": "Different text." + OMISSION})
    retained = (ledd,)
    if change == "extra_ledd":
        retained = (ledd, *provision.ledd[1:], Ledd(text="Extra.", xml="Extra."))
    oracle = baseline.model_copy(
        update={
            "provisions": (provision.model_copy(update={"heading": heading, "ledd": retained}),)
        }
    )
    result = verify(_run([]), oracle)
    assert result.sections[0].verdict == "text_differs"


def test_repeal_ledd_range_removes_every_addressed_ledd() -> None:
    state = _run([_event(_op("repeal_ledd", Address(section="§1", ledd=1, ledd_end=2)))])
    assert state.unresolved == ()
    assert texts(state, "§1") == []


def test_move_ledd_refuses_a_destination_ordinal_that_does_not_fit() -> None:
    move = _op("move_ledd", Address(section="§1", ledd=2, destination_ledd=4), destination="§3")
    state = _run([_event(move)])
    assert state.unresolved == (("x#I:0.0", "destination ledd 4 does not fit 1 existing"),)
    assert texts(state, "§1") == ENACTED_TEXT


def test_replacement_text_headed_with_another_section_is_refused() -> None:
    other = load("amending.xml")
    wrong = next(op for op in interpret(other).operations if op.kind == "replace_section")
    wrong = wrong.model_copy(update={"address": Address(section="§1")})
    state = _run([_event(wrong)])
    assert state.unresolved[0][1] == "replacement text is headed §3, not §1"


def test_term_substitution_is_scoped_to_listed_ledd_and_whole_words() -> None:
    sub = _op(
        "substitute_term", Address(), term_old="ledd", term_new="LEDD", locations="§ 1 annet ledd"
    )
    state = _run([_event(sub)])
    assert state.unresolved == ()
    assert texts(state, "§1") == [
        ENACTED_TEXT[0],
        "Annet LEDD første punktum. Annet LEDD annet punktum.",
    ]
    narrow = _op("substitute_term", Address(), term_old="fylke", term_new="X", locations="§ 1")
    state = _run([_event(narrow)])
    assert state.unresolved == (("x#I:0.0", "term not found in ['§1']"),)
    assert texts(state, "§1") == ENACTED_TEXT


def test_term_substitution_leaves_markup_alone() -> None:
    sub = _op("substitute_term", Address(), term_old="a", term_new="Z", locations="§ 2")
    state = _run([_event(sub)])
    provision = next(p for p in state.provisions if p.key == "§2")
    assert "bokstav Z" in provision.ledd[0].text
    assert 'data-name="a)"' in provision.ledd[0].xml


def test_bokstav_pattern_never_crosses_into_the_next_item() -> None:
    bokstav = _op("replace_bokstav", Address(section="§2", ledd=1, bokstav="a"), ("alpha",))
    state = _run([_event(bokstav)])
    assert texts(state, "§2") == [
        "Følgende gjelder: alpha bokstav b Bokstav b gjelder ikke private."
    ]


@pytest.mark.parametrize(
    ("locations", "expected"),
    [
        ("§ 28 tredje og fjerde ledd", [("§28", frozenset({3, 4}))]),
        ("§ 5 andre til femte ledd, § 6", [("§5", frozenset({2, 3, 4, 5})), ("§6", frozenset())]),
        ("§ 14 andre ledd tredje punktum og sjette ledd", [("§14", frozenset({2, 6}))]),
        ("§ 63 andre ledd andre og tredje punktum", [("§63", frozenset({2}))]),
    ],
)
def test_substitution_locations_read_every_listed_ledd(
    locations: str, expected: list[tuple[str, frozenset[int]]]
) -> None:
    assert _locations(locations) == expected
