"""The shared chrome and the base template (ADR-0014 Decisions 3 and 5,
Decision 5 as amended 2026-09-16 by Amendment 2)."""

import inspect
import re

import pytest
from jinja2 import UndefinedError

import lovspor.site.chrome as chrome_module
from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.capabilities import Checkout, Observation, derive_state
from lovspor.site.chrome import Chrome, chrome_html, corpus_chrome_html
from lovspor.site.facts import FactLedger, FactRegistry, FactSource
from lovspor.site.routes import emitted_pages
from lovspor.site.style import stylesheet
from lovspor.site.templates import TEMPLATES_DIR, page_globals, site_environment
from tests.unit.site_fixtures import available_observation, checkout_expectations, readyz_503

_EXTERNAL = re.compile(r"https?://|<script|<link|<img|@import|url\(|\bon\w+=", re.IGNORECASE)


def _fact_set(documents: int, hosted_ready: bool) -> tuple[FactRegistry, str]:
    """Two deliberately different worlds: counts, corpus state, capability flags."""
    observation = available_observation() if hosted_ready else readyz_503()
    state = derive_state(
        Observation.model_validate(observation), Checkout.model_validate(checkout_expectations())
    )
    registry = FactRegistry(
        sources=(
            FactSource(
                id="corpus.documents",
                kind="corpus",
                artifact="corpus/site-manifest.json",
                field="documents",
                value=documents,
            ),
            FactSource(
                id="hosted.state",
                kind="hosted",
                artifact="deployment-capabilities.json",
                field="state.hosted_state",
                value=state.hosted_state,
            ),
        )
    )
    return registry, state.hosted_state


class TestChromeSignature:
    def test_takes_only_the_language_and_the_switch_target(self) -> None:
        """No parameter through which a fact, manifest or capability state
        could arrive (ADR:1170-1176) — enforced structurally."""
        parameters = inspect.signature(chrome_html).parameters

        assert list(parameters) == ["lang", "language_switch_href"]
        assert parameters["language_switch_href"].default is None
        assert inspect.signature(chrome_html).return_annotation is Chrome


class TestChromeInvariance:
    def test_identical_bytes_under_two_different_fact_sets(self) -> None:
        registry_a, hosted_a = _fact_set(5900, hosted_ready=True)
        registry_b, hosted_b = _fact_set(87052, hosted_ready=False)
        assert hosted_a != hosted_b

        ledger_a, ledger_b = FactLedger(), FactLedger()
        page_globals("/", "nb", registry_a, ledger_a)["fact"]("corpus.documents", kind="corpus")
        page_globals("/", "nb", registry_b, ledger_b)["fact"]("hosted.state", kind="hosted")

        assert chrome_html("nb") == chrome_html("nb")
        assert chrome_html("nb").header.encode("utf-8") == chrome_html("nb").header.encode("utf-8")
        assert chrome_html("en", "/status/") == chrome_html("en", "/status/")
        assert ledger_a.entries != ledger_b.entries

    def test_renders_with_no_fact_in_scope(self) -> None:
        """StrictUndefined plus a context of exactly two names: a chrome
        template that reached for ``fact`` or a manifest value would fail
        here, not silently on a corpus page."""
        chrome = chrome_html("nb")

        assert "data-fact" not in chrome.header + chrome.footer
        assert "data-kind" not in chrome.header + chrome.footer


