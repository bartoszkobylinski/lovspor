"""Commencement: dates only from the header, the law's clause, and cited decisions."""

from datetime import date
from pathlib import Path

from lovspor.legaltime.commencement import (
    Timing,
    clause_timings,
    header_timing,
    is_decision_for,
    resolve_timings,
)
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import Operation, interpret

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"


def load(name: str) -> Document:
    return parse_document((FIXTURES / name).read_bytes(), f"lti/x/{name}")


def _future(key: str, text: str) -> str:
    return (
        f'<article class="futureLegalArticle" data-name="{key}">'
        f'<article class="legalP">{text}</article></article>'
    )


def _law(
    refid: str, date_in_force: str, clause: str, published: str = "2020-12-18 12:10"
) -> Document:
    header = (
        f'<header><dl><dd class="refid">{refid}</dd><dd class="dateInForce">{date_in_force}</dd>'
        f'<dd class="dateOfPublication">{published}</dd><dd class="title">Lov om endringer</dd>'
        "</dl></header>"
    )
    part_one = (
        '<section class="section"><h2>I</h2><article class="defaultP">I lov 14. juni 2019 nr. 99 '
        'om prøving gjøres følgende endringer:</article><article class="defaultP">§ 1 skal lyde:'
        f'</article>{_future("§1", "A.")}<article class="defaultP">Ny § 2 a skal lyde:</article>'
        f"{_future('§2a', 'B.')}</section>"
    )
    part_two = (
        '<section class="section"><h2>II</h2><article class="defaultP">I lov 10. februar 1967 '
        f"om behandlingsmåten skal § 5 lyde:</article>{_future('§5', 'C.')}</section>"
    )
    part_three = (
        f'<section class="section"><h2>III</h2><article class="legalP">{clause}</article></section>'
    )
    body = f"<html><body>{header}<main>{part_one}{part_two}{part_three}</main></body></html>"
    return parse_document(body.encode(), "lti/x/law.xml")


def _timings(
    law: Document, decisions: tuple[Document, ...] = ()
) -> tuple[tuple[Operation, ...], dict[str, Timing]]:
    result = interpret(law)
    return result.operations, resolve_timings(law, result.clauses, result.operations, decisions)


def test_header_timing_reads_the_three_header_shapes() -> None:
    assert header_timing(load("resolution-full.xml")).effective == date(2021, 1, 1)
    assert header_timing(load("enacting.xml")).status == "pending"
    assert header_timing(load("amending.xml")).status == "unresolved"


def test_straks_dates_every_operation_from_the_header_date() -> None:
    ops, timings = _timings(_law("lov/2022-06-22-81", "2022-06-22", "Loven trer i kraft straks."))
    assert {timings[op.id].effective for op in ops} == {date(2022, 6, 22)}
    assert all(timings[op.id].status == "dated" for op in ops)
    assert timings[ops[0].id].proofs[0].quote == "Loven trer i kraft straks."
    assert timings[ops[0].id].known_from == date(2020, 12, 18)


def test_delegated_commencement_is_pending_until_a_decision_cites_the_law() -> None:
    law = _law(
        "lov/2023-06-16-59",
        "Kongen bestemmer",
        "Loven gjelder fra den tid Kongen bestemmer. "
        "Kongen kan sette i kraft de enkelte bestemmelsene til forskjellig tid.",
    )
    ops, timings = _timings(law)
    assert {timings[op.id].status for op in ops} == {"pending"}
    decision = parse_document(
        b'<html><body><header><dl><dd class="refid">forskrift/2023-06-16-934</dd>'
        b'<dd class="dateInForce">2023-10-01</dd>'
        b'<dd class="dateOfPublication">2023-06-16 16:25</dd>'
        b'<dd class="title">Ikraftsetting av lov 16. juni 2023 nr. 59 om endringer i arveloven'
        b" mv.</dd>"
        b'</dl></header><main><article class="legalP">Loven trer i kraft 1. oktober 2023.</article>'
        b"</main></body></html>",
        "lti/2023/sf-20230616-0934.xml",
    )
    assert is_decision_for(decision, "lov/2023-06-16-59")
    assert not is_decision_for(decision, "lov/2019-06-14-21")
    ops, timings = _timings(law, (decision,))
    assert {timings[op.id].effective for op in ops} == {date(2023, 10, 1)}
    assert timings[ops[0].id].proofs[0].refid == "forskrift/2023-06-16-934"


