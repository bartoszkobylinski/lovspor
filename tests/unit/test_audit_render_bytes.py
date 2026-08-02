"""Guard the corpus-audit instrument itself.

``scripts/audit_render_bytes.py`` is the only tool that measures whether the
rendered corpus still contains the source text, and its verdict is the gate on
a corpus-wide re-render. A measuring instrument that silently stops measuring
manufactures confidence, so its metric core is pinned here: the two coverage
metrics must register damage, and the fabricated-token counter must fire on a
fused boundary and stay silent on a faithful render.

The script is not an importable package (it lives in ``scripts/`` precisely
because it imports the engine but is not part of it), so it is loaded from its
path — the same "test the artifact where it actually lives" approach as
``test_deploy_landing.py``.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from lxml import etree

from lovspor.errors import ParseError

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "audit_render_bytes.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_render_bytes", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_script()


# --------------------------------------------------------------------------
# char coverage — "is the text still there at all?"
# --------------------------------------------------------------------------


def test_char_coverage_is_total_when_the_markdown_keeps_every_character() -> None:
    xml = audit._alnum_stream("Enhver kan yte rettslig bistand mot vederlag")
    md = audit._alnum_stream("**Enhver** kan yte rettslig bistand, mot vederlag.")

    # Punctuation and emphasis are stripped on both sides, so formatting the
    # same text differently must not read as content loss.
    assert audit._char_coverage(xml, md) == 1.0


def test_char_coverage_falls_when_a_passage_is_excised() -> None:
    source = "Kongen kan gi forskrift om gjennomforing og utfylling av loven " * 4
    xml = audit._alnum_stream(source)
    half = audit._alnum_stream(source[: len(source) // 2])

    assert audit._char_coverage(xml, half) < 0.6


def test_char_coverage_of_empty_source_is_total_not_zero() -> None:
    # A document with no body text has nothing to lose; scoring it 0 would
    # flood the flag list with documents that are fine.
    assert audit._char_coverage("", "anything") == 1.0


def test_char_coverage_ignores_text_the_renderer_adds() -> None:
    xml = audit._alnum_stream("navn og adresse")
    md = audit._alnum_stream("navn og adresse — se ogsa kapittel 3 og vedlegg 1")

    # One-directional on purpose: added content cannot raise the score above
    # what the source justifies, and must not lower it either.
    assert audit._char_coverage(xml, md) == 1.0


# --------------------------------------------------------------------------
# token coverage — "are word boundaries intact?"
# --------------------------------------------------------------------------


def test_token_coverage_collapses_when_words_fuse_although_characters_survive() -> None:
    # The exact defect class found in kredittopplysningsforskriften § 2:
    # a nine-item <ol> flattened into one run-on string.
    xml_text = "Grunndata: navn folkeregistrert adresse telefonnummer"
    fused = "Grunndata:navnfolkeregistrert adressetelefonnummer"

    xml_counts = audit._words(xml_text)
    fused_counts = audit._words(fused)

    assert audit._char_coverage(audit._alnum_stream(xml_text), audit._alnum_stream(fused)) > 0.5
    assert audit._coverage(xml_counts, fused_counts) < 0.3


def test_token_coverage_is_total_for_a_faithful_render() -> None:
    xml_counts = audit._words("navn folkeregistrert adresse telefonnummer")
    md_counts = audit._words("- navn\n- folkeregistrert adresse\n- telefonnummer\n")

    assert audit._coverage(xml_counts, md_counts) == 1.0


def test_token_coverage_weights_long_words_more_than_short_ones() -> None:
    xml_counts = audit._words("saksbehandlingsregler og")
    lost_long = audit._words("og")
    lost_short = audit._words("saksbehandlingsregler")

    # Losing a 21-character legal term is not the same defect size as losing
    # a conjunction, so the metric must not treat them as one token each.
    assert audit._coverage(xml_counts, lost_long) < audit._coverage(xml_counts, lost_short)


def test_missing_words_reports_the_heaviest_deficits_first() -> None:
    xml_counts = audit._words("saksbehandlingsregler og og og")
    md_counts = audit._words("")

    sample = audit._missing_words(xml_counts, md_counts)

    assert sample[0].startswith("saksbehandlingsregler")


# --------------------------------------------------------------------------
# fabricated tokens — the counter behind the 74,622 figure
# --------------------------------------------------------------------------


def _fabricated(xml_text: str, md_body: str) -> dict[str, int]:
    """Reproduce the script's novel-token rule over two ready-made strings."""
    xml_counts = audit._words(xml_text)
    md_counts = audit._words(md_body)
    return {t: n for t, n in md_counts.items() if t not in xml_counts}


