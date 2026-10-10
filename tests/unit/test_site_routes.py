"""The route tree of lovspor.no (ADR-0014 Decision 2)."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site import routes as routes_module
from lovspor.site.routes import (
    CLIENT_REGISTRY,
    SITE_ROUTES,
    ConnectClient,
    Localised,
    SiteRoute,
    canonical_url,
    client_routes,
    emitted_pages,
    en_path,
    lang_path,
)
from lovspor.site.templates import TEMPLATES_DIR

# The hand-written pages the generator replaced, kept as fixtures when
# `deploy/digitalocean/site/` was retired in the first-migration PR.
_SITE = Path(__file__).resolve().parent / "fixtures" / "site" / "pre-envelope"

EXPECTED_STATUS = {
    "/": "current",
    "/connect/": "current",
    "/infrastructure/": "planned",
    "/research/": "research",
    "/research/llhb/": "research",
    "/research/pl-temporal/": "research",
    "/status/": "current",
    "/docs/": "current",
    "/about/": "planned",
    "/business/": "early_access",
    "/privacy/": "current",
    "/terms/": "current",
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

        assert not paths & {"/terms", "/mcp", "/mcp/", "/en/", "/pl/"}
        assert not [path for path in paths if path.startswith(("/en/", "/pl/"))]

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
        assert observatory.title.pl is None

    def test_every_route_has_copy_in_each_language_it_emits(self) -> None:
        for route in SITE_ROUTES:
            assert route.title.nb and route.description.nb, route.path
            if route.twin:
                assert route.title.en and route.description.en, route.path
                assert route.title.pl and route.description.pl, route.path

    def test_routes_are_unique_and_directory_shaped(self) -> None:
        paths = [route.path for route in SITE_ROUTES]

        assert len(paths) == len(set(paths))
        assert all(path.endswith("/") for path in paths)

    @pytest.mark.parametrize("path", ["/privacy/", "/terms/"])
    def test_the_legal_pages_are_written_pages_not_placeholders(self, path: str) -> None:
        """The OAuth consent screen links both pages, so neither may be the
        "not published yet" placeholder (owner decision 2026-09-30)."""
        route = next(route for route in SITE_ROUTES if route.path == path)

        assert route.template == path.strip("/")
        assert route.status == "current"
        assert route.twin

    def test_the_registry_names_the_two_clients_with_a_recorded_procedure(self) -> None:
        """claude.ai and ChatGPT were connected to the hosted service on
        2026-09-30 (docs/mcp.md); no other client has a recorded procedure."""
        assert [client.slug for client in CLIENT_REGISTRY] == ["claude", "chatgpt"]

    def test_client_routes_are_one_current_guide_per_registry_entry(self) -> None:
        routes = client_routes()

        assert [route.path for route in routes] == ["/connect/claude/", "/connect/chatgpt/"]
        assert [route.template for route in routes] == ["connect-claude", "connect-chatgpt"]
        assert {route.status for route in routes} == {"current"}
        assert all(route.twin for route in routes)
        assert client_routes(None) == routes
        assert client_routes(()) == ()

    def test_a_client_route_carries_its_copy_in_all_three_languages(self) -> None:
        for client in CLIENT_REGISTRY:
            route = client.route()
            for lang in ("nb", "en", "pl"):
                assert route.title.text(lang), (client.slug, lang)  # type: ignore[arg-type]
                assert route.description.text(lang), (client.slug, lang)  # type: ignore[arg-type]

    def test_a_client_slug_is_one_path_segment(self) -> None:
        copy = Localised(nb="t", en="t", pl="t")
        for slug in ("Claude", "a/b", "", "a b"):
            with pytest.raises(ValidationError):
                ConnectClient(slug=slug, title=copy, description=copy)


class TestSiteRoute:
    def test_rejects_a_path_outside_the_directory_shape(self) -> None:
        for path in ("/status", "status/", "/en/status/", "/pl/status/", "/Status/", "/a b/"):
            with pytest.raises(ValidationError):
                SiteRoute(
                    path=path,
                    template="placeholder",
                    status="planned",
                    title=Localised(nb="t", en="t", pl="t"),
                    description=Localised(nb="d", en="d", pl="d"),
                )

    def test_a_twin_needs_polish_copy(self) -> None:
        """Owner decision 2026-10-10: every route with an English twin has a
        Polish one, so a route that cannot say its title in Polish fails at
        definition rather than rendering a blank head."""
        for title, description in (
            (Localised(nb="t", en="t"), Localised(nb="d", en="d", pl="d")),
            (Localised(nb="t", en="t", pl="t"), Localised(nb="d", en="d")),
        ):
            with pytest.raises(ValidationError, match="pl title and description"):
                SiteRoute(
                    path="/x/",
                    template="placeholder",
                    status="planned",
                    title=title,
                    description=description,
                )

    def test_a_twin_needs_english_copy(self) -> None:
        with pytest.raises(ValidationError, match="en"):
            SiteRoute(
                path="/x/",
                template="placeholder",
                status="planned",
                title=Localised(nb="t", pl="t"),
                description=Localised(nb="d", en="d", pl="d"),
            )

    def test_localised_text_by_language(self) -> None:
        copy = Localised(nb="norsk", en="english", pl="polski")

        assert copy.text("nb") == "norsk"
        assert copy.text("en") == "english"
        assert copy.text("pl") == "polski"

    def test_missing_localised_text_names_the_language(self) -> None:
        with pytest.raises(ValueError, match="no en copy"):
            Localised(nb="norsk").text("en")
        with pytest.raises(ValueError, match="no pl copy"):
            Localised(nb="norsk", en="english").text("pl")

    def test_route_helper_preserves_every_argument(self) -> None:
        title = Localised(nb="tittel", en="title", pl="tytuł")
        description = Localised(nb="omtale", en="description", pl="opis")

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

    @pytest.mark.parametrize(
        ("path", "lang", "expected"),
        [
            ("/", "nb", "/"),
            ("/status/", "nb", "/status/"),
            ("/", "pl", "/pl/"),
            ("/terms/", "pl", "/pl/terms/"),
            ("/docs/", "en", "/en/docs/"),
        ],
    )
    def test_lang_path(self, path: str, lang: str, expected: str) -> None:
        assert lang_path(path, lang) == expected  # type: ignore[arg-type]

    def test_canonical_url_is_absolute_on_the_site_origin(self) -> None:
        assert canonical_url("/en/status/") == f"{SITE_ORIGIN}/en/status/"
        assert canonical_url("/") == f"{SITE_ORIGIN}/"


class TestEmittedPages:
    def test_supplied_registry_preserves_order_copy_and_all_language_twins(self) -> None:
        """The registry projection contract applies to supplied entries too."""
        registry = tuple(
            ConnectClient(
                slug=slug,
                title=Localised(nb=f"nb {slug}", en=f"en {slug}", pl=f"pl {slug}"),
                description=Localised(nb=f"nb d {slug}", en=f"en d {slug}", pl=f"pl d {slug}"),
            )
            for slug in ("second-client", "first-client")
        )
        guides = [
            page
            for page in emitted_pages(registry)
            if page.route_path.startswith("/connect/") and page.route_path != "/connect/"
        ]

        assert [page.path for page in guides] == [
            f"{prefix}/connect/{client.slug}/"
            for prefix in ("", "/en", "/pl")
            for client in registry
        ]
        for page in guides:
            client = next(
                client for client in registry if page.route_path == f"/connect/{client.slug}/"
            )
            assert page.template == f"pages/connect-{client.slug}.{page.lang}.html"
            context = page.head_context()
            assert context["title"] == client.title.text(page.lang)
            assert context["description"] == client.description.text(page.lang)
            assert context["language_switch"] == (
                ("nb", f"/connect/{client.slug}/"),
                ("en", f"/en/connect/{client.slug}/"),
                ("pl", f"/pl/connect/{client.slug}/"),
            )

    def test_every_route_in_all_three_languages_where_the_mirror_rule_applies(self) -> None:
        pages = emitted_pages()
        paths = [page.path for page in pages]
        twinned = [p for p in EXPECTED_STATUS if p != "/observatory/"]

        twinned += ["/connect/claude/", "/connect/chatgpt/"]

        expected = [*EXPECTED_STATUS, "/connect/claude/", "/connect/chatgpt/"]
        expected += [en_path(p) for p in twinned] + [f"/pl{p}" for p in twinned]
        assert sorted(paths) == sorted(expected)
        assert len(paths) == len(set(paths))
        assert len(pages) == 43

    def test_emission_order_is_norwegian_then_english_then_polish(self) -> None:
        langs = [page.lang for page in emitted_pages()]
        first_en, first_pl = langs.index("en"), langs.index("pl")

        assert set(langs[:first_en]) == {"nb"}
        assert set(langs[first_en:first_pl]) == {"en"}
        assert set(langs[first_pl:]) == {"pl"}

    def test_twins_name_each_other(self) -> None:
        by_path = {page.path: page for page in emitted_pages()}

        trio = (("nb", "/status/"), ("en", "/en/status/"), ("pl", "/pl/status/"))

        assert by_path["/status/"].twins == trio
        assert by_path["/en/status/"].twins == trio
        assert by_path["/pl/status/"].twins == trio
        assert by_path["/observatory/"].twins == ()
        assert by_path["/en/status/"].lang == "en"
        assert by_path["/pl/status/"].lang == "pl"
        assert by_path["/status/"].lang == "nb"
        assert by_path["/en/status/"].route_path == "/status/"
        assert by_path["/pl/status/"].route_path == "/status/"

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
            ("pl", f"{SITE_ORIGIN}/pl/docs/"),
        )
        assert context["language_switch"] == (
            ("nb", "/docs/"),
            ("en", "/en/docs/"),
            ("pl", "/pl/docs/"),
        )
        assert context["corpus"] is False
        assert context["status"] == "current"
        assert context["title"] and context["description"]

    def test_a_page_without_a_twin_has_no_alternates_and_no_switch(self) -> None:
        page = next(page for page in emitted_pages() if page.path == "/observatory/")
        context = page.head_context()

        assert context["alternates"] == ()
        assert context["language_switch"] == ()

    def test_the_polish_head_is_polish(self) -> None:
        page = next(page for page in emitted_pages() if page.path == "/pl/privacy/")
        context = page.head_context()

        assert context["lang"] == "pl"
        assert context["title"] == "Prywatność"
        assert context["canonical"] == f"{SITE_ORIGIN}/pl/privacy/"

    def test_order_is_deterministic(self) -> None:
        assert [p.path for p in emitted_pages()] == [p.path for p in emitted_pages()]

    def test_the_registry_reaches_the_client_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``emitted_pages`` hands its registry to ``client_routes`` — the hook
        ignores it today, so only the hand-over itself can be observed."""
        handed: list[object] = []

        def hook(registry: object | None = None) -> tuple[SiteRoute, ...]:
            handed.append(registry)
            return ()

        monkeypatch.setattr(routes_module, "client_routes", hook)
        registry = (CLIENT_REGISTRY[0],)

        assert emitted_pages(registry) == emitted_pages()
        assert handed == [registry, None]

    def test_client_pages_are_a_projection_of_the_registry(self) -> None:
        """One registry entry, one guide in each language — the set of pages
        is never the source for the set of clients (ADR:565-572)."""
        guides = {
            page.path
            for page in emitted_pages()
            if page.route_path.startswith("/connect/") and page.route_path != "/connect/"
        }
        only_claude = {
            page.path
            for page in emitted_pages((CLIENT_REGISTRY[0],))
            if page.route_path.startswith("/connect/") and page.route_path != "/connect/"
        }

        assert guides == {
            f"{prefix}/connect/{slug}/"
            for prefix in ("", "/en", "/pl")
            for slug in ("claude", "chatgpt")
        }
        assert only_claude == {"/connect/claude/", "/en/connect/claude/", "/pl/connect/claude/"}
        assert not [
            p
            for p in emitted_pages(())
            if p.route_path.startswith("/connect/") and p.route_path != "/connect/"
        ]
