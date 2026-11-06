"""Instruction grammar: every supported operation shape, and the refusal of the rest."""

from pathlib import Path

import pytest

from lovspor.legaltime.document import parse_document
from lovspor.legaltime.instructions import Address, _address, classify, interpret

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"


def _xml(refid: str, label: str, inner: str) -> bytes:
    return (
        f'<html><body><header><dl><dd class="refid">{refid}</dd></dl></header>'
        f'<main><section class="section"><h2>{label}</h2>{inner}</section></main></body></html>'
    ).encode()


@pytest.mark.parametrize(
    ("sentence", "kind", "address"),
    [
        ("§ 89 skal lyde:", "replace_section", Address(section="§89")),
        ("§ 2-4 b skal lyde:", "replace_section", Address(section="§2-4b")),
        ("Ny § 88 a skal lyde:", "insert_section", Address(section="§88a")),
        ("ny § 12 a lyde:", "insert_section", Address(section="§12a")),
        ("§ 118 syvende ledd skal lyde:", "replace_ledd", Address(section="§118", ledd=7)),
        ("§ 3 andre ledd skal lyde:", "replace_ledd", Address(section="§3", ledd=2)),
        (
            "§ 178 tredje til syvende ledd lyde:",
            "replace_ledd",
            Address(section="§178", ledd=3, ledd_end=7),
        ),
        ("§ 92 nytt annet ledd skal lyde:", "insert_ledd", Address(section="§92", ledd=2)),
        (
            "§ 116 nytt fjerde og femte ledd skal lyde:",
            "insert_ledd",
            Address(section="§116", ledd=4, ledd_end=5),
        ),
        (
            "§ 92 første ledd første punktum skal lyde:",
            "replace_punktum",
            Address(section="§92", ledd=1, punktum=1),
        ),
        (
            "§ 77 annet og tredje punktum skal lyde:",
            "replace_punktum",
            Address(section="§77", punktum=2, punktum_end=3),
        ),
        (
            "§ 96 fjerde ledd nytt tredje punktum lyde:",
            "insert_punktum",
            Address(section="§96", ledd=4, punktum=3),
        ),
        (
            "§ 20 nytt tredje punktum skal lyde:",
            "insert_punktum",
            Address(section="§20", punktum=3),
        ),
        (
            "§ 3 andre ledd bokstav d lyde:",
            "replace_bokstav",
            Address(section="§3", ledd=2, bokstav="d"),
        ),
        ("§ 4 oppheves.", "repeal_section", Address(section="§4")),
        ("§ 3 tredje ledd blir oppheva.", "repeal_ledd", Address(section="§3", ledd=3)),
        (
            "§ 20 første ledd annet punktum oppheves.",
            "repeal_punktum",
            Address(section="§20", ledd=1, punktum=2),
        ),
        (
            "§ 43 annet ledd blir nytt femte ledd i § 42.",
            "move_ledd",
            Address(section="§43", ledd=2),
        ),
        ("Nåværende tredje punktum blir annet punktum.", "renumber", Address()),
        ("§ 10 overskriften skal lyde:", "replace_heading", Address(section="§10")),
    ],
)
def test_supported_sentences(sentence: str, kind: str, address: Address) -> None:
    found, groups = classify(sentence)
    assert found == kind
    assert _address(groups) == address


def test_move_destination_is_read() -> None:
    body = _xml(
        "lov/2020-12-18-149",
        "I",
        '<article class="defaultP">I lov 14. juni 2019 nr. 21 om arv gjøres følgende endringer:'
        '</article><article class="defaultP">§ 43 annet ledd blir nytt femte ledd i § 42.'
        "</article>",
    )
    op = interpret(parse_document(body, "x")).operations[0]
    assert (op.kind, op.address, op.destination) == (
        "move_ledd",
        Address(section="§43", ledd=2),
        "§42",
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Kapittel 15 skal lyde:",
        "Lovens tittel skal lyde:",
        "§ 5 første ledd skal lyde slik som Kongen bestemmer",
        "Loven trer i kraft straks.",
        "§ 116 femte ledd gjelder tilsvarende.",
    ],
)
def test_other_sentences_are_unsupported(sentence: str) -> None:
    assert classify(sentence)[0] == "unsupported"


def test_non_adjacent_pair_is_not_a_range() -> None:
    kind, groups = classify("§ 5 første og tredje ledd skal lyde:")
    assert kind == "replace_ledd"
    assert _address(groups) == Address(section="§5", ledd=1, ledd_end=None)


