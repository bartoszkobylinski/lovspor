"""The shared chrome and the base template (ADR-0014 Decisions 3 and 5)."""

import inspect
import re
from pathlib import Path

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.capabilities import Checkout, Observation, derive_state
from lovspor.site.chrome import Chrome, chrome_html
from lovspor.site.facts import FactLedger, FactRegistry, FactSource
from lovspor.site.routes import emitted_pages
from lovspor.site.templates import TEMPLATES_DIR, page_globals, site_environment
from tests.unit.site_fixtures import available_observation, checkout_expectations, readyz_503

_REPO = Path(__file__).resolve().parents[2]
_LANDING = _REPO / "deploy" / "digitalocean" / "site" / "index.html"
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


class TestCorpusVariant:
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


def _render(path: str) -> str:
    page = next(page for page in emitted_pages() if page.path == path)
    registry = FactRegistry(sources=())
    context = page.head_context() | page_globals(page.path, page.lang, registry, FactLedger())
    return site_environment().get_template(page.template).render(context)


class TestBaseTemplate:
    def test_head_carries_lang_title_description_canonical_and_hreflang_pair(self) -> None:
        html = _render("/docs/")

        assert html.startswith('<!doctype html>\n<html lang="nb">\n')
        assert "<title>" in html
        assert '<meta name="description" content="' in html
        assert f'<link rel="canonical" href="{SITE_ORIGIN}/docs/">' in html
        assert f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/docs/">' in html
        assert f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/docs/">' in html
        assert html.count("<h1>") == 1
        assert html.endswith("</html>\n")

    def test_the_english_twin_mirrors_the_pair_and_switches_back(self) -> None:
        html = _render("/en/docs/")

        assert '<html lang="en">' in html
        assert f'<link rel="canonical" href="{SITE_ORIGIN}/en/docs/">' in html
        assert f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/docs/">' in html
        assert f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/docs/">' in html
        assert '<a href="/docs/">NO</a>' in html

    def test_a_page_without_a_twin_has_no_hreflang_and_no_switch(self) -> None:
        html = _render("/observatory/")

        assert "hreflang" not in html
        assert 'class="lang"' in html  # the corpus links stay
        assert ">EN</a>" not in html

    def test_the_stylesheet_is_the_landing_stylesheet_verbatim(self) -> None:
        """One inline stylesheet, migrated from deploy/digitalocean/site/index.html."""
        landing = _LANDING.read_text(encoding="utf-8")
        golden = landing[landing.index("<style>") : landing.index("</style>") + len("</style>")]
        html = _render("/docs/")

        assert golden in html
        assert html.count("<style>") == 1

    def test_placeholder_pages_carry_the_badge_title_and_lede(self) -> None:
        nb = _render("/docs/")
        en = _render("/en/docs/")

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
            "pages/landing.en.html",
            "pages/landing.nb.html",
            "pages/observatory.nb.html",
            "pages/placeholder.en.html",
            "pages/placeholder.nb.html",
            "pages/status.en.html",
            "pages/status.nb.html",
        ]
        assert TEMPLATES_DIR.name != "html"