class TestNorwegianSiteChrome:
    """``chrome_html("nb")`` with no switch target: a site page whose twin
    does not exist, such as ``/observatory/``.

    It was the corpus chrome too until ADR-0014 Amendment 2 gave the corpus
    its own variant; ``TestCorpusChrome`` covers that one. What is pinned
    here is the site's, so the two cannot be read as one again.
    """

    def test_is_norwegian_with_the_three_links_and_no_switch_or_badge(self) -> None:
        chrome = chrome_html("nb")

        assert 'href="/"' in chrome.header
        assert 'href="/lov/"' in chrome.header
        assert 'href="/forskrift/"' in chrome.header
        assert "Lover" in chrome.header
        assert "Forskrifter" in chrome.header
        assert "lov<span>spor</span>" in chrome.header
        assert "EN" not in chrome.header
        assert "NO" not in chrome.header
        assert 'class="tag"' not in chrome.header + chrome.footer
        assert "data-status" not in chrome.header + chrome.footer

    def test_footer_carries_the_licence_and_not_legal_advice_lines_verbatim(self) -> None:
        footer = chrome_html("nb").footer

        assert "Inneholder data under <span data-literal>NLOD 2.0</span> fra Lovdata." in footer
        assert "Ikke tilknyttet eller godkjent av Lovdata." in footer
        assert "lovspor viser lovtekst til oppslag; det er ikke juridisk rådgivning." in footer
        assert "Bygget av Bartosz Kobyliński." in footer
        assert '<a href="/observatory/">Om roboten vår</a>' in footer

    def test_adds_no_external_link_asset_or_script(self) -> None:
        """The chrome adds no external link and no new scheme (ADR:1180-1181)."""
        for lang, href in (("nb", None), ("en", "/"), ("nb", "/en/status/")):
            chrome = chrome_html(lang, href)  # type: ignore[arg-type]

            assert not _EXTERNAL.search(chrome.header + chrome.footer)

    def test_the_only_numerals_are_marked_literals(self) -> None:
        for lang in ("nb", "en"):
            text = chrome_html(lang).header + chrome_html(lang).footer  # type: ignore[arg-type]
            outside = re.sub(r"<span data-literal>[^<]*</span>", "", text)

            assert not re.search(r"\d", outside), outside


class TestLanguageSwitch:
    def test_norwegian_page_links_to_its_english_twin(self) -> None:
        header = chrome_html("nb", "/en/status/").header

        assert "<strong>NO</strong>" in header
        assert '<a href="/en/status/">EN</a>' in header

    def test_english_page_links_back_and_reads_english(self) -> None:
        chrome = chrome_html("en", "/status/")

        assert "<strong>EN</strong>" in chrome.header
        assert '<a href="/status/">NO</a>' in chrome.header
        assert "Acts" in chrome.header
        assert "Regulations" in chrome.header
        assert (
            "Contains data under <span data-literal>NLOD 2.0</span> from Lovdata." in chrome.footer
        )
        assert "it does not provide legal advice." in chrome.footer
        assert '<a href="/observatory/">About our crawler</a>' in chrome.footer

    def test_the_switch_target_is_escaped(self) -> None:
        header = chrome_html("nb", '/en/"><script>').header

        assert "<script>" not in header
        assert "&lt;script&gt;" in header


