"""Phase 3 (#744): the structure a commencement scope can name — chapters and parts.

Synthetic copies of the Lovtidend shape (nested ``section`` elements named
``del1`` / ``kap16``, chapter titles inside an amending payload); no Lovdata
text enters the repository.
"""

from lovspor.legaltime.document import parse_document
from lovspor.legaltime.text import chapter_key

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


def test_chapter_key_normalises_titles_and_citations_alike() -> None:
    assert chapter_key("Kapittel 16. Folkemord") == "kap16"
    assert chapter_key("kapittel 16") == "kap16"
    assert chapter_key("Kapittel 9 A. Ettervern") == "kap9a"
    assert chapter_key("nytt kapittel 9 A") == "kap9a"
    assert chapter_key("Kapittel 6A skal lyde:") == "kap6a"
    assert chapter_key("Annen del. De straffbare handlingene") is None
    assert chapter_key("§ 16") is None
