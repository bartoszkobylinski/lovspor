"""Phase 3 (#744): instruction shapes behind straffeloven's commencement chain.

Shapes seen in the permitted archives (lov 7. mars 2008 nr. 4, lov 19. juni
2009 nr. 74, lov 19. juni 2015 nr. 65); the text here is synthetic.
"""

from datetime import date

from lovspor.legaltime.commencement import resolve_timings
from lovspor.legaltime.document import Node, parse_document
from lovspor.legaltime.instructions import _Reader, interpret

ACT = "lov/2005-05-20-28"


def _law(body: str, refid: str = "lov/2008-03-07-4", date_in_force: str = "2008-03-07") -> str:
    return (
        f'<html><body><header><dl><dd class="refid">{refid}</dd>'
        f'<dd class="dateInForce">{date_in_force}</dd>'
        '<dd class="dateOfPublication">2008-03-07 14:45</dd><dd class="title">Lov om endringer</dd>'
        f"</dl></header><main>{body}</main></body></html>"
    )


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _title(text: str) -> str:
    return f'<article class="futuretitle">{text}</article>'


def _future(key: str, text: str = "T.") -> str:
    return (
        f'<article class="futureLegalArticle" data-name="{key}">'
        '<span class="futureLegalArticleHeader">'
        f'<span class="legalArticleValue">§ {key[1:]}</span></span>'
        f'<article class="legalP">{text}</article></article>'
    )


def test_an_endres_slik_intro_names_the_act_under_amendment() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        '<article class="legalP">Lov 20. mai 2005 nr. 28 om straff endres slik:</article>'
        + _p("§ 29 nytt annet ledd skal lyde:")
        + '<article class="legalP">Ny.</article>'
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.kind, op.act, op.address.section) for op in operations] == [
        ("insert_ledd", ACT, "§29")
    ]


def test_a_part_rewrite_inserts_each_provision_with_its_chapter_and_part() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        '<article class="legalP">Lov 20. mai 2005 nr. 28 om straff endres slik:</article>'
        + _p("Annen del skal lyde:")
        + _title("Annen del. De straffbare handlingene")
        + _title("Kapittel 16. Folkemord, forbrytelse mot menneskeheten og krigsforbrytelse")
        + _future("§101")
        + _future("§102")
        + _title("Kapittel 17. Vern av Norges selvstendighet")
        + _future("§111")
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    placed = [
        (op.kind, op.address.section, op.new_provision.chapter, op.new_provision.part)
        for op in operations
        if op.new_provision is not None
    ]
    assert placed == [
        ("insert_section", "§101", "kap16", "del2"),
        ("insert_section", "§102", "kap16", "del2"),
        ("insert_section", "§111", "kap17", "del2"),
    ]
    assert {op.act for op in operations} == {ACT}


def test_new_chapters_placed_in_a_part_carry_chapter_and_part() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        '<article class="legalP">Lov 20. mai 2005 nr. 28 om straff endres slik:</article>'
        + _p("Nye kapitler 21 til 31 plasseres i annen del, og skal lyde:")
        + _title("Kapittel 21. Vern av informasjon")
        + _future("§201")
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.new_provision.chapter, op.new_provision.part) for op in operations] == [
        ("kap21", "del2")
    ]


def test_several_new_chapters_without_titles_stamp_no_chapter() -> None:
    """``Nye kapitler 6 til 8`` names a run, not one chapter: unplaced until a title names one."""
    body = (
        '<section class="section"><h2>I</h2>'
        '<article class="legalP">Lov 20. mai 2005 nr. 28 om straff endres slik:</article>'
        + _p("Nye kapitler 6 til 8 plasseres i annen del, og skal lyde:")
        + _future("§61")
        + _title("Kapittel 7. B")
        + _future("§71")
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.new_provision.chapter, op.new_provision.part) for op in operations] == [
        (None, "del2"),
        ("kap7", "del2"),
    ]


def test_a_single_new_chapter_takes_its_chapter_from_the_sentence() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        + _p("I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:")
        + _p("Nytt kapittel 6A skal lyde:")
        + _future("§6A-1")
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.new_provision.chapter, op.new_provision.part) for op in operations] == [
        ("kap6a", None)
    ]


def test_a_lettered_new_chapter_inserts_its_spaced_labels_under_the_chapter() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        + _p("I lov 18. juni 2021 nr. 97 om barnevern gjøres følgende endringer:")
        + _p("Nytt kapittel 9 A skal lyde:")
        + _title("Kapittel 9 A Kommunale botiltak med døgnbemanning")
        + '<article class="futureLegalArticle" data-name="§9A-1">'
        '<span class="futureLegalArticleHeader"><span class="legalArticleValue">§ 9 A-1</span>'
        "</span>"
        '<article class="legalP">T.</article></article>' + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.kind, op.address.section, op.new_provision.chapter) for op in operations] == [
        ("insert_section", "§9A-1", "kap9a")
    ]