def test_a_fused_boundary_produces_a_word_absent_from_the_source() -> None:
    fabricated = _fabricated("oppfyller kravene", "oppfyllerkravene")

    assert fabricated == {"oppfyllerkravene": 1}


def test_a_faithful_render_fabricates_nothing() -> None:
    fabricated = _fabricated("oppfyller kravene", "**oppfyller** kravene\n\n")

    assert fabricated == {}


def test_synthesized_markdown_is_not_counted_as_fabricated_text() -> None:
    # Link destinations, list markers and heading marks have no counterpart in
    # any XML text node. Counting them would report every healthy document as
    # fabricating, and the real signal would drown.
    md = (
        "## Kapittel 1\n\n"
        "1. [forskriften](forskrift/2003-11-06-1318) gjelder\n"
        "- navn\n"
        "> Endret ved lov 12 mai 2000\n"
    )
    body = audit._md_body(md)

    fabricated = _fabricated(
        "Kapittel 1 forskriften gjelder navn Endret ved lov 12 mai 2000",
        body,
    )

    assert fabricated == {}


def test_md_body_strips_the_frontmatter_before_measuring() -> None:
    md = '---\nslug: "lovdata"\nrenderer_version: 3\n---\nEnhver kan yte bistand\n'

    body = audit._md_body(md)

    assert "renderer_version" not in body
    assert "Enhver kan yte bistand" in body


def test_inline_markup_inside_a_word_does_not_split_it() -> None:
    # "CO<sub>2</sub>" is one word. Splitting it made the renderer's correct
    # "CO2" look like a fabricated token — 13,499 of the 13,501 reported on
    # 2026-07-27 were this, and they hid the 140 that were real.
    root = etree.fromstring(
        '<main><article class="legalP">avgift per km<sup>2</sup> for CO<sub>2</sub>'
        '<sup class="footnotereference">1</sup>.</article></main>',
    )

    text = audit._all_text(root)

    assert "km2" in text
    assert "co2" in text.lower()
    assert "km 2" not in text


def test_a_block_boundary_still_separates_two_words() -> None:
    root = etree.fromstring(
        '<main><article class="legalP">gjelder, eller</article>'
        '<article class="legalP">dersom eieren</article></main>',
    )

    text = audit._all_text(root)

    assert "eller dersom" in text
    assert "ellerdersom" not in text


def test_a_void_block_element_separates_the_text_around_it() -> None:
    # <br/> carries no text, so a model built from the two text nodes'
    # ancestries cannot see it and would glue the cell's two lines together.
    root = etree.fromstring(
        "<main><table><tbody><tr><td>Akademia Rolnicza<br/>2. Uniwersytet</td>"
        "</tr></tbody></table></main>",
    )

    assert "Rolnicza 2. Uniwersytet" in audit._all_text(root)


def test_md_body_strips_a_nested_list_marker_sharing_its_parents_line() -> None:
    # A nested list whose first item shares the outer marker's line renders as
    # "9. 1. Landbruksdepartementet". One marker stripped leaves a stray "1".
    body = audit._md_body("9. 1. Landbruksdepartementet endres\n")

    assert body.startswith("Landbruksdepartementet")


def test_md_body_unescapes_without_mistaking_escaped_text_for_a_list_marker() -> None:
    # The renderer escapes body text that genuinely begins "1." so Markdown
    # does not read it as a list. Undoing escapes before marker-stripping
    # would delete the real legal numbering along with synthesized markers.
    body = audit._md_body("1\\. januar trer loven i kraft\n")

    assert body.startswith("1. januar")


# --------------------------------------------------------------------------
# not-in-force elision — the documented renderer drop
# --------------------------------------------------------------------------