class TestCorpusChrome:
    """The corpus frame answers an English reader (ADR-0014 Amendment 2).

    Decision 5 made the corpus chrome Norwegian with no switch and no badge.
    The amendment separates the legal text from its frame: the text stays
    Norwegian and the badges stay out, the *navigation* gains an English
    gloss and a link to the English site, and the per-page switch stays out
    for the reason it was refused in the first place — there is no English
    twin of a law page, so a switch would publish a dead link on ~93k pages.
    """

    def test_it_takes_no_argument_at_all(self) -> None:
        """Stronger than ``chrome_html``'s guarantee, not weaker: the corpus
        variant has no parameter of any kind, so no fact, count, manifest or
        capability value can reach ~93k pages (ADR:1170-1176)."""
        assert list(inspect.signature(corpus_chrome_html).parameters) == []
        assert inspect.signature(corpus_chrome_html).return_annotation is Chrome

    def test_it_renders_with_exactly_the_fixed_corpus_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pin the zero-input boundary before template branching can hide drift.

        The language remains the canonical Norwegian code, and the absent
        per-page twin is represented by the switch key with a null target.
        """
        expected = Chrome(header="header", footer="footer")
        contexts: list[dict[str, object]] = []

        def record_context(context: dict[str, object]) -> Chrome:
            contexts.append(context)
            return expected

        monkeypatch.setattr(chrome_module, "_render", record_context)

        assert corpus_chrome_html() is expected
        assert contexts == [{"lang": "nb", "language_switch_href": None, "corpus": True}]

    def test_both_navigation_labels_carry_their_english_gloss(self) -> None:
        header = corpus_chrome_html().header

        assert '<a href="/lov/">Lover <span class="gloss" lang="en">Acts</span></a>' in header
        assert (
            '<a href="/forskrift/">Forskrifter '
            '<span class="gloss" lang="en">Regulations</span></a>' in header
        )

    def test_it_links_to_the_english_site(self) -> None:
        """Named, not abbreviated: an ``EN`` with no ``NO`` beside it reads as
        half a switch, and ``In English`` is the idiom Norwegian public sites
        use for this affordance."""
        assert '<a href="/en/" lang="en">In English</a>' in corpus_chrome_html().header

    def test_every_english_string_is_marked_as_english(self) -> None:
        """Otherwise a screen reader pronounces the gloss and the link with
        Norwegian phonetics: the frame is for a reader who cannot read the
        page it frames, so the one thing it must get right is being heard."""
        header = corpus_chrome_html().header

        assert header.count('lang="en"') == 3
        for english in ("Acts", "Regulations", "In English"):
            assert f'lang="en">{english}<' in header

    def test_it_carries_no_per_page_language_switch(self) -> None:
        """The switch markup is what promises a twin at the other language's
        URL. A later refactor must not reintroduce it by passing ``/en/`` as
        ``language_switch_href``, which renders exactly that promise."""
        header = corpus_chrome_html().header

        assert "<strong>NO</strong>" not in header
        assert "<strong>EN</strong>" not in header
        assert ">NO</a>" not in header
        assert header != chrome_html("nb", "/en/").header

    def test_the_frame_is_norwegian_and_carries_no_status_badge(self) -> None:
        chrome = corpus_chrome_html()

        assert "Lover" in chrome.header
        assert "Forskrifter" in chrome.header
        assert 'href="/"' in chrome.header
        assert "Inneholder data under <span data-literal>NLOD 2.0</span> fra Lovdata." in (
            chrome.footer
        )
        assert 'class="tag"' not in chrome.header + chrome.footer
        assert "data-status" not in chrome.header + chrome.footer

    def test_it_adds_no_external_link_asset_or_script(self) -> None:
        chrome = corpus_chrome_html()

        assert not _EXTERNAL.search(chrome.header + chrome.footer)

    def test_the_only_numerals_are_marked_literals(self) -> None:
        text = corpus_chrome_html().header + corpus_chrome_html().footer
        outside = re.sub(r"<span data-literal>[^<]*</span>", "", text)

        assert not re.search(r"\d", outside), outside

    def test_identical_bytes_on_every_call_and_no_fact_in_scope(self) -> None:
        first, second = corpus_chrome_html(), corpus_chrome_html()

        assert first == second
        assert first.header.encode("utf-8") == second.header.encode("utf-8")
        assert "data-fact" not in first.header + first.footer
        assert "data-kind" not in first.header + first.footer

    def test_the_site_chrome_gains_no_gloss(self) -> None:
        """Site pages keep today's chrome exactly: their switch is real,
        because both twins exist, and their labels need no gloss because the
        English twin is a page of its own."""
        for lang, href in (("nb", None), ("en", "/status/"), ("nb", "/en/status/")):
            chrome = chrome_html(lang, href)  # type: ignore[arg-type]

            assert "gloss" not in chrome.header + chrome.footer


def _render(path: str) -> str:
    page = next(page for page in emitted_pages() if page.path == path)
    registry = FactRegistry(sources=())
    context = page.head_context() | page_globals(page.path, page.lang, registry, FactLedger())
    return site_environment().get_template(page.template).render(context)


class TestBaseTemplate:
    def test_missing_frame_variant_fails_closed(self) -> None:
        """Every page must explicitly choose the site or corpus frame.

        The new branch must not silently default when a future rendering
        entry point omits ``corpus``: that would make the navigation depend
        on Jinja's treatment of an undefined value instead of the route's
        declared page kind.
        """
        page = next(page for page in emitted_pages() if page.path == "/about/")
        context = page.head_context() | page_globals(
            page.path, page.lang, FactRegistry(sources=()), FactLedger()
        )
        del context["corpus"]

        with pytest.raises(UndefinedError, match="corpus.*undefined"):
            site_environment().get_template(page.template).render(context)

    def test_head_carries_lang_title_description_canonical_and_hreflang_pair(self) -> None:
        html = _render("/about/")

        assert html.startswith('<!doctype html>\n<html lang="nb">\n')
        assert "<title>" in html
        assert '<meta name="description" content="' in html
        assert f'<link rel="canonical" href="{SITE_ORIGIN}/about/">' in html
        assert f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/about/">' in html
        assert f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/about/">' in html
        assert html.count("<h1>") == 1
        assert html.endswith("</html>\n")

    def test_the_english_twin_mirrors_the_pair_and_switches_back(self) -> None:
        html = _render("/en/about/")

        assert '<html lang="en">' in html
        assert f'<link rel="canonical" href="{SITE_ORIGIN}/en/about/">' in html
        assert f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/about/">' in html
        assert f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/about/">' in html
        assert '<a href="/about/">NO</a>' in html

    def test_a_page_without_a_twin_has_no_hreflang_and_no_switch(self) -> None:
        html = _render("/observatory/")

        assert "hreflang" not in html
        assert 'class="lang"' in html  # the corpus links stay
        assert ">EN</a>" not in html

    def test_the_stylesheet_is_the_one_shared_source_verbatim(self) -> None:
        """The base template inlines the shared source and nothing of its own.

        Pinned against ``style.css`` rather than against a copy: a second
        copy is exactly how the site and the corpus drifted apart (#335).
        The exemplar is a page that renders no fact: this harness builds an
        empty registry, so a page calling ``fact()`` fails here for a reason
        that has nothing to do with the stylesheet.
        """
        html = _render("/about/")

        assert f"<style>\n{stylesheet()}</style>" in html
        assert html.count("<style>") == 1

    def test_placeholder_pages_carry_the_badge_title_and_lede(self) -> None:
        nb = _render("/about/")
        en = _render("/en/about/")

        assert '<span class="tag" data-status="planned">Planlagt</span>' in nb
        assert '<span class="tag" data-status="planned">Planned</span>' in en
        assert "<h1>" in nb
        assert '<p class="lede">' in nb
        assert "ikke publisert ennå" in nb
        assert "not published yet" in en

    def test_no_script_and_no_external_asset_on_any_placeholder_page(self) -> None:
        """Pages that read facts are covered on the built tree (test_site_build)."""
        for page in emitted_pages():
            if page.route.template != "placeholder":
                continue
            html = _render(page.path)
            body = html[html.index("<body>") :]

            assert not _EXTERNAL.search(body), page.path
            assert "<script" not in html

    def test_every_template_lives_under_the_package(self) -> None:
        templates = sorted(
            path.relative_to(TEMPLATES_DIR).as_posix() for path in TEMPLATES_DIR.rglob("*.html")
        )

        assert templates == [
            "_base.html",
            "_chrome_footer.html",
            "_chrome_header.html",
            "pages/connect.en.html",
            "pages/connect.nb.html",
            "pages/docs.en.html",
            "pages/docs.nb.html",
            "pages/landing.en.html",
            "pages/landing.nb.html",
            "pages/observatory.nb.html",
            "pages/placeholder.en.html",
            "pages/placeholder.nb.html",
            "pages/status.en.html",
            "pages/status.nb.html",
        ]
        assert TEMPLATES_DIR.name != "html"
