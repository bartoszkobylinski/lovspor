"""Observable boundaries from the supplied 102-survivor artifact."""

from datetime import date

import pytest
from lxml import etree

from lovspor.legaltime import archive, attribution, replay, scopes
from lovspor.legaltime import commencement as c
from lovspor.legaltime import instructions as ins
from lovspor.legaltime.text import section_sort_key
from tests.unit.test_legaltime_mutants import ACT, _law, _op
from tests.unit.test_legaltime_remediation import xml_doc


def test_index_standin_preserves_changes():
    record = archive.MemberRecord(
        name="d.xml",
        sha256="a" * 64,
        refid="forskrift/2022-01-01-1",
        title="Ikraftsetting av lov 14. juni 2019 nr. 99",
        date_in_force="",
        published_at=None,
        changes_to_documents=(ACT, "lov/2020-01-01-1"),
    )
    doc = archive._title_document(record)
    assert doc.header.changes_to_documents == record.changes_to_documents
    index = archive.ArchiveIndex(
        archive="a", sha256="a" * 64, index_version=archive.INDEX_VERSION, members=(record,)
    )
    assert archive._decision_names(index, {ACT}) == ["d.xml"]
    title_only = index.model_copy(
        update={"members": (record.model_copy(update={"changes_to_documents": ()}),)}
    )
    assert archive._decision_names(title_only, {ACT}) == ["d.xml"]
    assert archive._decision_names(index, {"lov/1900-01-01-1"}) == []


def test_cited_date_exposes_adjacent_clause_date():
    doc = xml_doc("")
    assert c._clause_date(doc, "Loven trer i kraft lov 1. januar 20201. juli 2022") == date(
        2022, 7, 1
    )


def test_reference_removal_preserves_information():
    assert c._informational("Se reglene etter at loven trer i kraft.")
    # Removing the reference exposes a remaining commencement phrase even
    # when malformed source prose omits the separating space.
    assert not c._informational("i etter at loven trer i kraftkraft")
    assert not c._informational(
        "Se reglene etter at loven trer i kraft; loven trer i kraft straks."
    )


def test_amendment_in_clause_does_not_supply_timing():
    doc = _law("lov/2020-01-01-1", "2022-01-01", "")
    reading = c._ClauseReading()
    reading.read(doc, "I lov 14. juni 2019 nr. 99 gjøres følgende endringer:")
    reading.read(doc, "Nåværende første ledd blir nytt annet ledd fra 1. juli 2022.")
    assert reading.failure is None
    assert reading.default is None
    assert reading.exceptions == {}