def test_elision_drops_a_future_subtree_but_keeps_the_text_after_it() -> None:
    root = etree.fromstring(
        '<main><article class="legalP">gjeldende tekst'
        '<span class="futureLegalArticle">fremtidig tekst</span>'
        "etterfolgende tekst</article></main>",
    )

    audit._elide_not_in_force(root)
    text = audit._all_text(root)

    assert "fremtidig" not in text
    # The tail is the sibling's text, not part of the elided subtree: dropping
    # it with the subtree would understate the XML side and hide real loss.
    assert "etterfolgende tekst" in text
    assert "gjeldende tekst" in text


def test_elision_reattaches_the_tail_where_it_stood_not_at_the_front() -> None:
    # elem.getprevious() answers None once the element is detached, so taking it
    # after the removal sent every tail to parent.text — in front of the
    # siblings that preceded it. The XML side then carried the document's own
    # words out of order, which the char n-gram metric reads as loss.
    root = etree.fromstring(
        '<main><p>Aaa <i>kursiv</i> <span class="futuretitle">BORTE</span> Bbb</p></main>',
    )

    audit._elide_not_in_force(root)

    assert audit._all_text(root).split() == ["Aaa", "kursiv", "Bbb"]


def _header(inner: str, tag: str = "h3") -> etree._Element:
    return etree.fromstring(f'<main><{tag} class="legalArticleHeader">{inner}</{tag}></main>')


def test_a_heading_footnote_marker_is_elided_as_a_documented_drop() -> None:
    # _render_legal_article_header renders the two spans and nothing else.
    # Counting the marker as loss would hold the metric permanently above zero
    # for a drop the renderer documents and defends.
    root = _header(
        '<span class="legalArticleValue">§ 5</span>.'
        '<sup class="footnotereference">1</sup> '
        '<span class="legalArticleTitle">(skade som ikkje kan krevjast)</span>',
    )

    audit._elide_dropped_heading_marks(root)
    text = audit._all_text(root)

    assert "§ 5" in text
    assert "(skade som ikkje kan krevjast)" in text
    # "§ 5.1" is what emitting it verbatim would produce, and reads as a
    # citation of subsection 5.1 — the reason for the drop in the first place.
    assert "5.1" not in text


def test_eliding_the_marker_leaves_the_rest_of_the_heading_intact() -> None:
    root = _header(
        '<span class="legalArticleValue">§ 1</span>. '
        '<span class="legalArticleTitle">Lovens <i>område</i>.</span>'
        '<sup class="footnotereference">1</sup>',
    )

    audit._elide_dropped_heading_marks(root)

    assert "Lovens" in audit._all_text(root)
    assert "område" in audit._all_text(root)


def test_a_heading_without_a_value_span_keeps_everything() -> None:
    # The renderer falls back to the full inline walk there, so nothing is
    # dropped and the audit must not pretend otherwise.
    root = _header('Fritekst<sup class="footnotereference">1</sup>')

    audit._elide_dropped_heading_marks(root)

    assert "1" in audit._all_text(root)


def test_an_h2_marker_is_elided_since_renderer_8() -> None:
    # _render_h2_legal_article_header assembles the two spans and drops the
    # sibling marker, the same documented drop as h3-h6.
    root = _header(
        '<span class="legalArticleValue">§ 7</span>. '
        '<span class="legalArticleTitle">Tittel</span>'
        '<sup class="footnotereference">1</sup>',
        tag="h2",
    )

    audit._elide_dropped_heading_marks(root)

    assert not audit._all_text(root).endswith("1")
    assert "Tittel" in audit._all_text(root)


def test_an_h2_marker_inside_a_span_is_elided_but_an_h3_one_is_kept() -> None:
    # The h2 drop is wider than h3-h6's: the renderer omits an in-span marker
    # on h2, while h3-h6 keep it delimited as [^n] — so its text IS in their
    # Markdown and eliding it there would misstate what the renderer does.
    inner = (
        '<span class="legalArticleValue">§ 1</span>. '
        '<span class="legalArticleTitle">Definisjoner'
        '<sup class="footnotereference">1</sup></span>'
    )

    h2 = _header(inner, tag="h2")
    audit._elide_dropped_heading_marks(h2)
    assert "1" not in audit._all_text(h2).replace("§ 1", "")

    h3 = _header(inner, tag="h3")
    audit._elide_dropped_heading_marks(h3)
    assert "Definisjoner1" in audit._all_text(h3).replace(" ", "")


