"""The route tree of lovspor.no (ADR-0014 Decision 2)."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site import routes as routes_module
from lovspor.site.routes import (
    SITE_ROUTES,
    Localised,
    SiteRoute,
    canonical_url,
    client_routes,
    emitted_pages,
    en_path,
)
from lovspor.site.templates import TEMPLATES_DIR

_REPO = Path(__file__).resolve().parents[2]
_SITE = _REPO / "deploy" / "digitalocean" / "site"

EXPECTED_STATUS = {
    "/": "current",
    "/connect/": "planned",
    "/infrastructure/": "planned",
    "/research/": "research",
    "/research/llhb/": "research",
    "/research/pl-temporal/": "research",
    "/status/": "current",
    "/docs/": "planned",
    "/about/": "planned",
    "/business/": "early_access",
    "/privacy/": "planned",
    "/observatory/": "current",
}


def _head(path: Path) -> tuple[str, str]:
    html = path.read_text(encoding="utf-8")
    title = re.search(r"<title>(.*?)</title>", html)
    description = re.search(r'<meta name="description" content="(.*?)">', html)
    assert title and description
    return title.group(1), description.group(1)


class TestSiteRoutes:
    def test_every_decision_2_route_with_its_status(self) -> None:
        assert {route.path: route.status for route in SITE_ROUTES} == EXPECTED_STATUS

    def test_reserved_and_non_page_paths_are_absent(self) -> None:
        paths = {route.path for route in SITE_ROUTES}

        assert not paths & {"/terms", "/terms/", "/mcp", "/mcp/", "/en/"}
        assert not [path for path in paths if path.startswith("/en/")]

    def test_only_the_observatory_lacks_a_twin(self) -> None:
        assert [route.path for route in SITE_ROUTES if not route.twin] == ["/observatory/"]

    def test_landing_copy_is_the_existing_landing_copy(self) -> None:
        landing = next(route for route in SITE_ROUTES if route.path == "/")
        title_nb, description_nb = _head(_SITE / "index.html")
        title_en, description_en = _head(_SITE / "en" / "index.html")

        assert (landing.title.nb, landing.description.nb) == (title_nb, description_nb)
        assert (landing.title.en, landing.description.en) == (title_en, description_en)

    def test_observatory_copy_is_the_existing_observatory_copy(self) -> None:
        observatory = next(route for route in SITE_ROUTES if route.path == "/observatory/")
        title, description = _head(_SITE / "observatory" / "index.html")

        assert (observatory.title.nb, observatory.description.nb) == (title, description)
        assert observatory.title.en is None

    def test_every_route_has_copy_in_each_language_it_emits(self) -> None:
        for route in SITE_ROUTES:
            assert route.title.nb and route.description.nb, route.path
            if route.twin:
                assert route.title.en and route.description.en, route.path

    def test_routes_are_unique_and_directory_shaped(self) -> None:
        paths = [route.path for route in SITE_ROUTES]

        assert len(paths) == len(set(paths))
        assert all(path.endswith("/") for path in paths)

    def test_client_routes_hook_is_empty_until_a_registry_exists(self) -> None:
        assert client_routes() == ()
        assert client_routes(None) == ()


class TestSiteRoute:
    def test_rejects_a_path_outside_the_directory_shape(self) -> None:
        for path in ("/status", "status/", "/en/status/", "/Status/", "/a b/"):
            with pytest.raises(ValidationError):
                SiteRoute(
                    path=path,
                    template="placeholder",
                    status="planned",
                    title=Localised(nb="t", en="t"),
                    description=Localised(nb="d", en="d"),
                )

    def test_a_twin_needs_english_copy(self) -> None:
        with pytest.raises(ValidationError, match="en"):
            SiteRoute(
                path="/x/",
                template="placeholder",
                status="planned",
                title=Localised(nb="t"),
                description=Localised(nb="d", en="d"),
            )

    def test_localised_text_by_language(self) -> None:
        copy = Localised(nb="norsk", en="english")

        assert copy.text("nb") == "norsk"
        assert copy.text("en") == "english"

    def test_missing_localised_text_names_the_language(self) -> None:
        with pytest.raises(ValueError, match="no en copy"):
            Localised(nb="norsk").text("en")

    def test_route_helper_preserves_every_argument(self) -> None:
        title = Localised(nb="tittel", en="title")
        description = Localised(nb="omtale", en="description")

        route = routes_module._route("/exact/", "research", title, description)

        assert route == SiteRoute(
            path="/exact/",
            template="placeholder",
            status="research",
            title=title,
            description=description,
        )


class TestHelpers:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [("/", "/en/"), ("/status/", "/en/status/"), ("/research/llhb/", "/en/research/llhb/")],
    )
    def test_en_path(self, path: str, expected: str) -> None:
        assert en_path(path) == expected

    def test_canonical_url_is_absolute_on_the_site_origin(self) -> None:
        assert canonical_url("/en/status/") == f"{SITE_ORIGIN}/en/status/"
        assert canonical_url("/") == f"{SITE_ORIGIN}/"


class TestEmittedPages:
    def test_every_route_in_both_languages_where_the_mirror_rule_applies(self) -> None:
        pages = emitted_pages()
        paths = [page.path for page in pages]

        expected = list(EXPECTED_STATUS) + [
            en_path(p) for p in EXPECTED_STATUS if p != "/observatory/"
        ]
        assert sorted(paths) == sorted(expected)
        assert len(paths) == len(set(paths))
        assert len(pages) == 23

    def test_twins_name_each_other(self) -> None:
        by_path = {page.path: page for page in emitted_pages()}

        assert by_path["/status/"].alternate == "/en/status/"
        assert by_path["/en/status/"].alternate == "/status/"
        assert by_path["/observatory/"].alternate is None
        assert by_path["/en/status/"].lang == "en"
        assert by_path["/status/"].lang == "nb"
        assert by_path["/en/status/"].route_path == "/status/"

    def test_every_page_names_an_existing_template_in_its_language(self) -> None:
        for page in emitted_pages():
            assert page.template.endswith(f".{page.lang}.html"), page.path
            assert (TEMPLATES_DIR / page.template).is_file(), page.template

    def test_head_context_is_the_page_head(self) -> None:
        page = next(page for page in emitted_pages() if page.path == "/en/docs/")
        context = page.head_context()

        assert context["lang"] == "en"
        assert context["canonical"] == f"{SITE_ORIGIN}/en/docs/"
        assert context["alternates"] == (
            ("nb", f"{SITE_ORIGIN}/docs/"),
            ("en", f"{SITE_ORIGIN}/en/docs/"),
        )
        assert context["language_switch_href"] == "/docs/"
        assert context["status"] == "planned"
        assert context["title"] and context["description"]

    def test_a_page_without_a_twin_has_no_alternates_and_no_switch(self) -> None:
        page = next(page for page in emitted_pages() if page.path == "/observatory/")
        context = page.head_context()

        assert context["alternates"] == ()
        assert context["language_switch_href"] is None

    def test_order_is_deterministic(self) -> None:
        assert [p.path for p in emitted_pages()] == [p.path for p in emitted_pages()]

    def test_client_pages_are_a_projection_of_the_registry(self) -> None:
        """Zero registry entries, zero client pages — the set of pages is
        never the source for the set of clients (ADR:565-572)."""
        assert not [
            page
            for page in emitted_pages()
            if page.route_path.startswith("/connect/") and page.route_path != "/connect/"
        ]
