"""Real-input boundary cases for the survivors in the supplied mutation artifact."""

from datetime import date

import pytest

from lovspor.legaltime import commencement as c
from lovspor.legaltime import instructions as ins
from lovspor.legaltime import scopes, verify
from lovspor.legaltime.instructions import Address
from tests.unit.test_legaltime_mutants import ACT, _law, _op, _working
from tests.unit.test_legaltime_remediation import xml_doc
from tests.unit.test_legaltime_renumber import _instruction, _ops, _text


def decision(body: str = "", published: str = "", raw: str = "2022-01-01"):
    return xml_doc(
        f'<article class="legalP">{body}</article>',
        f'<dd class="title">Ikraftsetting av lov 14. juni 2019 nr. 99</dd>'
        f'<dd class="dateInForce">{raw}</dd><dd class="dateOfPublication">{published}</dd>',
    )


@pytest.mark.parametrize(
    "listing,expected", [("1 til 1", frozenset({"§1"})), ("", None), ("1 !", None)]
)
def test_section_listing_requires_complete_nonempty_input(listing, expected):
    assert scopes._own_sections(listing) == expected


@pytest.mark.parametrize(
    "sentence",
    ["Paragrafane 1 ! gjeld frå 1. januar 2022", "Paragrafane 1 gjeld frå 1. ukjent 2022"],
)
def test_scope_requires_both_sections_and_date(sentence):
    assert scopes._scopes_of(sentence, None) is None


def test_informational_reservation_is_not_a_body_conflict():
    doc = decision("Departementet kan gi regler unntatt om klage.")
    assert c._blocks(doc) == [(None, [])]  # informational prose is not part of any block


def test_body_checks_sentences_after_informational_prose():
    doc = decision("Departementet kan gi regler. Loven trer i kraft 1. januar 2023.")
    assert c._blocks(doc) == [(None, ["Loven trer i kraft 1. januar 2023."])]
    assert c._block_date(doc, ["Loven trer i kraft 1. januar 2023."]) == date(2023, 1, 1)


def test_citation_removal_preserves_adjacent_date_boundary():
    assert c._stated_date("trer i kraft lov 1. januar 20201. februar 2022") == date(2022, 2, 1)


def test_unsettled_timing_clears_retroactivity():
    current = c.Timing(status="dated", effective=date(2020, 1, 1), retroactive=True)
    result = c._unsettled(current, decision(), "conflict")
    assert result.status == "unresolved"
    assert result.effective is None
    assert result.retroactive is False


def test_full_titled_decision_with_a_scope_shaped_body_reaches_only_that_scope():
    law = _law(ACT, "Kongen bestemmer", "")
    op = _op("replace_ledd", Address(section="§1", ledd=1))
    doc = decision("Endringsloven del II trer i kraft 1. januar 2022.")
    result = c.resolve_timings(law, (), (op,), (doc,))[op.id]
    assert result.status == "pending"  # the body commenced part II; this operation is in part I


def test_multiple_unpublished_decisions_cannot_establish_order():
    law = _law(ACT, "Kongen bestemmer", "")
    result = c.baseline_timing(law, (decision(), decision()))
    assert result.reason == "decision without a publication time among several"


def test_published_decision_retains_its_readable_status_in_mixed_order():
    law = _law(ACT, "Kongen bestemmer", "")
    readings = c._citing(law, (decision(), decision(published="2021-01-01 12:00")))
    assert readings[0].unreadable == "decision without a publication time among several"
    assert readings[1].unreadable is None


def test_baseline_stops_at_first_unresolved_evidence():
    law = _law(ACT, "Kongen bestemmer", "")
    first = decision(published="2021-01-01 12:00", raw="unknown")
    later = decision(published="2021-01-02 12:00")
    result = c.baseline_timing(law, (first, later))
    assert result.reason == "full decision without a readable date"
    assert len(result.proofs) == 2


def test_substitution_retains_own_section_and_change_part():
    doc = xml_doc(
        '<article class="change" data-change-part="I II">'
        '<article class="legalArticle" data-name="§9">'
        '<article class="defaultP">I følgende bestemmelser skal «før» endres til «etter»:</article>'
        '<article class="legalP">lov 14. juni 2019 nr. 99 § 1</article>'
        "</article></article>"
    )
    assert doc.parts[0].nodes[0].change_part == ("I", "II")
    result = ins.interpret(doc)
    assert len(result.operations) == 1
    assert result.operations[0].kind == "substitute_term"
    assert result.operations[0].host_section == "§9"


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("Nåværende første og tredje ledd blir annet og fjerde ledd.", ((1, 2), (3, 4))),
        ("Nåværende første til tredje ledd blir annet til fjerde ledd.", ((1, 2), (2, 3), (3, 4))),
    ],
)
def test_shift_preserves_pairs_and_ranges(sentence, expected):
    shift = ins._shift(sentence)
    assert shift is not None
    assert shift.pairs == expected


@pytest.mark.parametrize(
    "kind,address,unit,expected",
    [
        ("insert_punktum", Address(ledd=1, punktum=2, punktum_end=4), "punktum", (2, 3)),
        ("replace_ledd", Address(ledd=2), "ledd", None),
        ("insert_ledd", Address(), "ledd", None),
        ("insert_ledd", Address(ledd=2), "ledd", (2, 1)),
        ("repeal_ledd", Address(ledd=2, ledd_end=3), "ledd", (4, -2)),
    ],
)
def test_implied_shift_uses_the_requested_unit(kind, address, unit, expected):
    assert ins._implied(_op(kind, address), unit) == expected


def test_unabsorbed_shift_discards_the_previous_ledd_address():
    ops = _ops(
        _instruction("§ 1 første ledd skal lyde:"),
        _text("Nytt."),
        _instruction("§ 1 nåværende annet ledd blir fjerde ledd."),
    )
    assert ops[1].absorbed_by is None
    assert ops[1].address == Address(section="§1")


def test_blanket_problem_detail_separates_multiple_reasons():
    section = verify.SectionVerdict(key="§1", verdict="exact_raw")
    result = verify._blanket(section, ("first", "second"))
    assert result.detail == "act-level problem, target unknown: first; second"


def test_absorbed_renumber_reports_unparseable_instruction():
    working = _working()
    working.done.add("previous")
    op = _op("renumber", Address(section="§1"), instruction="broken", absorbed_by="previous")
    assert working.apply_renumber(op) == "renumbering sentence no longer parses"


@pytest.mark.parametrize(
    "unit,address",
    [("ledd", Address(section="§999")), ("punktum", Address(section="§1", ledd=999))],
)
def test_missing_renumber_target_has_zero_units(unit, address):
    assert _working()._renumbered_count(_op("renumber", address), unit) == 0


@pytest.mark.parametrize("raw", ["2022-01-01", "2022-99-99"])
def test_partial_decision_uses_body_date_even_with_dated_header(raw):
    law = _law(ACT, "Kongen bestemmer", "")
    doc = xml_doc(
        '<article class="legalP">Endringsloven del I trer i kraft 1. januar 2023.</article>',
        '<dd class="title">Delt ikraftsetting av lov 14. juni 2019 nr. 99</dd>'
        f'<dd class="dateInForce">{raw}</dd>',
    )
    op = _op("replace_ledd", Address(section="§1", ledd=1))
    result = c.resolve_timings(law, (), (op,), (doc,))[op.id]
    assert result.effective == date(2023, 1, 1)
