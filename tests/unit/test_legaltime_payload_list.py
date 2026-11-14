"""#907: numbered items inside a replacement provision's payload never rebind the outer block.

The shapes are quoted from ``lti/2009/nl-20090619-074.xml`` (``lov/2009-06-19-74``, sha256
``7ec6a1b4…``) part I: "Nåværende § 401 blir § 411 som innleder tredje del og som skal
lyde:", § 411 whose last ledd ends "… gjøres følgende endringer i andre lover:", the items
1-3 as flat ``defaultP`` siblings, then "Nåværende § 402 blir § 412 og skal lyde:"
(straffeloven's own); and § 412's item 18, "I lov … skal § 4 annet ledd lyde:", followed by
"§ 4 annet ledd annet punktum oppheves." (item 18's own). One shape on both sides of the
list's end: neither is bound to a guessed act. The markup is synthetic.
"""

import pytest

from lovspor.legaltime.document import Document, Node, parse_document
from lovspor.legaltime.instructions import Operation, interpret
from lovspor.legaltime.payload_list import Listing, host_list

STRAFFELOVEN = "lov/2005-05-20-28"
UTLEVERING = "lov/1975-06-13-39"
AML = "lov/2005-06-17-62"
INTRO = "Lov 20. mai 2005 nr. 28 om straff endres slik:"
STRAKS = "Loven trer i kraft straks."
LIST_LEDD = "Fra den tid kapittel 16 tar til å gjelde, gjøres følgende endringer i andre lover:"
RENAME_411 = "Nåværende § 401 blir § 411 som innleder tredje del og som skal lyde:"
RENAME_412 = "Nåværende § 402 blir § 412 og skal lyde:"
ITEM_1 = "1. Militær straffelov 22. mai 1902 nr. 13 § 107 oppheves."
ITEM_2 = "2. Lov 13. desember 1946 nr. 14 om straff for utlendske krigsbrotsmenn oppheves."
ITEM_3 = (
    "3. I lov 13. juni 1975 nr. 39 om utlevering av lovbrytere mv. skal § 9 annet punktum lyde:"
)
ITEM_18 = (
    "18. I lov 17. juni 1932 nr. 6 om kvalitetskontroll med landbruksvarer m.v. skal § 4 annet"
    " ledd lyde:"
)
ITEM_18_REST = "§ 4 annet ledd annet punktum oppheves."
ITEM_AML = (
    "4. I lov 17. juni 2005 nr. 62 om arbeidsmiljø, arbeidstid og stillingsvern mv. gjøres "
    "følgende endringer:"
)


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _ledd(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _future(key: str, *ledd: str) -> str:
    return (
        f'<article class="futureLegalArticle" data-name="{key}">'
        '<span class="futureLegalArticleHeader">'
        f'<span class="legalArticleValue">§ {key[1:]}</span>'
        '<span class="legalArticleTitle">Ikraftsetting</span></span>'
        f"{''.join(_ledd(text) for text in ledd or ('Teksten.',))}</article>"
    )


HOST_411 = _p(RENAME_411) + _future("§411", "Ikraftsetting fastsettes ved lov.", LIST_LEDD)
ITEMS = _p(ITEM_1) + _p(ITEM_2) + _p(ITEM_3) + _ledd("Utlevering kan likevel skje.")
RENAME = _p(RENAME_412) + _future("§412")


def _document(part_one: str, part_two: str) -> Document:
    changes = "".join(f"<li>{a}</li>" for a in (STRAFFELOVEN, AML))
    body = (
        f'<section class="section"><h2>I</h2>{_ledd(INTRO)}{part_one}</section>'
        f'<section class="section"><h2>II</h2>{part_two}</section>'
    )
    xml = (
        '<html><body><header><dl><dd class="refid">lov/2009-06-19-74</dd>'
        '<dd class="dateInForce">2009-06-19</dd>'
        '<dd class="dateOfPublication">2009-06-19 15:00</dd>'
        '<dd class="title">Lov om endringer i straffeloven 20. mai 2005 nr. 28 mv.</dd>'
        f'<dd class="changesToDocuments"><ul>{changes}</ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    )
    return parse_document(xml.encode(), "lti/2009/nl-20090619-074.xml")


def _operations(part_one: str, part_two: str = _p(STRAKS)) -> list[Operation]:
    return list(interpret(_document(part_one, part_two)).operations)


def _acts(part_one: str, part_two: str = _p(STRAKS)) -> list[tuple[str, str | None]]:
    return [(op.instruction, op.act) for op in _operations(part_one, part_two)]


def test_the_pinned_shape_never_inherits_the_items_act_and_guesses_none() -> None:
    assert _acts(HOST_411 + ITEMS + RENAME) == [
        (RENAME_411, STRAFFELOVEN),
        (ITEM_1, None),  # militær straffelov: an item of § 411, never straffeloven's (main: it is)
        (ITEM_2, None),
        ("§ 9 annet punktum lyde:", UTLEVERING),
        (RENAME_412, None),  # main: lov/1975-06-13-39, item 3's act (#907)
    ]


def test_an_items_second_instruction_of_the_same_shape_is_not_guessed_either() -> None:
    """Item 18's own continuation looks exactly like § 402 → § 412: neither binds an act."""
    acts = _acts(HOST_411 + _p(ITEM_18) + _ledd("Ny tekst.") + _p(ITEM_18_REST))
    assert acts[1:] == [("§ 4 annet ledd lyde:", "lov/1932-06-17-6"), (ITEM_18_REST, None)]


def test_outside_a_payload_list_the_same_shape_still_binds_by_the_d7_chain() -> None:
    top = _p(ITEM_18) + _ledd("Ny tekst.") + _p(ITEM_18_REST)
    assert _acts(top)[-1] == (ITEM_18_REST, "lov/1932-06-17-6")


@pytest.mark.parametrize(
    "last", ["Ikraftsetting fastsettes ved lov.", "Departementet kan gi forskrift om følgende:"]
)
def test_without_the_announcing_ledd_nothing_changes(last: str) -> None:
    """A payload provision that announces no list opens none: the items read as before."""
    plain = _p(RENAME_411) + _future("§411", "Ikraftsetting fastsettes ved lov.", last)
    assert _acts(plain + _p(ITEM_1) + RENAME) == [
        (RENAME_411, STRAFFELOVEN),
        (ITEM_1, STRAFFELOVEN),
        (RENAME_412, STRAFFELOVEN),
    ]


def test_an_open_item_keeps_its_own_unnumbered_instructions() -> None:
    """``N. I lov … gjøres følgende endringer:`` holds what follows, as a top-level block does."""
    items = _p(ITEM_AML) + _p("§ 1-1 oppheves.") + _p("§ 1-2 oppheves.")
    assert _acts(HOST_411 + items + _p("§ 5 oppheves.")) == [
        (RENAME_411, STRAFFELOVEN),
        ("§ 1-1 oppheves.", AML),
        ("§ 1-2 oppheves.", AML),
        ("§ 5 oppheves.", AML),  # the item's, by the D7 chain; never straffeloven's
    ]


def test_every_item_starts_unbound_never_inheriting_the_previous_items_act() -> None:
    items = _p(ITEM_AML) + _p("§ 1-1 oppheves.") + _p("5. Militær straffelov § 3 oppheves.")
    assert _acts(HOST_411 + items)[-1] == ("5. Militær straffelov § 3 oppheves.", None)


@pytest.mark.parametrize(
    "item",
    [
        "4. I lov 17. juni 2005 nr. 62 skal «arbeidsgiver» endres til «virksomhet».",
        "4. Lov 17. juni 2005 nr. 62 om arbeidsmiljø",  # a heading: its scope is not proven
        ITEM_1,
    ],
)
def test_after_an_item_of_unproven_extent_the_next_instruction_binds_no_act(item: str) -> None:
    acts = _acts(HOST_411 + _p(item) + RENAME)
    assert acts[-1] == (RENAME_412, None)  # kept as an operation, not dropped
    assert all(act != STRAFFELOVEN for _, act in acts[1:])


def test_before_the_first_item_an_unnumbered_instruction_binds_no_act() -> None:
    assert _acts(HOST_411 + RENAME) == [(RENAME_411, STRAFFELOVEN), (RENAME_412, None)]


def test_the_outer_act_never_returns_inside_the_part() -> None:
    later = (
        _p("§ 5 oppheves.") + _p("Overskriften til kapittel 31 skal lyde:") + _ledd("Kreditorvern")
    )
    assert {act for _, act in _acts(HOST_411 + ITEMS + later)[4:]} == {None}


def test_a_list_opened_inside_a_list_keeps_failing_closed() -> None:
    nested = _p("3. I lov 13. juni 1975 nr. 39 skal § 9 lyde:") + _future("§9", LIST_LEDD)
    acts = _acts(HOST_411 + nested + _p(ITEM_1) + RENAME)
    assert acts[-2:] == [(ITEM_1, None), (RENAME_412, None)]


def test_an_unnumbered_intro_in_the_list_binds_its_own_block() -> None:
    intro = _p("I lov 17. juni 2005 nr. 62 gjøres følgende endringer:")
    acts = _acts(HOST_411 + ITEMS + intro + _p("§ 1-1 oppheves.") + _p("§ 1-2 oppheves."))
    assert acts[-2:] == [("§ 1-1 oppheves.", AML), ("§ 1-2 oppheves.", AML)]


def test_the_list_ends_with_its_part() -> None:
    part_two = _ledd(INTRO) + _p("§ 7 oppheves.")
    acts = _acts(HOST_411 + _p(ITEM_AML), part_two)
    assert acts[-1] == ("§ 7 oppheves.", STRAFFELOVEN)


def test_no_operation_is_lost() -> None:
    """Every instruction node still yields its operation; only acts move."""
    body = HOST_411 + ITEMS + RENAME + _p(ITEM_18) + _ledd("Ny tekst.") + _p(ITEM_18_REST)
    plain = body.replace(LIST_LEDD, "Teksten.")
    assert [op.id for op in _operations(body)] == [op.id for op in _operations(plain)]
    assert len(_operations(body)) == 7


def test_a_top_level_numbered_list_is_untouched() -> None:
    """No payload announced it: genuine numbered intros bind exactly as phase 4B reads them."""
    top = _p(ITEM_AML) + _p("§ 1-1 oppheves.") + _p(ITEM_3) + _ledd("Ny tekst.")
    assert _acts(top + _p("§ 9 a oppheves.")) == [
        ("§ 1-1 oppheves.", AML),
        ("§ 9 annet punktum lyde:", UTLEVERING),
        ("§ 9 a oppheves.", UTLEVERING),
    ]


def test_host_list_reads_only_the_last_ledd() -> None:
    last, first = (
        next(
            n.provision
            for q in _document(_future("§411", *ledd), _p(STRAKS)).parts
            for n in q.nodes
            if n.provision
        )
        for ledd in (("Teksten.", LIST_LEDD), (LIST_LEDD, "Teksten."))
    )
    assert host_list(last)
    assert not host_list(first)
    assert not host_list(None)


def test_a_listing_in_another_article_closes() -> None:
    listing = Listing()
    listing.active = True
    node = Node(kind="paragraph", css_class="defaultP", text="§ 2 oppheves.", host_section="§2")
    assert listing.step(node, (STRAFFELOVEN, False, "§1")) == (STRAFFELOVEN, False, "§1")
    assert not listing.active


def test_a_substitution_lists_numbered_entries_are_not_items_of_the_payload_list() -> None:
    """Inside an open item, a substitution's numbered entries keep the item's block open."""
    substitution = _p("I følgende bestemmelser skal ordet «arbeidsgiver» endres til «virksomhet»:")
    entries = _p("1. § 1-1 første ledd") + _p("2. § 1-2 annet ledd")
    acts = _acts(HOST_411 + _p(ITEM_AML) + substitution + entries + _p("§ 2-2 oppheves."))
    assert acts[-1] == ("§ 2-2 oppheves.", AML)