def test_mixed_header_splits_by_the_likevel_exception_and_a_partial_decision() -> None:
    law = load("amending.xml")
    ops, timings = _timings(law)
    by_section = {(op.kind, op.address.section): timings[op.id] for op in ops}
    assert by_section[("replace_punktum", "§1")].effective == date(2020, 12, 18)
    assert by_section[("insert_section", "§2a")].status == "pending"
    assert by_section[("replace_ledd", "§5")].effective == date(2020, 12, 18)
    ops, timings = _timings(law, (load("resolution-partial.xml"),))
    new_section = next(timings[op.id] for op in ops if op.address.section == "§2a")
    assert new_section.status == "dated"
    assert new_section.effective == date(2022, 1, 1)
    assert new_section.known_from == date(2021, 5, 31)
    assert new_section.proofs[0].refid == "forskrift/2021-05-28-9998"


def test_part_scoped_exception_and_for_ovrig_default() -> None:
    law = _law(
        "lov/2026-06-19-48",
        "2026-06-19, Kongen bestemmer",
        "Endringen av prøveloven i del II i loven her trer i kraft straks. For øvrig gjelder loven "
        "fra den tid Kongen bestemmer. De enkelte bestemmelser kan settes i kraft til ulik tid.",
    )
    ops, timings = _timings(law)
    by_part = {op.part: timings[op.id] for op in ops}
    assert by_part["I"].status == "pending"
    assert by_part["II"].status == "dated"
    assert by_part["II"].effective == date(2026, 6, 19)


def test_transitional_sentences_do_not_disturb_commencement() -> None:
    law = _law(
        "lov/2023-06-16-59",
        "Kongen bestemmer",
        "Loven gjelder fra den tid Kongen bestemmer. Endringene i del I av lov 14. juni 2019 "
        "nr. 99 om prøving §§ 1 og 2 gjelder når dødsfallet finner sted etter lovens "
        "ikrafttredelse. "
        "Departementet kan gi nærmere overgangsbestemmelser.",
    )
    ops, timings = _timings(law)
    assert {timings[op.id].status for op in ops} == {"pending"}


def test_commencement_sentence_outside_the_grammar_fails_closed() -> None:
    law = _law(
        "lov/2021-06-18-124",
        "2021-06-18, Kongen bestemmer",
        "Loven gjelder fra den tiden Kongen bestemmer. Endringene i offentleglova § 5 i del I "
        "i loven trer likevel i kraft straks.",
    )
    ops, timings = _timings(law)
    assert {timings[op.id].status for op in ops} == {"unresolved"}
    assert "outside the grammar" in timings[ops[0].id].reason


def test_retroactive_date_is_flagged_not_rejected() -> None:
    law = _law(
        "lov/2021-12-17-150", "2021-01-01", "Loven trer i kraft 1. januar 2021.", "2021-12-17 14:00"
    )
    ops, timings = _timings(law)
    assert timings[ops[0].id].effective == date(2021, 1, 1)
    assert timings[ops[0].id].retroactive is True


def test_partial_decision_outside_the_grammar_leaves_operations_unresolved() -> None:
    law = _law(
        "lov/2020-12-18-999", "Kongen bestemmer", "Loven gjelder fra den tid Kongen bestemmer."
    )
    decision = parse_document(
        (
            '<html><body><header><dl><dd class="refid">forskrift/2021-05-28-1</dd>'
            '<dd class="dateInForce">2022-01-01</dd>'
            '<dd class="dateOfPublication">2021-05-31 16:10</dd>'
            '<dd class="title">Delt ikrafttredelse av lov 18. desember 2020 nr. 999 om '
            'endringer</dd></dl></header><main><article class="legalP">Paragrafane 1 til 10 '
            "gjeld frå 1. januar 2022.</article></main></body></html>"
        ).encode(),
        "lti/2021/sf-x.xml",
    )
    ops, timings = _timings(law, (decision,))
    assert {timings[op.id].status for op in ops} == {"unresolved"}
    assert {timings[op.id].reason for op in ops} == {"partial decision outside the grammar"}


def test_clause_timings_without_clauses_falls_back_to_the_header() -> None:
    law = _law("lov/2022-06-22-81", "2022-06-22", "Departementet kan gi overgangsregler.")
    result = interpret(law)
    timings = clause_timings(law, (), result.operations)
    assert {t.effective for t in timings.values()} == {date(2022, 6, 22)}