def test_a_conditional_renumbering_that_opens_a_payload_is_an_instruction_not_a_clause() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        '<article class="legalP">Lov 20. mai 2005 nr. 28 om straff endres slik:</article>'
        + _p(
            "De delene av § 102 i lov 20. mai 2005 nr. 28 som ikke allerede er satt i kraft, "
            "blir § 402, og skal lyde slik:"
        )
        + _future("§402")
        + '</section><section class="section"><h2>II</h2>'
        '<article class="legalP">Loven trer i kraft straks.</article></section>'
    )
    reading = interpret(parse_document(_law(body).encode(), "lti/x.xml"))
    assert reading.clauses == ("Loven trer i kraft straks.",)
    assert [op.kind for op in reading.operations] == ["unsupported"]


def _instrument() -> str:
    """Straffelovens ikraftsettingslov: § 1 amends act A's § 411; § 8 is its own clause."""
    return _law(
        '<article class="legalArticle" data-name="§1"><h3 class="legalArticleHeader">'
        '<span class="legalArticleValue">§ 1</span>. <span class="legalArticleTitle">'
        "Ikraftsetting av straffeloven 2005</span></h3>"
        '<article class="legalP">I lov 20. mai 2005 nr. 28 om straff skal § 411 første ledd '
        "lyde:</article>"
        '<article class="legalP">Loven trer i kraft 1. oktober 2015. Fra samme tid oppheves '
        "straffeloven 22. mai 1902 nr. 10.</article></article>"
        '<article class="legalArticle" data-name="§8"><h3 class="legalArticleHeader">'
        '<span class="legalArticleValue">§ 8</span>. <span class="legalArticleTitle">'
        "Ikrafttredelse</span></h3>"
        '<article class="legalP">Loven her trer i kraft straks.</article></article>',
        refid="lov/2015-06-19-65",
        date_in_force="2015-06-19",
    )


def test_an_instrument_commencing_another_act_has_one_clause_of_its_own() -> None:
    law = parse_document(_instrument().encode(), "lti/2015/b.xml")
    reading = interpret(law)
    assert reading.clauses == ("Loven her trer i kraft straks.",)
    assert [
        (op.kind, op.act, op.address.section, op.address.ledd) for op in reading.operations
    ] == [("replace_ledd", ACT, "§411", 1)]
    timing = resolve_timings(law, reading.clauses, reading.operations, ())[reading.operations[0].id]
    assert (timing.status, timing.effective) == ("dated", date(2015, 6, 19))


def test_a_range_shift_after_a_first_ledd_rewrite_is_an_insertion() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        + _p("I lov 18. juni 2021 nr. 97 om barnevern gjøres følgende endringer:")
        + _p("§ 13-5 første ledd skal lyde:")
        + '<article class="legalP">Ny første.</article>'
        + _p("Nåværende første til femte ledd blir annet til sjette ledd.")
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.kind, op.address.ledd, op.absorbed_by) for op in operations] == [
        ("insert_ledd", 1, None),
        ("renumber", None, operations[0].id),
    ]


def test_a_range_shift_that_skips_a_number_is_not_an_insertion() -> None:
    body = (
        '<section class="section"><h2>I</h2>'
        + _p("I lov 18. juni 2021 nr. 97 om barnevern gjøres følgende endringer:")
        + _p("§ 13-5 første ledd skal lyde:")
        + '<article class="legalP">Ny første.</article>'
        + _p("Nåværende annet til femte ledd blir tredje til sjette ledd.")
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.kind, op.absorbed_by) for op in operations] == [
        ("replace_ledd", None),
        ("renumber", None),
    ]


def test_a_one_sentence_term_substitution_names_its_locations() -> None:
    body = (
        '<section class="section"><h2>II</h2>'
        + _p("I lov 18. juni 2021 nr. 97 om barnevern gjøres følgende endringer:")
        + _p(
            "Ordet «atferdsinstitusjon» skal endres til «barnevernsinstitusjon» i § 1-2 tredje "
            "og fjerde ledd, § 5-1 første ledd bokstav d og § 10-4."
        )
        + "</section>"
    )
    operations = interpret(parse_document(_law(body).encode(), "lti/x.xml")).operations
    assert [(op.kind, op.act, op.term_old, op.term_new) for op in operations] == [
        ("substitute_term", ACT.replace("2005-05-20-28", "2021-06-18-97"), "atferdsinstitusjon",
         "barnevernsinstitusjon"),
    ]  # fmt: skip
    assert operations[0].locations.startswith("§ 1-2 tredje og fjerde ledd")


def test_an_intro_alone_consumes_no_payload() -> None:
    """``_paragraph`` answers False for an intro alone: the nodes after it are not its payload."""
    law = parse_document(
        _law('<section class="section"><h2>I</h2></section>').encode(), "lti/x.xml"
    )
    reader = _Reader(law)
    intro = Node(
        kind="paragraph",
        css_class="defaultP",
        text="I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:",
    )
    payload = (Node(kind="ledd", css_class="legalP", text="Loose."),)
    assert reader._paragraph(("I", "0:I"), 0, intro, payload) is False
    assert reader.act == ACT and reader.operations == []
    slik = Node(
        kind="ledd", css_class="legalP", text="Lov 20. mai 2005 nr. 28 om straff endres slik:"
    )
    assert reader._paragraph(("I", "0:I"), 1, slik, payload) is False
    instruction = Node(kind="paragraph", css_class="defaultP", text="§ 1 skal lyde:")
    assert reader._paragraph(("I", "0:I"), 2, instruction, ()) is True
