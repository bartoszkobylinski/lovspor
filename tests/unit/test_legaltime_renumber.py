"""Renumbering sentences: consequential ones are absorbed, everything else fails closed.

A sentence such as ``Nåværende annet ledd blir tredje ledd`` after ``§ 1 nytt annet ledd skal
lyde`` moves no legal text: the insertion already shifted the old ledd. Such a sentence is
*absorbed* by the operation before it and carries its id. A renumbering that nothing before it
implies, or that states a shift the preceding operation did not cause, would change legal state
if it were real, so the replay refuses it and names the provision.
"""

from datetime import date
from pathlib import Path

from lovspor.legaltime import replay
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import Address, Operation, _shift, interpret
from lovspor.legaltime.replay import Event, ProvisionState, ReplayState, _Working

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
ENACTED = Timing(status="dated", effective=date(2021, 1, 1))
DATED = Timing(status="dated", effective=date(2022, 1, 1))
INTRO = (
    '<article class="defaultP">I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer:'
    "</article>"
)


def _amending(*articles: str) -> Document:
    body = (
        '<html><body><header><dl><dd class="refid">lov/2021-06-01-7</dd></dl></header>'
        f'<main><section class="section"><h2>I</h2>{INTRO}{"".join(articles)}</section></main>'
        "</body></html>"
    )
    return parse_document(body.encode(), "lti/2021/x.xml")


