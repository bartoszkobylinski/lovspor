"""Phase 3 (#744): the structure a commencement scope can name — chapters and parts.

Synthetic copies of the Lovtidend shape (nested ``section`` elements named
``del1`` / ``kap16``, chapter titles inside an amending payload); no Lovdata
text enters the repository.
"""

import pytest

from lovspor.legaltime.commencement import _statements
from lovspor.legaltime.document import Ledd, Node, list_statements, parse_document
from lovspor.legaltime.text import chapter_key, section_key

_HEAD = (
    '<html><body><header><dl><dd class="refid">lov/2005-05-20-28</dd>'
    '<dd class="dateInForce">Kongen bestemmer</dd><dd class="title">Lov om straff</dd>'
    "</dl></header><main>"
)


def _article(key: str, text: str) -> str:
    return (
        f'<article class="legalArticle" data-name="{key}"><h3 class="legalArticleHeader">'
        f'<span class="legalArticleValue">{key[0]} {key[1:]}</span></h3>'
        f'<article class="legalP">{text}</article></article>'
    )


def test_provisions_carry_their_enclosing_chapter_and_part() -> None:
    body = (
        _HEAD
        + '<section class="section" data-name="del1"><h2>Første del</h2>'
        + '<section class="section" data-name="kap1"><h3>Kapittel 1. A</h3>'
        + _article("§1", "En.")
        + "</section>"
        + '<section class="section" data-name="kap16"><h3>Kapittel 16. B</h3>'
        + _article("§101", "To.")
        + "</section></section>"
        + '<section class="section" data-name="del3"><h2>Tredje del</h2>'
        + _article("§102", "Tre.")
        + "</section></main></body></html>"
    )
    provisions = {p.key: p for p in parse_document(body.encode(), "lti/x.xml").provisions}
    assert (provisions["§1"].chapter, provisions["§1"].part) == ("kap1", "del1")
    assert (provisions["§101"].chapter, provisions["§101"].part) == ("kap16", "del1")
    assert (provisions["§102"].chapter, provisions["§102"].part) == (None, "del3")


def test_a_provision_outside_every_section_has_neither() -> None:
    body = _HEAD + _article("§1", "En.") + "</main></body></html>"
    provision = parse_document(body.encode(), "lti/x.xml").provisions[0]
    assert (provision.chapter, provision.part) == (None, None)


def test_a_chapter_lettered_section_label_is_keyed_without_its_space() -> None:
    assert section_key("§ 9 A-1") == "§9A-1"
    assert section_key("§ 9 A-2 og 9 A-3") == "§9A-2"
    assert section_key("§ 6A-4") == "§6A-4"
    assert section_key("§ 5 Departementet") == "§5"


def test_chapter_key_normalises_titles_and_citations_alike() -> None:
    assert chapter_key("Kapittel 16. Folkemord") == "kap16"
    assert chapter_key("kapittel 16") == "kap16"
    assert chapter_key("Kapittel 9 A. Ettervern") == "kap9a"
    assert chapter_key("nytt kapittel 9 A") == "kap9a"
    assert chapter_key("Kapittel 6A skal lyde:") == "kap6a"
    assert chapter_key("Annen del. De straffbare handlingene") is None
    assert chapter_key("§ 16") is None


@pytest.mark.parametrize(
    "xml",
    [
        "Tiltak:<ul><li>Uavsluttet</ul>",
        "Tiltak:<ul><li>A.</li></ul><ol><li>B.</li></ol>",
        "Tiltak:<div><ul><li>A.</li></ul></div>",
    ],
)
def test_unreadable_list_markup_falls_back_to_the_nodes_sentences(xml: str) -> None:
    """The new list reader refuses ambiguous structure and preserves the text fallback."""
    ledd = Ledd(xml=xml, text="Loven trer i kraft straks. Annet ledd gjelder senere.")
    node = Node(kind="ledd", css_class="legalP", text=ledd.text, ledd=ledd)
    assert list_statements(ledd) is None
    assert _statements(node) == ["Loven trer i kraft straks.", "Annet ledd gjelder senere."]


def test_nearest_named_ancestors_win_through_unrelated_sections() -> None:
    """document._enclosing promises the nearest chapter and part, independently."""
    body = (
        _HEAD
        + '<section data-name="del1"><section data-name="kap1">'
        + '<section data-name="DEL2"><section data-name="KAP9A">'
        + '<section data-name="kap10-extra"><section data-name="del3-extra">'
        + _article("§9A-1", "En.")
        + "</section></section></section></section></section></section>"
        + "</main></body></html>"
    )
    document = parse_document(body.encode(), "lti/nested.xml")
    provision = document.provisions[0]
    assert (provision.key, provision.chapter, provision.part) == ("§9A-1", "kap9a", "del2")
    assert parse_document(body.encode(), "lti/nested.xml") == document


@pytest.mark.parametrize("tag", ["ul", "ol"])
def test_nynorsk_list_lead_is_repeated_without_splitting_item_sentences(tag: str) -> None:
    """list_statements promises one statement per item, including ordered lists."""
    intro = "Følgjande trer i kraft straks:"
    xml = f"{intro}<{tag}><li>Endringsloven del I. § 1.</li><li>Endringsloven del II.</li></{tag}>"
    ledd = Ledd(xml=xml, text="")
    node = Node(kind="ledd", css_class="legalP", text="", ledd=ledd)
    expected = [
        f"{intro} Endringsloven del I. § 1.",
        f"{intro} Endringsloven del II.",
    ]
    assert list_statements(ledd) == expected
    assert _statements(node) == expected