def test_amending_fixture_yields_ordered_operations_with_payloads() -> None:
    document = parse_document((FIXTURES / "amending.xml").read_bytes(), "amending.xml")
    result = interpret(document)
    assert result.document == "lov/2020-12-18-999"
    kinds = [(op.kind, op.act, op.address.section) for op in result.operations]
    assert kinds == [
        ("replace_punktum", "lov/2019-06-14-99", "§1"),
        ("insert_ledd", "lov/2019-06-14-99", "§1"),
        ("insert_section", "lov/2019-06-14-99", "§2a"),
        ("repeal_section", "lov/2019-06-14-99", "§4"),
        ("replace_section", "lov/2019-06-14-99", "§3"),
        ("replace_ledd", "lov/1967-02-10", "§5"),
    ]
    first, second, third, _, fifth, sixth = result.operations
    assert [ledd.text for ledd in first.ledd] == ["Annet ledd nytt annet punktum."]
    assert second.address == Address(section="§1", ledd=3)
    assert third.new_provision is not None
    assert third.new_provision.heading == "Innskutt"
    assert fifth.new_provision is not None
    assert len(fifth.new_provision.ledd) == 2
    assert sixth.change_part == ("lov/1967-02-10/§5/ledd/1",)
    assert first.id == "lov/2020-12-18-999#I:1.0"
    assert result.clauses == (
        "Loven trer i kraft straks. Ny § 2 a i prøveloven trer likevel i kraft fra den tid "
        "Kongen bestemmer.",
    )


def test_global_term_substitution_lists_locations_per_act() -> None:
    body = _xml(
        "lov/2021-05-07-34",
        "I",
        '<article class="defaultP">I følgjande lovføresegner vert «fylkesmannen» endra til '
        "«statsforvalteren»:</article>"
        '<article class="defaultP"><strong>1</strong>. lov 14. juni 2019 nr. 21 om arv</article>'
        '<article class="defaultP">§ 11, § 14 andre ledd</article>'
        '<article class="legalP">lov 30. juni 1916 nr. 1 om kommisjon § 47</article>',
    )
    ops = interpret(parse_document(body, "x")).operations
    assert [(op.kind, op.act, op.term_old, op.term_new, op.locations) for op in ops] == [
        (
            "substitute_term",
            "lov/2019-06-14-21",
            "fylkesmannen",
            "statsforvalteren",
            "§ 11, § 14 andre ledd",
        ),
        ("substitute_term", "lov/1916-06-30-1", "fylkesmannen", "statsforvalteren", "§ 47"),
    ]


def test_paired_term_substitution_yields_two_operations() -> None:
    body = _xml(
        "lov/2026-06-19-48",
        "VI",
        '<article class="defaultP">I følgende bestemmelser skal ordene «a» og «b» endres til '
        "henholdsvis «c» og «d»:</article>"
        '<article class="legalP">lov 14. juni 2019 nr. 21 om arv § 103</article>',
    )
    ops = interpret(parse_document(body, "x")).operations
    assert [(op.term_old, op.term_new) for op in ops] == [("a", "c"), ("b", "d")]
    assert {op.act for op in ops} == {"lov/2019-06-14-21"}


def test_instruction_carried_as_ledd_is_still_read() -> None:
    body = _xml(
        "lov/2018-12-20-116",
        "I",
        '<article class="legalP">I lov 15. juni 2018 nr. 38 om behandling skal ny § 12 a lyde:'
        '</article><article class="futureLegalArticle" data-name="§12a">'
        '<article class="legalP">Tekst.</article></article></section>'
        '<section class="section"><h2>II</h2>'
        '<article class="legalP">Loven trer i kraft fra den tiden Kongen bestemmer.</article>',
    )
    result = interpret(parse_document(body, "x"))
    assert [(op.kind, op.act, op.address.section) for op in result.operations] == [
        ("insert_section", "lov/2018-06-15-38", "§12a")
    ]
    assert result.operations[0].new_provision is not None
    assert result.clauses == ("Loven trer i kraft fra den tiden Kongen bestemmer.",)


def test_unsupported_instruction_keeps_act_and_payload() -> None:
    body = _xml(
        "lov/2024-06-21-46",
        "I",
        '<article class="defaultP">I lov 12. mai 2022 nr. 28 om advokater gjøres følgende '
        'endringer:</article><article class="defaultP">Kapittel 15 skal lyde:</article>'
        '<article class="futureLegalArticle" data-name="§72"><article class="legalP">A.</article>'
        "</article>",
    )
    ops = interpret(parse_document(body, "x")).operations
    assert len(ops) == 1
    assert ops[0].kind == "unsupported"
    assert ops[0].act == "lov/2022-05-12-28"
    assert ops[0].address == Address()
    assert ops[0].new_provision is not None


