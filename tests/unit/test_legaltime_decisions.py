"""Commencement decisions that conflict, supersede, narrow or coincide.

Primary-source shape behind these cases: arkivlova (lov 20. juni 2025 nr. 96)
received a full decision ("Loven trer i kraft 1. januar 2026", FOR-2025-06-20-1103)
and, later, "Delt iverksetjing" (FOR-2025-12-12-2510) that lists §§ 1-10 and
12-25 for the same date and leaves § 11 out. A later decision therefore can
narrow an earlier one. No precedence rule is invented here: agreement is
kept, every disagreement or narrowing is `unresolved` with both proofs.
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.legaltime.commencement import (
    Timing,
    _informational,
    baseline_timing,
    resolve_timings,
)
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import Operation, interpret
from lovspor.legaltime.scopes import Scope, _own_sections

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
LAW = "lov/2020-12-18-999"


def _law(clause: str = "Loven gjelder fra den tid Kongen bestemmer.") -> Document:
    body = (
        f'<html><body><header><dl><dd class="refid">{LAW}</dd>'
        '<dd class="dateInForce">Kongen bestemmer</dd>'
        '<dd class="dateOfPublication">2020-12-18 12:10</dd><dd class="title">Lov om endringer</dd>'
        '</dl></header><main><section class="section"><h2>I</h2><article class="defaultP">'
        "I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer:</article>"
        '<article class="defaultP">§ 1 skal lyde:</article>'
        '<article class="futureLegalArticle" data-name="§1"><article class="legalP">A.</article>'
        '</article><article class="defaultP">Ny § 2 a skal lyde:</article>'
        '<article class="futureLegalArticle" data-name="§2a"><article class="legalP">B.</article>'
        '</article></section><section class="section"><h2>II</h2><article class="defaultP">'
        "I lov 10. februar 1967 om behandlingsmåten skal § 5 lyde:</article>"
        '<article class="futureLegalArticle" data-name="§5"><article class="legalP">C.</article>'
        f'</article></section><section class="section"><h2>III</h2><article class="legalP">{clause}'
        "</article></section></main></body></html>"
    )
    return parse_document(body.encode(), "lti/2020/law.xml")


def _decision(
    refid: str, title: str, date_in_force: str, published: str, body: str, member: str = ""
) -> Document:
    xml = (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd><dd class="dateOfPublication">{published}</dd>'  # noqa: E501
        f'<dd class="title">{title}</dd></dl></header><main><article class="legalP">{body}</article>'  # noqa: E501
        "</main></body></html>"
    )
    return parse_document(xml.encode(), member or f"lti/x/{refid.rsplit('/', maxsplit=1)[-1]}.xml")


FULL = "Ikraftsetting av lov 18. desember 2020 nr. 999 om endringer"
DELT = "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer"


def _timings(law: Document, *decisions: Document) -> dict[str, Timing]:
    reading = interpret(law)
    return resolve_timings(law, reading.clauses, reading.operations, decisions)


def _by_part(law: Document, timings: dict[str, Timing]) -> dict[tuple[str, str | None], Timing]:
    return {(op.part, op.address.section): timings[op.id] for op in interpret(law).operations}


def _refids(timing: Timing) -> list[str]:
    return [proof.refid for proof in timing.proofs]


def test_two_full_decisions_with_the_same_date_agree_and_both_are_cited() -> None:
    law = _law()
    first = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    second = _decision(
        "forskrift/2021-02-01-2",
        FULL,
        "2021-07-01",
        "2021-02-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    timing = _by_part(law, _timings(law, first, second))[("I", "§1")]
    assert timing.status == "dated" and timing.effective == date(2021, 7, 1)
    assert _refids(timing) == ["forskrift/2021-01-01-1", "forskrift/2021-02-01-2"]


def test_two_full_decisions_with_different_dates_are_a_conflict_not_a_choice() -> None:
    law = _law()
    first = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    second = _decision(
        "forskrift/2021-02-01-2",
        FULL,
        "2021-10-01",
        "2021-02-01 10:00",
        "Loven trer i kraft 1. oktober 2021.",
    )
    for order in ((first, second), (second, first)):
        timings = _by_part(law, _timings(law, *order))
        for key in (("I", "§1"), ("I", "§2a"), ("II", "§5")):
            assert timings[key].status == "unresolved", key
            assert timings[key].reason == "conflicting commencement decisions"
            assert timings[key].effective is None
            assert sorted(_refids(timings[key])) == [
                "forskrift/2021-01-01-1",
                "forskrift/2021-02-01-2",
            ]


def test_same_day_decisions_with_different_dates_are_a_conflict_whatever_the_member_order() -> None:
    law = _law()
    a = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
        "lti/2021/sf-a.xml",
    )
    b = _decision(
        "forskrift/2021-01-01-2",
        FULL,
        "2021-08-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. august 2021.",
        "lti/2021/sf-b.xml",
    )
    for order in ((a, b), (b, a)):
        timing = _by_part(law, _timings(law, *order))[("I", "§1")]
        assert timing.status == "unresolved"
        assert timing.reason == "conflicting commencement decisions"


def test_later_partial_decision_narrows_an_earlier_full_one() -> None:
    law = _law()
    full = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    narrowing = _decision(
        "forskrift/2021-03-01-2", DELT, "2021-07-01", "2021-03-01 10:00",
        "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer. "
        "Endringsloven del I trer i kraft 1. juli 2021.",
    )  # fmt: skip
    timings = _by_part(law, _timings(law, full, narrowing))
    assert timings[("I", "§1")].status == "dated"
    assert timings[("I", "§1")].effective == date(2021, 7, 1)
    assert _refids(timings[("I", "§1")]) == ["forskrift/2021-01-01-1", "forskrift/2021-03-01-2"]
    assert timings[("II", "§5")].status == "unresolved"
    assert timings[("II", "§5")].reason == "narrowed by a later partial decision"
    assert timings[("II", "§5")].effective is None
    assert _refids(timings[("II", "§5")]) == ["forskrift/2021-01-01-1", "forskrift/2021-03-01-2"]


def test_later_partial_decision_with_another_date_is_a_conflict() -> None:
    law = _law()
    full = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    later = _decision(
        "forskrift/2021-03-01-2", DELT, "2021-10-01", "2021-03-01 10:00",
        "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer. "
        "Endringsloven del I trer i kraft 1. oktober 2021.",
    )  # fmt: skip
    timings = _by_part(law, _timings(law, full, later))
    assert timings[("I", "§1")].status == "unresolved"
    assert timings[("I", "§1")].reason == "conflicting commencement decisions"
    assert timings[("II", "§5")].reason == "narrowed by a later partial decision"


def test_partial_first_then_full_confirming_the_same_date_is_consistent() -> None:
    law = _law()
    partial = _decision(
        "forskrift/2021-01-01-1", DELT, "2021-07-01", "2021-01-01 10:00",
        "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer. "
        "Endringsloven del I trer i kraft 1. juli 2021.",
    )  # fmt: skip
    full = _decision(
        "forskrift/2021-03-01-2",
        FULL,
        "2021-07-01",
        "2021-03-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    timings = _by_part(law, _timings(law, partial, full))
    assert timings[("I", "§1")].effective == date(2021, 7, 1)
    assert timings[("II", "§5")].effective == date(2021, 7, 1)
    assert _refids(timings[("II", "§5")]) == ["forskrift/2021-03-01-2"]
    later_full = _decision(
        "forskrift/2021-03-01-3",
        FULL,
        "2021-09-01",
        "2021-03-02 10:00",
        "Loven trer i kraft 1. september 2021.",
    )
    conflicting = _by_part(law, _timings(law, partial, later_full))
    assert conflicting[("I", "§1")].reason == "conflicting commencement decisions"
    assert conflicting[("II", "§5")].effective == date(2021, 9, 1)


def test_partial_decision_outside_the_grammar_after_a_full_one_unsettles_what_it_might_cover() -> (
    None
):
    law = _law()
    full = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    opaque = _decision(
        "forskrift/2021-03-01-2",
        DELT,
        "2021-07-01",
        "2021-03-01 10:00",
        "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer. Resten seinare.",
    )
    timings = _by_part(law, _timings(law, full, opaque))
    assert {t.status for t in timings.values()} == {"unresolved"}
    assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}


def test_own_section_scope_dates_amendments_hosted_in_the_listed_sections() -> None:
    act = parse_document((FIXTURES / "enacting-embedded.xml").read_bytes(), "lti/2025/act.xml")
    reading = interpret(act)
    hosts = {op.address.section: op.host_section for op in reading.operations}
    assert hosts == {"§26": "§25", "§11": "§25"}
    full = _decision(
        "forskrift/2025-06-20-1103",
        "Ikraftsetting av lov 20. juni 2025 nr. 996 om prøvearkiv",
        "2026-01-01",
        "2025-06-20 15:30",
        "Loven trer i kraft 1. januar 2026.",
    )
    delt = _decision(
        "forskrift/2025-12-12-2510", "Delt iverksetjing av lov 20. juni 2025 nr. 996 om prøvearkiv",
        "2026-01-01", "2025-12-12 14:00",
        "Delt iverksetjing av lov 20. juni 2025 nr. 996 om prøvearkiv. Paragrafane 1 til 10 og "
        "§§ 12 til 25 gjeld frå 1. januar 2026. Paragraf 9 innleiingsteksten i lov 4. desember 1992 "  # noqa: E501
        "nr. 126 om arkiv held fram å gjelde fram til § 11 i lov 20. juni 2025 nr. 996 om prøvearkiv "  # noqa: E501
        "tek til å gjelde.",
    )  # fmt: skip
    timings = resolve_timings(act, reading.clauses, reading.operations, (full, delt))
    assert {t.status for t in timings.values()} == {"dated"}
    assert {t.effective for t in timings.values()} == {date(2026, 1, 1)}
    assert all(
        _refids(t) == ["forskrift/2025-06-20-1103", "forskrift/2025-12-12-2510"]
        for t in timings.values()
    )
    narrowed = delt.model_copy(update={"parts": ()})
    narrowed = _decision(
        "forskrift/2025-12-12-2510", "Delt iverksetjing av lov 20. juni 2025 nr. 996 om prøvearkiv",
        "2026-01-01", "2025-12-12 14:00",
        "Delt iverksetjing av lov 20. juni 2025 nr. 996 om prøvearkiv. Paragrafane 1 til 10 gjeld frå 1. januar 2026.",  # noqa: E501
    )  # fmt: skip
    timings = resolve_timings(act, reading.clauses, reading.operations, (full, narrowed))
    assert {t.reason for t in timings.values()} == {"narrowed by a later partial decision"}


def test_an_own_section_scope_on_an_amending_law_without_own_sections_is_unreadable() -> None:
    law = _law()
    delt = _decision(
        "forskrift/2021-03-01-2",
        DELT,
        "2021-07-01",
        "2021-03-01 10:00",
        "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer. Paragrafane 1 til 3 gjeld frå 1. juli 2021.",  # noqa: E501
    )
    timings = _by_part(law, _timings(law, delt))
    assert {t.reason for t in timings.values()} == {
        "partial decision names no part or section of the law"
    }


def test_operation_model_carries_its_host_section() -> None:
    assert Operation.model_fields["host_section"].default is None


def test_a_decision_disagreeing_with_the_law_s_own_clause_is_a_conflict() -> None:
    law = _law("Loven trer i kraft 1. juli 2021.")
    other = _decision(
        "forskrift/2021-02-01-2",
        FULL,
        "2021-10-01",
        "2021-02-01 10:00",
        "Loven trer i kraft 1. oktober 2021.",
    )
    timing = _by_part(law, _timings(law, other))[("I", "§1")]
    assert timing.status == "unresolved"
    assert timing.reason == "conflicting commencement decisions"
    assert _refids(timing) == [LAW, "forskrift/2021-02-01-2"]


def test_a_decision_agreeing_with_the_law_s_own_clause_adds_its_proof() -> None:
    law = _law("Loven trer i kraft 1. juli 2021.")
    same = _decision(
        "forskrift/2021-02-01-2",
        FULL,
        "2021-07-01",
        "2021-02-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    timing = _by_part(law, _timings(law, same))[("I", "§1")]
    assert timing.status == "dated" and timing.effective == date(2021, 7, 1)
    assert _refids(timing) == [LAW, "forskrift/2021-02-01-2"]


def test_a_partial_decision_not_covering_a_law_dated_operation_says_nothing_about_it() -> None:
    law = _law("Loven trer i kraft 1. juli 2021.")
    delt = _decision(
        "forskrift/2021-03-01-2", DELT, "2021-07-01", "2021-03-01 10:00",
        "Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer. "
        "Endringsloven del I trer i kraft 1. juli 2021.",
    )  # fmt: skip
    timings = _by_part(law, _timings(law, delt))
    assert timings[("II", "§5")].status == "dated"
    assert _refids(timings[("II", "§5")]) == [LAW]
    assert _refids(timings[("I", "§1")]) == [LAW, "forskrift/2021-03-01-2"]


def test_a_full_decision_without_a_readable_date_unsettles_what_it_reaches() -> None:
    law = _law()
    vague = _decision(
        "forskrift/2021-02-01-2",
        FULL,
        "Kongen bestemmer",
        "2021-02-01 10:00",
        "Loven trer i kraft seinare.",
    )
    timings = _by_part(law, _timings(law, vague))
    assert {t.status for t in timings.values()} == {"unresolved"}
    assert {t.reason for t in timings.values()} == {"full decision without a readable date"}
    assert all(_refids(t) == [LAW, "forskrift/2021-02-01-2"] for t in timings.values())


# ---------------------------------------------------------------- review round 2 (2026-11-07)


def _delt(body: str, refid: str = "forskrift/2021-03-01-2", when: str = "2021-07-01") -> Document:
    return _decision(refid, DELT, when, "2021-03-01 10:00", f"{DELT}. {body}")


def _embedded() -> tuple[Document, tuple[Operation, ...], tuple[str, ...]]:
    act = parse_document((FIXTURES / "enacting-embedded.xml").read_bytes(), "lti/2025/act.xml")
    reading = interpret(act)
    return act, reading.operations, reading.clauses


ACT_DELT = "Delt iverksetjing av lov 20. juni 2025 nr. 996 om prøvearkiv"


def _act_delt(body: str, refid: str = "forskrift/2025-12-12-2510") -> Document:
    return _decision(refid, ACT_DELT, "2026-01-01", "2025-12-12 14:00", f"{ACT_DELT}. {body}")


def test_an_exception_inside_an_own_section_listing_is_outside_the_grammar() -> None:
    act, ops, clauses = _embedded()
    for body in (
        "Paragrafane 1 til 25 unntatt § 11 gjeld frå 1. januar 2026.",
        "Paragrafane 1 til 25, bortsett frå § 11, gjeld frå 1. januar 2026.",
        "Paragrafane 1 til 25 med unntak av § 11 gjeld frå 1. januar 2026.",
    ):
        timings = resolve_timings(act, clauses, ops, (_act_delt(body),))
        assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}, (
            body
        )


def test_own_section_letters_are_read_and_a_lettered_range_is_refused() -> None:

    assert _own_sections("§ 2 a") == frozenset({"§2a"})
    assert _own_sections("§§ 2, 3 b og 7 til 9") == frozenset({"§2", "§3b", "§7", "§8", "§9"})
    assert _own_sections("§§ 2 a til 5") is None
    assert _own_sections("1 til 25 unntatt 11") is None
    assert _own_sections("1 til 10 og 12 til 25") == frozenset(
        {f"§{n}" for n in (*range(1, 11), *range(12, 26))}
    )
    assert _own_sections("25 til 12") is None


def test_two_scopes_of_one_decision_naming_different_dates_for_one_section_conflict() -> None:
    act, ops, clauses = _embedded()
    for body in (
        "Paragrafane 20 til 25 gjeld frå 1. januar 2026. Paragrafane 24 til 25 gjeld frå 1. mars 2026.",  # noqa: E501
        "Paragrafane 24 til 25 gjeld frå 1. mars 2026. Paragrafane 20 til 25 gjeld frå 1. januar 2026.",  # noqa: E501
    ):
        timings = resolve_timings(act, clauses, ops, (_act_delt(body),))
        assert {t.status for t in timings.values()} == {"unresolved"}, body
        assert {t.reason for t in timings.values()} == {"conflicting commencement decisions"}


def test_a_reservation_inside_a_part_scope_is_outside_the_grammar() -> None:
    law = _law()
    delt = _delt("Endringsloven del I trer i kraft 1. juli 2021, unntatt § 2 a.")
    timings = _by_part(law, _timings(law, delt))
    assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}


def test_a_full_decision_whose_body_names_another_date_or_a_reservation_is_unreadable() -> None:
    law = _law()
    for body, reason in (
        (
            "Loven trer i kraft 1. januar 2026, med unntak av § 11 som trer i kraft 1. juli 2026.",
            "full decision body carries an exception",
        ),
        ("Loven trer i kraft 1. juli 2026.", "full decision body disagrees with its header"),
    ):
        full = _decision("forskrift/2025-12-12-1", FULL, "2026-01-01", "2025-12-12 10:00", body)
        timings = _by_part(law, _timings(law, full))
        assert {t.status for t in timings.values()} == {"unresolved"}, body
        assert {t.reason for t in timings.values()} == {reason}, body
    for body in (
        "Loven trer i kraft 1. januar 2026. Departementet kan gi overgangsregler.",
        "Europaparlaments- og rådsforordning (EU) 2016/679 av 27. april 2016 om vern av fysiske "
        "personer trer i kraft 1. januar 2026. Lov 18. desember 2020 nr. 999 om endringer trer "
        "derfor i kraft 1. januar 2026, jf. kongelig resolusjon 15. juni 2018 nr. 875.",
    ):
        agreeing = _decision("forskrift/2025-12-12-1", FULL, "2026-01-01", "2025-12-12 10:00", body)
        timings = _by_part(law, _timings(law, agreeing))
        assert {t.effective for t in timings.values()} == {date(2026, 1, 1)}, body


def test_a_continues_to_apply_sentence_that_names_a_date_is_not_informational() -> None:

    assert _informational("Paragraf 9 i lov 4. desember 1992 nr. 126 held fram å gjelde.")
    assert not _informational(
        "Paragrafane 1 til 10 gjeld frå 1. januar 2026 og § 9 i lov 4. desember 1992 nr. 126 held fram å gjelde."  # noqa: E501
    )


def test_a_decision_without_a_publication_time_among_several_is_unreadable() -> None:
    law = _law()
    dated = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    undated = _decision(
        "forskrift/2021-03-01-2",
        DELT,
        "2021-07-01",
        "",
        f"{DELT}. Endringsloven del I trer i kraft 1. juli 2021.",
    )
    assert undated.header.published_at is None
    for order in ((dated, undated), (undated, dated)):
        timings = _by_part(law, _timings(law, *order))
        assert {t.status for t in timings.values()} == {"unresolved"}
        assert {t.reason for t in timings.values()} == {
            "decision without a publication time among several"
        }
    alone = _by_part(law, _timings(law, undated))
    assert alone[("I", "§1")].status == "dated"


def test_a_partial_decision_reaching_nothing_the_law_contains_is_unreadable() -> None:
    law = _law()  # parts I, II, III; no own sections
    for body in (
        "Endringsloven del IV trer i kraft 1. juli 2021.",
        "Paragrafane 1 til 10 gjeld frå 1. juli 2021.",
    ):
        timings = _by_part(law, _timings(law, _delt(body)))
        assert {t.reason for t in timings.values()} == {
            "partial decision names no part or section of the law"
        }, body
    act, ops, clauses = _embedded()  # own sections §1-§26 host the amendments in § 25
    timings = resolve_timings(
        act, clauses, ops, (_act_delt("Paragrafane 1 til 10 gjeld frå 1. januar 2026."),)
    )
    assert {t.status for t in timings.values()} == {"pending"}


def test_own_section_range_end_is_inclusive_and_exclusive_beyond_it() -> None:
    act, ops, clauses = _embedded()
    reaches = resolve_timings(
        act, clauses, ops, (_act_delt("Paragrafane 12 til 25 gjeld frå 1. januar 2026."),)
    )
    assert {t.status for t in reaches.values()} == {"dated"}
    short = resolve_timings(
        act, clauses, ops, (_act_delt("Paragrafane 12 til 24 gjeld frå 1. januar 2026."),)
    )
    assert {t.status for t in short.values()} == {"pending"}


def test_an_own_section_scope_outranks_a_whole_part_scope_for_a_hosted_operation() -> None:
    act, ops, clauses = _embedded()
    body = "Endringsloven del I trer i kraft 1. januar 2026. Paragrafane 20 til 25 gjeld frå 1. mars 2026."  # noqa: E501
    timings = resolve_timings(act, clauses, ops, (_act_delt(body),))
    assert {t.effective for t in timings.values()} == {date(2026, 3, 1)}


def test_a_partial_decision_with_only_informational_text_unsettles_everything() -> None:
    law = _law()
    timings = _by_part(law, _timings(law, _delt("Kongen kan gi overgangsreglar.")))
    assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}


def test_baseline_timing_folds_every_full_decision_for_the_act() -> None:

    act = parse_document((FIXTURES / "enacting.xml").read_bytes(), "lti/x/enacting.xml")
    assert (
        act.header.date_in_force == "Kongen bestemmer" or baseline_timing(act, ()).status == "dated"
    )
    title = "Ikraftsetting av lov 14. juni 2019 nr. 99 om prøving"
    first = _decision(
        "forskrift/2020-02-28-9999",
        title,
        "2021-01-01",
        "2020-02-28 10:00",
        "Loven trer i kraft 1. januar 2021.",
    )
    other = _decision(
        "forskrift/2020-06-01-1",
        title,
        "2021-03-01",
        "2020-06-01 10:00",
        "Loven trer i kraft 1. mars 2021.",
    )
    for order in ((first, other), (other, first)):
        timing = baseline_timing(act, order)
        assert timing.status == "unresolved", order
        assert timing.reason == "conflicting commencement decisions"
    same = _decision(
        "forskrift/2020-06-01-1",
        title,
        "2021-01-01",
        "2020-06-01 10:00",
        "Loven trer i kraft 1. januar 2021.",
    )
    agreed = baseline_timing(act, (first, same))
    assert agreed.status == "dated" and agreed.effective == date(2021, 1, 1)
    assert _refids(agreed) == ["forskrift/2020-02-28-9999", "forskrift/2020-06-01-1"]


def test_a_scope_cannot_carry_both_a_part_and_own_sections() -> None:

    with pytest.raises(ValueError, match="own_sections"):
        Scope(part="I", own_sections=frozenset({"§1"}), when=date(2026, 1, 1), quote="q")


# ---------------------------------------------------------------- conditional decisions


CONDITIONAL = "Når personvernforordningen trer i kraft"
MELDING = "Meddelelse om ikrafttredelse av lov 18. desember 2020 nr. 999 om endringer"


def test_a_conditional_decision_keeps_pending_with_its_proof_until_a_dated_one_resolves_it() -> (
    None
):
    law = _law()
    conditional = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        CONDITIONAL,
        "2021-01-01 10:00",
        "Loven trer i kraft når personvernforordningen trer i kraft.",
    )
    alone = _by_part(law, _timings(law, conditional))[("I", "§1")]
    assert alone.status == "pending"
    assert alone.reason == "commencement conditional on an event outside the archive"
    assert _refids(alone) == [LAW, "forskrift/2021-01-01-1"]
    melding = _decision(
        "forskrift/2021-02-01-2",
        MELDING,
        "2021-07-20",
        "2021-02-01 10:00",
        "Loven trer i kraft 20. juli 2021.",
    )
    resolved = _by_part(law, _timings(law, conditional, melding))[("I", "§1")]
    assert resolved.status == "dated" and resolved.effective == date(2021, 7, 20)
    assert resolved.reason == ""
    assert _refids(resolved) == ["forskrift/2021-01-01-1", "forskrift/2021-02-01-2"]


def test_a_conditional_decision_after_a_dated_one_is_a_conflict() -> None:
    law = _law("Loven trer i kraft 1. juli 2021.")
    conditional = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        CONDITIONAL,
        "2021-01-01 10:00",
        "Loven trer i kraft når personvernforordningen trer i kraft.",
    )
    timing = _by_part(law, _timings(law, conditional))[("I", "§1")]
    assert timing.status == "unresolved" and timing.reason == "conflicting commencement decisions"


def test_the_act_s_own_conditional_commencement_resolves_the_same_way() -> None:
    act = parse_document((FIXTURES / "enacting.xml").read_bytes(), "lti/x/enacting.xml")
    title = "Ikraftsetting av lov 14. juni 2019 nr. 99 om prøving"
    conditional = _decision(
        "forskrift/2020-02-28-9999",
        title,
        CONDITIONAL,
        "2020-02-28 10:00",
        "Loven trer i kraft når forordningen trer i kraft.",
    )
    alone = baseline_timing(act, (conditional,))
    assert alone.status == "pending"
    assert alone.reason == "commencement conditional on an event outside the archive"
    melding = _decision(
        "forskrift/2020-07-17-1",
        "Meddelelse om ikrafttredelse av lov 14. juni 2019 nr. 99 om prøving",
        "2021-01-01",
        "2020-07-17 10:00",
        "Loven trer i kraft 1. januar 2021.",
    )
    resolved = baseline_timing(act, (conditional, melding))
    assert resolved.status == "dated" and resolved.effective == date(2021, 1, 1)
    assert _refids(resolved) == ["forskrift/2020-02-28-9999", "forskrift/2020-07-17-1"]
    still_unreadable = _decision(
        "forskrift/2020-02-28-9999", title, "snart", "2020-02-28 10:00", "Loven trer i kraft snart."
    )
    assert (
        baseline_timing(act, (still_unreadable,)).reason == "full decision without a readable date"
    )


# ---------------------------------------------------------------- mutation round (2026-11-07)


def test_own_section_listing_edges() -> None:
    assert _own_sections("5 til 5") == frozenset({"§5"})
    assert _own_sections("1 til 10 unntatt") is None  # trailing residue
    assert _own_sections("§§") is None  # separators only, no section
    assert _own_sections("") is None


def test_own_section_sentence_with_an_unreadable_date_is_outside_the_grammar() -> None:
    act, ops, clauses = _embedded()
    timings = resolve_timings(
        act, clauses, ops, (_act_delt("Paragrafane 1 til 25 gjeld frå 1. foo 2026."),)
    )
    assert {t.reason for t in timings.values()} == {"partial decision outside the grammar"}


def test_full_decision_body_scan_skips_informational_sentences_and_reads_to_the_end() -> None:
    law = _law()
    fine = _decision(
        "forskrift/2025-12-12-1",
        FULL,
        "2026-01-01",
        "2025-12-12 10:00",
        "Loven trer i kraft 1. januar 2026. Kongen kan gjøre unntak i forskrift.",
    )
    assert {t.effective for t in _by_part(law, _timings(law, fine)).values()} == {date(2026, 1, 1)}
    late = _decision(
        "forskrift/2025-12-12-1",
        FULL,
        "2026-01-01",
        "2025-12-12 10:00",
        "Departementet kan gi overgangsregler. Loven trer i kraft 1. juli 2026.",
    )
    assert {t.reason for t in _by_part(law, _timings(law, late)).values()} == {
        "full decision body disagrees with its header"
    }


def test_a_full_titled_decision_whose_body_only_scopes_one_part_reaches_only_that_part() -> None:
    law = _law()
    full = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Endringsloven del I trer i kraft 1. juli 2021.",
    )
    timings = _by_part(law, _timings(law, full))
    assert timings[("I", "§1")].effective == date(2021, 7, 1)
    assert timings[("II", "§5")].status == "pending"  # the body commenced part I, nothing else


def test_unsettling_clears_a_retroactive_flag() -> None:
    law = _law("Loven trer i kraft 1. juli 2020.")  # published 2020-12-18: retroactive
    before = _by_part(law, _timings(law))[("I", "§1")]
    assert before.retroactive is True
    other = _decision(
        "forskrift/2021-02-01-2",
        FULL,
        "2021-10-01",
        "2021-02-01 10:00",
        "Loven trer i kraft 1. oktober 2021.",
    )
    after = _by_part(law, _timings(law, other))[("I", "§1")]
    assert after.status == "unresolved" and after.retroactive is False and after.effective is None


def test_two_decisions_both_without_a_publication_time_are_unreadable() -> None:
    law = _law()
    a = _decision(
        "forskrift/2021-01-01-1", FULL, "2021-07-01", "", "Loven trer i kraft 1. juli 2021."
    )
    b = _decision(
        "forskrift/2021-02-01-2", FULL, "2021-07-01", "", "Loven trer i kraft 1. juli 2021."
    )
    timings = _by_part(law, _timings(law, a, b))
    assert {t.reason for t in timings.values()} == {
        "decision without a publication time among several"
    }


def test_the_undated_decision_unsettles_first_and_the_dated_one_is_then_left_alone() -> None:
    law = _law()
    dated = _decision(
        "forskrift/2021-01-01-1",
        FULL,
        "2021-07-01",
        "2021-01-01 10:00",
        "Loven trer i kraft 1. juli 2021.",
    )
    undated = _decision(
        "forskrift/2021-03-01-2",
        DELT,
        "2021-07-01",
        "",
        f"{DELT}. Endringsloven del I trer i kraft 1. juli 2021.",
    )
    for order in ((dated, undated), (undated, dated)):
        timing = _by_part(law, _timings(law, *order))[("I", "§1")]
        assert _refids(timing) == [LAW, "forskrift/2021-03-01-2"], order


def test_baseline_stops_folding_once_unresolved() -> None:
    act = parse_document((FIXTURES / "enacting.xml").read_bytes(), "lti/x/enacting.xml")
    title = "Ikraftsetting av lov 14. juni 2019 nr. 99 om prøving"
    first = _decision(
        "forskrift/2020-02-28-9999",
        title,
        "2021-01-01",
        "2020-02-28 10:00",
        "Loven trer i kraft 1. januar 2021.",
    )
    other = _decision(
        "forskrift/2020-06-01-1",
        title,
        "2021-03-01",
        "2020-06-01 10:00",
        "Loven trer i kraft 1. mars 2021.",
    )
    third = _decision(
        "forskrift/2020-07-01-2",
        title,
        "2021-03-01",
        "2020-07-01 10:00",
        "Loven trer i kraft 1. mars 2021.",
    )
    timing = baseline_timing(act, (first, other, third))
    assert timing.status == "unresolved"
    assert _refids(timing) == ["forskrift/2020-02-28-9999", "forskrift/2020-06-01-1"]