def test_non_decision_and_missing_resolution_date():
    doc = xml_doc("", '<dd class="title">Lov om prøving</dd>')
    assert c.is_decision_for(doc, ACT) is False
    assert (
        c._own_date(
            doc.model_copy(
                update={"header": doc.header.model_copy(update={"refid": "forskrift/unknown"})}
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "sentences",
    [
        ["Loven trer i kraft straks og 1. juli 2022."],
        ["Loven trer i kraft 1. juli 2022.", "Loven trer i kraft 1. august 2022."],
    ],
)
def test_full_decision_reports_multiple_dates(sentences):
    assert c._block_date(xml_doc(""), sentences) == "full decision body names several dates"


def test_block_without_body_title_uses_header():
    doc = xml_doc(
        '<article class="legalP">Loven trer i kraft 1. juli 2022.</article>',
        '<dd class="title">Ikraftsetting av lov 14. juni 2019 nr. 99</dd>',
    )
    block = c._block_for(doc, ACT)
    assert block is not None
    assert block.title == doc.header.title
    assert block.sentences == ("Loven trer i kraft 1. juli 2022.",)


def test_partial_block_title_requires_readable_scopes():
    law = _law(ACT, "Kongen bestemmer", "")
    decision = xml_doc(
        '<article class="legalP">Delt ikraftsetting av lov 14. juni 2019 nr. 99.</article>'
        '<article class="legalP">Loven trer i kraft 1. juli 2022.</article>',
        '<dd class="title">Ikraftsetting av flere lover</dd>',
    )
    parsed = c._Decision(decision, law, True)
    assert parsed.partial is True
    assert parsed.unreadable == "partial decision outside the grammar"
    full = c._Decision(xml_doc("", '<dd class="dateInForce">2022-01-01</dd>'), law, True)
    assert full.partial is False
    assert full.scopes is None


def test_numbered_noncontiguous_ledd_is_not_a_range():
    kind, groups = ins.classify("§ 1 (1) og (3) skal lyde:")
    assert kind == "replace_ledd"
    assert ins._address(groups).ledd is None
    assert ins._address(groups).ledd_end is None


def test_numbered_ledd_does_not_join_sentence_range():
    kind, groups = ins.classify("§ 1 (1) og (2) første til tredje punktum skal lyde:")
    assert kind == "replace_punktum"
    assert ins._address(groups).punktum == 1
    assert ins._address(groups).punktum_end == 3


def test_equal_repeal_range_is_one_section():
    assert ins._expand("§§ 2 til 2 oppheves.") == ["§ 2 oppheves."]


def test_intro_and_payload_are_consumed():
    doc = xml_doc(
        '<section><h2>I</h2><article class="defaultP">'
        "I lov 14. juni 2019 nr. 99 skal § 1 lyde:</article>"
        '<article class="legalP">Loven trer i kraft 1. juli 2022.</article></section>'
    )
    got = ins.interpret(doc)
    assert len(got.operations) == 1
    assert got.operations[0].ledd[0].text == "Loven trer i kraft 1. juli 2022."
    assert got.clauses == ()


def test_capital_chapter_section_sort_key():
    assert section_sort_key("§6A-7") == (6, 7, "A")


@pytest.mark.parametrize(
    "numeral,expected",
    [
        ("I", 1),
        ("V", 5),
        ("X", 10),
        ("L", 50),
        ("C", 100),
        ("IV", 4),
        ("IX", 9),
        ("XIV", 14),
        ("XLI", 41),
    ],
)
def test_roman_scope_bounds(numeral, expected):
    assert scopes._roman(numeral) == expected


@pytest.mark.parametrize(
    "listing,expected", [("I til IV", ["I", "II", "III", "IV"]), ("I og IV", ["I", "IV"])]
)
def test_part_list_distinguishes_range_from_two_parts(listing, expected):
    assert scopes._part_labels(listing) == expected


@pytest.mark.parametrize(
    "sentence",
    ["Endringsloven del I trer i kraft straks.", "De andre bestemmelsene trer i kraft straks."],
)
def test_scopes_use_resolution_day(sentence):
    got = scopes._scopes_of(sentence, date(2022, 7, 1))
    assert got is not None and [s.when for s in got] == [date(2022, 7, 1)]


def test_undated_remainder_is_unreadable():
    assert scopes._scopes_of("De andre bestemmelsene trer i kraft senere.", None) is None


@pytest.mark.parametrize(
    "tail", ["§ 1 første ledd", "§ 1 og § 2", "§§ 1", "§ ukjent", "første ledd § 1"]
)
def test_partial_scope_does_not_widen_unrepresentable_section(tail):
    sentence = (
        f"Endringsloven del I, endringene i lov 14. juni 2019 nr. 99 {tail}, "
        "trer i kraft 1. juli 2022."
    )
    assert scopes._scopes_of(sentence, None) is None


def _item_working():
    return replay._Working(
        xml_doc(
            '<article class="legalArticle" data-name="§1">'
            '<article class="legalP"><ul><li data-name="a.">First</li>'
            '<li data-name="b.">Second</li></ul></article>'
            '<article class="legalP">Untouched</article></article>'
        )
    )


@pytest.mark.parametrize("kind", ["insert_bokstav", "repeal_bokstav"])
def test_item_without_ledd_refusal_names_item(kind):
    working = _item_working()
    before = list(working.map["§1"].ledd)
    assert (
        getattr(working, f"apply_{kind}")(
            _op(kind, ins.Address(section="§1", bokstav="c"), ("New",))
        )
        == "item without ledd on a provision with several ledd"
    )
    assert working.map["§1"].ledd == before


@pytest.mark.parametrize(
    "kind,expected", [("insert_bokstav", "First Second New"), ("repeal_bokstav", "Second")]
)
def test_item_edit_changes_only_selected_ledd(kind, expected):
    working = _item_working()
    op = _op(
        kind,
        ins.Address(section="§1", ledd=1, bokstav="c" if kind == "insert_bokstav" else "a"),
        ("New",),
    )
    assert getattr(working, f"apply_{kind}")(op) is None
    assert [p.text for p in working.map["§1"].ledd] == [expected, "Untouched"]


def test_item_repeal_without_label_does_not_invent_one():
    working = _item_working()
    assert (
        working.apply_repeal_bokstav(_op("repeal_bokstav", ins.Address(section="§1", ledd=1)))
        == "bokstav  matched 0 list items"
    )


@pytest.mark.parametrize(
    "label,expected", [("1", None), ("2", "1"), ("3", "2"), ("a", None), ("b", "a"), ("", None)]
)
def test_item_predecessor(label, expected):
    assert replay._previous_label(label) == expected


def test_item_selection_ignores_other_tags_and_preserves_x():
    root = etree.fromstring(
        '<article><span data-name="X.">No</span><li/><li data-name="X.">Yes</li></article>'
    )
    assert [e.text for e in replay._items(root, "X")] == ["Yes"]
    assert replay._items(root, "XXXX") == []


@pytest.mark.parametrize("tag", ["ul", "ol"])
def test_first_item_inserted_before_existing_items(tag):
    result = replay._insert_item(f'<{tag}><li data-name="b.">Second</li></{tag}>', "a", "First")
    assert not isinstance(result, str)
    assert result.text == "First Second"
    assert result.xml.index('data-name="a."') < result.xml.index('data-name="b."')


def test_numeric_item_inserted_after_predecessor():
    result = replay._insert_item(
        '<ol><li data-name="1.">First</li><li data-name="3.">Third</li></ol>', "2", "Second"
    )
    assert not isinstance(result, str)
    assert result.text == "First Second Third"


def test_item_parsers_keep_formatting_blanks():
    """The space between two inline elements is text ("A X", not "AX"): item edits keep it."""
    inserted = replay._insert_item('<ul> <li data-name="b.">B</li> </ul>', "a", "<b>A</b> <i>X</i>")
    assert not isinstance(inserted, str)
    assert inserted.xml == (
        '<ul> <li data-name="a."><article class="listArticle">'
        '<article class="legalP"><b>A</b> <i>X</i></article></article></li>'
        '<li data-name="b.">B</li> </ul>'
    )
    assert inserted.text == "A X B"
    removed = replay._remove_item(
        '<ul> <li data-name="a.">A</li> <li data-name="b.">B</li> </ul>', "a"
    )
    assert not isinstance(removed, str)
    assert removed.xml == '<ul> <li data-name="b.">B</li> </ul>'
    item = replay._item_element("a", "<b>A</b> <i>X</i>")
    assert not isinstance(item, str)
    assert etree.tostring(item, encoding=str) == (
        '<li data-name="a."><article class="listArticle">'
        '<article class="legalP"><b>A</b> <i>X</i></article></article></li>'
    )


@pytest.mark.parametrize("xml", ["Lead<b>A</b><i>B</i>", "<b>A</b><i>B</i>"])
def test_edited_ledd_preserves_text_and_children(xml):
    result = replay._ledd_from_root(etree.fromstring(f"<article>{xml}</article>"))
    assert result.xml == xml


def test_whole_part_scope_without_section_list():
    sentence = "Romertall I trer i kraft 1. juli 2022."
    assert scopes._scopes_of(sentence, None) == [
        scopes.Scope(part="I", when=date(2022, 7, 1), quote=sentence)
    ]


def test_intro_own_repeal_and_inline_repeal():
    for sentence, expected in [
        ("I lov 14. juni 2019 nr. 99 oppheves.", "lov 14. juni 2019 nr. 99 oppheves"),
        ("I lov 14. juni 2019 nr. 99 oppheves § 2.", "§ 2 oppheves."),
    ]:
        match = attribution.INTRO.match(sentence)
        assert match is not None
        assert ins._intro_rest(match) == expected