def test_two_sentence_instruction_yields_two_operations_payload_on_the_last() -> None:
    body = _xml(
        "lov/2025-06-20-97",
        "I",
        '<article class="defaultP">I lov 21. mai 2021 nr. 42 om språk skal desse endringane '
        'gjerast:</article><article class="defaultP">§ 3 tredje ledd blir oppheva. Noverande '
        "fjerde og femte ledd blir tredje og fjerde ledd.</article>"
        '<article class="defaultP">§ 13 fjerde ledd skal lyde:</article>'
        '<article class="legalP">Ny tekst.</article>',
    )
    ops = interpret(parse_document(body, "x")).operations
    assert [(op.kind, op.id) for op in ops] == [
        ("repeal_ledd", "lov/2025-06-20-97#I:1.0"),
        ("renumber", "lov/2025-06-20-97#I:1.1"),
        ("replace_ledd", "lov/2025-06-20-97#I:2.0"),
    ]
    assert ops[0].ledd == ()
    assert [ledd.text for ledd in ops[2].ledd] == ["Ny tekst."]


def test_replacement_followed_by_a_shift_renumbering_is_an_insertion() -> None:
    body = _xml(
        "lov/2023-06-16-59",
        "I",
        '<article class="defaultP">I lov 14. juni 2019 nr. 21 om arv gjøres følgende endringer:'
        '</article><article class="defaultP">§ 118 syvende ledd skal lyde:</article>'
        '<article class="legalP">Nytt syvende.</article>'
        '<article class="defaultP">Nåværende syvende ledd blir nytt åttende ledd.</article>'
        '<article class="defaultP">§ 139 annet ledd skal lyde:</article>'
        '<article class="legalP">Erstattet.</article>'
        '<article class="defaultP">Nåværende tredje ledd blir fjerde ledd.</article>',
    )
    ops = interpret(parse_document(body, "x")).operations
    assert [(op.kind, op.address.section, op.address.ledd) for op in ops] == [
        ("insert_ledd", "§118", 7),
        ("renumber", None, None),
        ("replace_ledd", "§139", 2),
        ("renumber", None, None),
    ]


def test_act_context_does_not_leak_across_parts() -> None:
    body = _xml(
        "lov/2026-06-19-48",
        "IV",
        '<article class="defaultP">I lov 14. juni 2019 nr. 21 om arv skal § 178 lyde:</article>'
        '<article class="futureLegalArticle" data-name="§178"><article class="legalP">T.</article>'
        '</article></section><section class="section"><h2>V</h2>'
        '<article class="defaultP">I midlertidig lov 7. mai 2020 nr. 38 om rekonstruksjon '
        "oppheves.</article>",
    )
    ops = interpret(parse_document(body, "x")).operations
    assert [(op.kind, op.act) for op in ops] == [("replace_section", "lov/2019-06-14-21")]


def test_inline_replacement_text_after_lyde_becomes_the_payload() -> None:
    body = _xml(
        "lov/2022-12-20-115",
        "X",
        '<article class="defaultP">I lov 21. mai 2021 nr. 42 om språk skal § 3 andre ledd bokstav '
        "d lyde: Stortinget, Riksrevisjonen og andre organ for Stortinget</article>",
    )
    ops = interpret(parse_document(body, "x")).operations
    assert [(op.kind, op.address.bokstav, op.instruction) for op in ops] == [
        ("replace_bokstav", "d", "§ 3 andre ledd bokstav d lyde:")
    ]
    assert [ledd.text for ledd in ops[0].ledd] == [
        "Stortinget, Riksrevisjonen og andre organ for Stortinget"
    ]


def test_section_prefixed_renumbering_also_turns_the_replacement_into_an_insertion() -> None:
    body = _xml(
        "lov/2025-06-20-97",
        "I",
        '<article class="defaultP">I lov 21. mai 2021 nr. 42 om språk skal desse endringane '
        'gjerast:</article><article class="defaultP">§ 13 fjerde ledd skal lyde:</article>'
        '<article class="legalP">Nytt.</article>'
        '<article class="defaultP">§ 13 noverande fjerde ledd blir nytt femte ledd.</article>'
        '<article class="defaultP">§ 14 fjerde ledd skal lyde:</article>'
        '<article class="legalP">Erstatning.</article>'
        '<article class="defaultP">§ 15 noverande fjerde ledd blir nytt femte ledd.</article>',
    )
    ops = interpret(parse_document(body, "x")).operations
    assert [(op.kind, op.address.section) for op in ops] == [
        ("insert_ledd", "§13"),
        ("renumber", "§13"),
        ("replace_ledd", "§14"),
        ("renumber", "§15"),
    ]


def test_enacting_act_with_embedded_amendments_yields_operations_and_its_own_clause() -> None:
    document = parse_document((FIXTURES / "enacting-embedded.xml").read_bytes(), "x")
    result = interpret(document)
    assert [(op.kind, op.act, op.address.section, op.change_part) for op in result.operations] == [
        ("replace_ledd", "lov/2021-06-18-121", "§26", ("lov/2021-06-18-121/§26/ledd/6",)),
        ("replace_section", "lov/2007-12-14-116", "§11", ("lov/2007-12-14-116/§11",)),
    ]
    assert result.clauses == ("Lova gjeld frå den tida Kongen fastset.",)
    assert [p.key for p in document.provisions] == ["§1", "§24", "§25"]