def test_an_h2_heading_without_a_value_span_keeps_its_marker() -> None:
    # No value span means the renderer falls back to the full inline walk and
    # emits the marker, on h2 exactly as on h3-h6.
    root = _header('Amtskasserere<sup class="footnotereference">1</sup>', tag="h2")

    audit._elide_dropped_heading_marks(root)

    assert "1" in audit._all_text(root)


def test_a_wrapper_holding_a_rendered_span_is_not_elided() -> None:
    root = _header(
        '<span class="futuretitle"><span class="legalArticleValue">§ 3</span></span>'
        '<span class="legalArticleTitle">Tittel</span>',
    )

    audit._elide_dropped_heading_marks(root)

    assert "§ 3" in audit._all_text(root)


def test_body_root_refuses_a_document_without_a_main_element() -> None:
    with pytest.raises(ParseError, match="no <main>"):
        audit._body_root(b"<document><header>metadata only</header></document>")


# --------------------------------------------------------------------------
# invocation surface
# --------------------------------------------------------------------------


class _Args:
    def __init__(
        self,
        docs: str | None = None,
        sample: int | None = None,
        seed: int = 1729,
    ) -> None:
        self.docs = docs
        self.sample = sample
        self.seed = seed


def test_selecting_by_doc_id_drops_ids_absent_from_the_manifest() -> None:
    records = {"lov-1": {}, "lov-2": {}}

    selected = audit._select_docs(_Args(docs="lov-1,lov-404"), records)

    # A typo in the invocation must not be audited as a corpus finding.
    assert selected == ["lov-1"]


def test_sampling_is_deterministic_for_a_given_seed() -> None:
    records = {f"lov-{i}": {} for i in range(50)}

    first = audit._select_docs(_Args(sample=10), records)
    second = audit._select_docs(_Args(sample=10), records)

    assert first == second
    assert len(first) == 10


def test_sampling_more_than_the_corpus_holds_returns_the_whole_corpus() -> None:
    records = {f"lov-{i}": {} for i in range(3)}

    assert len(audit._select_docs(_Args(sample=99), records)) == 3


def test_no_mode_flag_audits_every_current_document() -> None:
    records = {"lov-2": {}, "lov-1": {}}

    assert audit._select_docs(_Args(), records) == ["lov-1", "lov-2"]


def test_corpus_path_defaults_to_the_repo_the_sync_writes_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOVSPOR_OUTPUT_REPO_PATH", "/srv/lovverk")

    assert audit._env_corpus_path() == Path("/srv/lovverk")


def test_corpus_path_is_none_rather_than_a_guess_when_the_environment_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOVSPOR_OUTPUT_REPO_PATH", raising=False)

    # Auditing the wrong clone reports drift that is really a path mistake.
    assert audit._env_corpus_path() is None


def test_archive_dir_follows_the_sync_cache_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOVSPOR_DATA_DIR", "/var/lovspor")

    # Mirrors sync.orchestrator: settings.data_dir / "cache" / "archives".
    assert audit._env_archive_dir() == Path("/var/lovspor/cache/archives")


def test_a_footnote_reference_is_a_word_boundary_but_a_plain_sup_is_not() -> None:
    # Renderer 5 emits "[^1]" for a footnote marker, so the marker and the word
    # it annotates are separate tokens on the Markdown side and the XML side
    # must agree. A plain <sup> carries no delimiter — "km2" is one word — so
    # the distinction is by class, not by tag.
    root = etree.fromstring(
        '<main><article class="legalP">Amtskasserere'
        '<sup class="footnotereference">1</sup> og Politimestre. '
        "Avgift per km<sup>2</sup>.</article></main>",
    )

    text = audit._all_text(root)

    assert "Amtskasserere 1" in text
    assert "Amtskasserere1" not in text
    assert "km2" in text