def _instruction(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _text(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _ops(*articles: str) -> tuple[Operation, ...]:
    return interpret(_amending(*articles)).operations


def _state(*articles: str, at: date = date(2023, 1, 1)) -> ReplayState:
    events = [
        Event(
            sequence=i,
            law="lov/2021-06-01-7",
            member="m",
            operation=op,
            timing=DATED,
            published_at=None,
        )
        for i, op in enumerate(_ops(*articles))
    ]
    baseline = parse_document((FIXTURES / "enacting.xml").read_bytes(), "lti/x/enacting.xml")
    return replay.replay(baseline, ENACTED, events, at)


def _provision(state: ReplayState, key: str) -> ProvisionState:
    return next(p for p in state.provisions if p.key == key)


def _ledd(state: ReplayState, key: str) -> list[str]:
    return [ledd.text for ledd in _provision(state, key).ledd]


# ---------------------------------------------------------------- absorbed (consequential)


def test_shift_after_an_insertion_is_absorbed_and_applied() -> None:
    ops = _ops(
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    assert [op.kind for op in ops] == ["insert_ledd", "renumber"]
    assert ops[1].absorbed_by == ops[0].id
    assert ops[1].address == Address(section="§1")
    state = _state(
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    assert state.unresolved == () and len(state.applied) == 2
    assert _ledd(state, "§1")[1:] == [
        "Nytt.",
        "Annet ledd første punktum. Annet ledd annet punktum.",
    ]
    assert _provision(state, "§1").applied == (ops[0].id, ops[1].id)


def test_two_ledd_shift_after_an_insertion_is_absorbed() -> None:
    ops = _ops(
        _instruction("§ 1 nytt første ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende første og annet ledd blir annet og nytt tredje ledd."),
    )
    assert ops[1].absorbed_by == ops[0].id


def test_range_shift_after_a_two_ledd_insertion_is_absorbed() -> None:
    ops = _ops(
        _instruction("§ 1 nytt første og annet ledd skal lyde:"),
        _text("Ett."),
        _text("To."),
        _instruction("Noverande første til andre ledd blir tredje til fjerde ledd."),
    )
    assert ops[1].absorbed_by == ops[0].id


def test_shift_down_after_a_ledd_repeal_is_absorbed() -> None:
    ops = _ops(
        _instruction("§ 1 første ledd oppheves. Nåværende annet ledd blir første ledd."),
    )
    assert [op.kind for op in ops] == ["repeal_ledd", "renumber"]
    assert ops[1].absorbed_by == ops[0].id
    state = _state(_instruction("§ 1 første ledd oppheves. Nåværende annet ledd blir første ledd."))
    assert state.unresolved == () and _ledd(state, "§1") == [
        "Annet ledd første punktum. Annet ledd annet punktum."
    ]


def test_shift_down_after_a_punktum_repeal_is_absorbed() -> None:
    articles = (
        _instruction(
            "§ 1 første ledd første punktum oppheves. Nåværende annet punktum blir første punktum."
        ),
    )
    ops = _ops(*articles)
    assert [op.kind for op in ops] == ["repeal_punktum", "renumber"]
    assert ops[1].absorbed_by == ops[0].id
    state = _state(*articles)
    assert state.unresolved == () and _ledd(state, "§1")[0] == "Den gjelder også retting."


def test_shift_after_a_punktum_insertion_is_absorbed() -> None:
    articles = (
        _instruction("§ 1 første ledd nytt første punktum skal lyde:"),
        _text("Innledning."),
        _instruction("Nåværende første og annet punktum blir annet og tredje punktum."),
    )
    ops = _ops(*articles)
    assert ops[1].absorbed_by == ops[0].id
    assert _state(*articles).unresolved == ()


def test_replacement_turned_insertion_absorbs_its_shift() -> None:
    ops = _ops(
        _instruction("§ 1 annet ledd skal lyde:"),
        _text("Nytt annet."),
        _instruction("Nåværende annet ledd blir nytt tredje ledd."),
    )
    assert [op.kind for op in ops] == ["insert_ledd", "renumber"]
    assert ops[1].absorbed_by == ops[0].id


# ---------------------------------------------------------------- refused (would change state)


def test_standalone_renumbering_is_unresolved_and_names_its_provision() -> None:
    state = _state(_instruction("§ 1 nåværende annet ledd blir tredje ledd."))
    assert len(state.unresolved) == 1 and state.applied == ()
    assert state.unresolved[0][1] == "renumbering not implied by the operation before it"
    assert _provision(state, "§1").problems == (
        f"{state.unresolved[0][0]}: renumbering not implied by the operation before it",
    )
    assert _ledd(state, "§1") == [
        "Loven gjelder prøving av fylkesmannen. Den gjelder også retting.",
        "Annet ledd første punktum. Annet ledd annet punktum.",
    ]


def test_renumbering_without_any_section_lands_on_the_act() -> None:
    state = _state(_instruction("Nåværende annet ledd blir tredje ledd."))
    assert len(state.unresolved) == 1
    assert state.act_level_problems == (
        f"{state.unresolved[0][0]}: renumbering not implied by the operation before it",
    )


def test_shift_the_insertion_did_not_cause_is_refused() -> None:
    articles = (
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet ledd blir fjerde ledd."),
    )
    ops = _ops(*articles)
    assert ops[1].absorbed_by is None
    state = _state(*articles)
    assert [problem for _, problem in state.unresolved] == [
        "renumbering not implied by the operation before it"
    ]
    assert state.applied == (ops[0].id,)


def test_shift_of_a_ledd_the_repeal_removed_is_refused() -> None:
    ops = _ops(_instruction("§ 1 første ledd oppheves. Nåværende første ledd blir annet ledd."))
    assert ops[1].absorbed_by is None


def test_shift_naming_another_section_is_refused() -> None:
    ops = _ops(
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("§ 2 nåværende annet ledd blir tredje ledd."),
    )
    assert ops[1].absorbed_by is None
    assert ops[1].address == Address(section="§2")


def test_shift_of_the_wrong_unit_is_refused() -> None:
    ops = _ops(
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet punktum blir tredje punktum."),
    )
    assert ops[1].absorbed_by is None


def test_a_chain_of_renumberings_absorbs_only_the_first() -> None:
    articles = (
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction(
            "Nåværende annet ledd blir tredje ledd. Nåværende tredje ledd blir fjerde ledd."
        ),
    )
    ops = _ops(*articles)
    assert [op.absorbed_by for op in ops] == [None, ops[0].id, None]
    state = _state(*articles)
    assert [problem for _, problem in state.unresolved] == [
        "renumbering not implied by the operation before it"
    ]


def test_section_renumbering_is_not_absorbed() -> None:
    ops = _ops(
        _instruction("Ny § 2 b skal lyde:"),
        _text("Ny."),
        _instruction("Nåværende § 2 b blir ny § 2 c."),
    )
    assert [op.kind for op in ops] == ["insert_section", "renumber"]
    assert ops[1].absorbed_by is None


def test_absorbed_renumbering_fails_when_its_insertion_failed() -> None:
    articles = (
        _instruction("§ 1 nytt femte ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende femte ledd blir sjette ledd."),
    )
    ops = _ops(*articles)
    assert ops[1].absorbed_by == ops[0].id
    state = _state(*articles)
    assert [problem for _, problem in state.unresolved] == [
        "insertion point does not exist",
        "the operation this renumbering follows did not apply",
    ]
    assert state.applied == ()


def test_absorbed_renumbering_waits_with_its_insertion() -> None:
    articles = (
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    before = _state(*articles, at=date(2021, 12, 31))
    assert before.applied == () and before.unresolved == ()
    assert len(before.future) == 2 and before.withheld == ()


def test_a_later_amendment_addresses_the_shifted_number() -> None:
    state = _state(
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
        _instruction("§ 1 tredje ledd skal lyde:"),
        _text("Tredje, erstattet."),
    )
    assert state.unresolved == ()
    assert _ledd(state, "§1") == [
        "Loven gjelder prøving av fylkesmannen. Den gjelder også retting.",
        "Nytt.",
        "Tredje, erstattet.",
    ]


def test_cross_references_in_other_provisions_are_not_rewritten() -> None:
    """The replay renumbers positions, never the prose that cites them: a known limit."""
    state = _state(
        _instruction("§ 3 første ledd skal lyde:"),
        _text("Se § 1 annet ledd."),
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    assert state.unresolved == ()
    assert _ledd(state, "§3") == ["Se § 1 annet ledd."]


# ---------------------------------------------------------------- review round 2 (2026-11-07)


def test_a_reversed_or_empty_range_is_not_a_shift_and_does_not_crash_interpretation() -> None:

    assert _shift("Nåværende fjerde til annet ledd blir femte til tredje ledd.") is None
    ops = _ops(
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende fjerde til annet ledd blir femte til tredje ledd."),
    )
    assert [op.kind for op in ops] == ["insert_ledd", "renumber"]
    assert ops[1].absorbed_by is None


def test_shift_pairs_are_read_exactly() -> None:

    shift = _shift("Noverande første til andre ledd blir tredje til fjerde ledd.")
    assert shift is not None and shift.pairs == ((1, 3), (2, 4))
    pair = _shift("Nåværende annet og tredje ledd blir tredje og nytt fjerde ledd.")
    assert pair is not None and pair.pairs == ((2, 3), (3, 4))
    assert _shift("Nåværende annet og tredje ledd blir tredje ledd.") is None


def test_shifting_the_repealed_ledd_itself_or_the_ledd_before_an_insertion_is_refused() -> None:
    repealed = _ops(_instruction("§ 1 annet ledd oppheves. Nåværende annet ledd blir første ledd."))
    assert repealed[1].absorbed_by is None
    before = _ops(
        _instruction("§ 1 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende første ledd blir annet ledd."),
    )
    assert before[1].absorbed_by is None


def test_a_stray_renumbering_after_an_unrelated_operation_stays_act_level() -> None:
    ops = _ops(
        _instruction("§ 1 første ledd skal lyde:"),
        _text("Erstattet."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    assert ops[1].absorbed_by is None and ops[1].address == Address()
    state = _state(
        _instruction("§ 1 første ledd skal lyde:"),
        _text("Erstattet."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    assert len(state.act_level_problems) == 1


def test_an_absorbed_renumbering_checks_the_provision_has_the_ledd_it_names() -> None:
    """§ 3 has one ledd in the baseline: a law that inserts a second and renumbers 'annet' to
    'tredje' presupposes two, so the baseline disagrees with the law (a missed amendment)."""
    articles = (
        _instruction("§ 3 nytt annet ledd skal lyde:"),
        _text("Nytt."),
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    ops = _ops(*articles)
    assert ops[1].absorbed_by == ops[0].id
    state = _state(*articles)
    assert [problem for _, problem in state.unresolved] == [
        "renumbering names ledd 3, the provision has 2"
    ]
    assert _ledd(state, "§3") == ["Loven gjelder fra den tid Kongen bestemmer.", "Nytt."]


def test_an_absorbed_punktum_renumbering_checks_the_ledd_has_the_punktum_it_names() -> None:
    articles = (
        _instruction("§ 3 første ledd nytt første punktum skal lyde:"),
        _text("Innledning."),
        _instruction("Nåværende første og annet punktum blir annet og tredje punktum."),
    )
    ops = _ops(*articles)
    assert ops[1].absorbed_by == ops[0].id and ops[1].address.ledd == 1
    state = _state(*articles)
    assert [problem for _, problem in state.unresolved] == [
        "renumbering names punktum 3, the ledd has 2"
    ]


# ---------------------------------------------------------------- mutation round (2026-11-07)


def test_shift_reads_og_as_two_numbers_and_til_as_a_run() -> None:
    og = _shift("Nåværende annet og fjerde ledd blir tredje og femte ledd.")
    assert og is not None and og.pairs == ((2, 3), (4, 5))
    til = _shift("Nåværende første til tredje ledd blir andre til fjerde ledd.")
    assert til is not None and til.pairs == ((1, 2), (2, 3), (3, 4))


def test_a_renumbering_after_a_section_replacement_is_refused_without_crashing() -> None:
    ops = _ops(
        _instruction("§ 1 skal lyde:"),
        '<article class="futureLegalArticle" data-name="§1"><article class="legalP">Ny.</article></article>',  # noqa: E501
        _instruction("Nåværende annet ledd blir tredje ledd."),
    )
    assert [op.kind for op in ops] == ["replace_section", "renumber"]
    assert ops[1].absorbed_by is None


def test_punktum_shift_uses_the_punktum_numbers_not_the_ledd_number() -> None:
    articles = (
        _instruction("§ 1 annet ledd nytt første punktum skal lyde:"),
        _text("Innledning."),
        _instruction("Nåværende første punktum blir annet punktum."),
    )
    ops = _ops(*articles)
    assert ops[0].address.ledd == 2 and ops[0].address.punktum == 1
    assert ops[1].absorbed_by == ops[0].id and ops[1].address.ledd == 2


def test_a_two_punktum_insertion_absorbs_a_two_step_shift() -> None:
    ops = _ops(
        _instruction("§ 1 første ledd nytt første og annet punktum skal lyde:"),
        _text("En. To."),
        _instruction("Nåværende første og annet punktum blir tredje og fjerde punktum."),
    )
    assert ops[1].absorbed_by == ops[0].id
    off = _ops(
        _instruction("§ 1 første ledd nytt første og annet punktum skal lyde:"),
        _text("En. To."),
        _instruction("Nåværende første og annet punktum blir annet og tredje punktum."),
    )
    assert off[1].absorbed_by is None


def test_renumbering_count_messages_name_what_is_missing() -> None:
    baseline = parse_document((FIXTURES / "enacting.xml").read_bytes(), "lti/x/enacting.xml")
    working = _Working(baseline)
    working.done.add("prev")
    missing = Operation(
        id="r", kind="renumber", act="lov/2019-06-14-99", address=Address(section="§9"), part="I",
        instruction="Nåværende annet ledd blir tredje ledd.", absorbed_by="prev",
    )  # fmt: skip
    assert working.apply_renumber(missing) == "renumbering names ledd 3, the provision has 0"
    listed = missing.model_copy(
        update={
            "address": Address(section="§2", ledd=1),
            "instruction": "Nåværende første punktum blir annet punktum.",
        }
    )
    assert working.apply_renumber(listed) == "renumbering names punktum 2, the ledd has 0"
    garbled = missing.model_copy(update={"instruction": "i"})
    assert working.apply_renumber(garbled) == "renumbering sentence no longer parses"
