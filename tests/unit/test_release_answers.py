"""What an adapted configuration answers, read off real ``caddy adapt`` JSON.

Every configuration here is a committed capture of Caddy's own adapter
(``tests/unit/fixtures/caddy_adapt/``, provenance in its README) over the
world ``staged_fixtures.build_world`` builds — the same world, materialised
under ``tmp_path``, so a question about a URL is answered from files that
are really there. The toy adapter of ``caddy_fakes`` cannot serve here: it
models no URL matching, which is the whole subject.

The handful of route shapes neither Caddyfile produces — a hidden file, a
matcher the dry-run does not model, a second route in a consumed group —
are hand-built dicts, because they are about the evaluator and not about
either Caddyfile.
"""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from lovspor.release.answers import (
    NOT_FOUND,
    Answer,
    answer_for,
    contained,
    hidden_paths,
    hosts,
    is_servable_url,
    matcher_paths,
    matches_path,
    roots,
    served_file,
)
from lovspor.release.errors import UnroutableConfigError
from tests.unit.staged_fixtures import (
    GONE_PREFIX,
    REDIRECT_SOURCE,
    REDIRECT_TARGET,
    RELEASE_ID,
    build_world,
    load_adapted,
)

REPO = Path(__file__).resolve().parents[2]
LAW_URL = "/lov/nl-19140101-001/"
LAW_FILE = "lov/nl-19140101-001/index.html"


@pytest.fixture
def world(tmp_path: Path) -> Path:
    build_world(tmp_path, REPO)
    return tmp_path


@pytest.fixture
def previous(world: Path) -> object:
    return load_adapted("previous.json", world)


@pytest.fixture
def proposed(world: Path) -> object:
    return load_adapted("proposed.json", world)


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def site(world: Path, *parts: str) -> Path:
    return world.joinpath("www", *parts)


class TestWhatTheOldConfigurationAnswers:
    def test_a_corpus_page_comes_from_the_symlink(self, previous: object, world: Path) -> None:
        answer = answer_for(previous, LAW_URL)

        assert answer == Answer(
            handler="file_server",
            root=site(world, "lovspor-current").as_posix(),
            served=LAW_FILE,
            digest=digest_of(site(world, "lovspor-current", LAW_FILE)),
            status=200,
            location=None,
            upstream=None,
        )

    def test_the_landing_page_comes_from_its_own_root(self, previous: object, world: Path) -> None:
        answer = answer_for(previous, "/")

        assert answer is not None
        assert answer.root == site(world, "lovspor").as_posix()
        assert answer.served == "index.html"

    def test_a_retired_slug_is_a_301_to_its_successor(self, previous: object) -> None:
        answer = answer_for(previous, REDIRECT_SOURCE)

        assert answer is not None
        assert (answer.handler, answer.status, answer.location) == (
            "static_response",
            301,
            REDIRECT_TARGET,
        )

    @pytest.mark.parametrize("url", [GONE_PREFIX, GONE_PREFIX + "paragraf-1/"])
    def test_a_retired_namespace_is_410_through_its_wildcard(
        self, previous: object, url: str
    ) -> None:
        answer = answer_for(previous, url)

        assert answer is not None
        assert (answer.handler, answer.status) == ("static_response", 410)

    def test_the_app_paths_reach_the_proxy(self, previous: object) -> None:
        answer = answer_for(previous, "/mcp")

        assert answer is not None
        assert (answer.handler, answer.upstream) == ("reverse_proxy", "127.0.0.1:8000")

    def test_the_manifest_is_outside_the_old_matcher_and_is_not_answered(
        self, previous: object, world: Path
    ) -> None:
        """The old ``@corpus`` list predates ADR-0013's manifest, so it falls to the landing
        root, which does not hold it. The new configuration answers it — a difference in the
        direction the dry-run allows."""
        answer = answer_for(previous, "/site-manifest.json")

        assert answer == Answer(
            handler="file_server",
            root=site(world, "lovspor").as_posix(),
            served=None,
            digest=None,
            status=NOT_FOUND,
            location=None,
            upstream=None,
        )


class TestWhatTheNewConfigurationAnswers:
    def test_a_corpus_page_comes_from_the_envelopes_own_tree(
        self, proposed: object, world: Path
    ) -> None:
        answer = answer_for(proposed, LAW_URL)

        assert answer is not None
        assert answer.root == site(world, "lovspor-releases", RELEASE_ID, "corpus").as_posix()
        assert answer.served == LAW_FILE

    def test_the_landing_and_observatory_pages_come_from_the_envelopes_site_tree(
        self, proposed: object, world: Path
    ) -> None:
        expected = site(world, "lovspor-releases", RELEASE_ID, "site").as_posix()

        assert [answer_for(proposed, url) for url in ("/", "/observatory/")] == [
            Answer(
                handler="file_server",
                root=expected,
                served=name,
                digest=digest_of(Path(expected) / name),
                status=200,
                location=None,
                upstream=None,
            )
            for name in ("index.html", "observatory/index.html")
        ]

    def test_the_manifest_is_inside_the_new_matcher(self, proposed: object) -> None:
        answer = answer_for(proposed, "/site-manifest.json")

        assert answer is not None
        assert answer.status == 200 and answer.served == "site-manifest.json"

    def test_the_same_bytes_from_a_different_tree_is_the_same_response(
        self, previous: object, proposed: object
    ) -> None:
        """The one difference the migration is FOR: the root moves, the answer does not."""
        old, new = answer_for(previous, LAW_URL), answer_for(proposed, LAW_URL)

        assert old is not None and new is not None
        assert old != new
        assert old.same_response(new)


class TestTheEvaluatorsOwnRules:
    def test_a_hidden_file_is_not_served(self, world: Path) -> None:
        root = site(world, "lovspor-releases", RELEASE_ID, "corpus")
        config = _one_route(
            [
                {"handler": "vars", "root": root.as_posix()},
                {"handler": "file_server", "hide": [(root / "robots.txt").as_posix()]},
            ]
        )

        answer = answer_for(config, "/robots.txt")

        assert answer is not None
        assert answer.status == NOT_FOUND and answer.served is None

    def test_a_matcher_the_dry_run_does_not_model_is_refused(self) -> None:
        config = _one_route([{"handler": "file_server"}], match=[{"expression": "true"}])

        with pytest.raises(UnroutableConfigError, match="expression"):
            answer_for(config, "/")

    def test_a_handler_the_dry_run_does_not_model_is_refused(self) -> None:
        config = _one_route([{"handler": "templates"}])

        with pytest.raises(UnroutableConfigError, match="templates"):
            answer_for(config, "/")

    def test_a_url_that_climbs_out_of_the_root_is_refused(self, proposed: object) -> None:
        with pytest.raises(UnroutableConfigError, match="not a served URL"):
            answer_for(proposed, "/lov/../../etc/passwd")

    def test_an_absolute_path_disguised_as_a_url_is_refused(self, world: Path) -> None:
        """A second leading slash must not make the root-relative target absolute."""
        root = site(world, "lovspor")
        config = _one_route(
            [
                {"handler": "vars", "root": root.as_posix()},
                {"handler": "file_server"},
            ]
        )

        with pytest.raises(UnroutableConfigError, match="not a served URL"):
            answer_for(config, "//etc/passwd")

    def test_only_the_first_matching_route_of_a_group_runs(self, world: Path) -> None:
        """``handle`` blocks are mutually exclusive; Caddy marks them with one group name."""
        first = {"handler": "static_response", "status_code": 410}
        second = {"handler": "static_response", "status_code": 301}
        config = _routes(
            [
                {"group": "g", "match": [{"path": ["/lov/*"]}], "handle": [first]},
                {"group": "g", "match": [{"path": ["/lov/*"]}], "handle": [second]},
            ]
        )

        answer = answer_for(config, LAW_URL)

        assert answer is not None and answer.status == 410

    def test_a_configuration_no_route_answers_yields_nothing(self) -> None:
        assert answer_for(_routes([]), "/") is None

    def test_a_configuration_without_servers_yields_nothing(self) -> None:
        assert answer_for({"admin": {"listen": "unix//run/caddy/admin.sock"}}, "/") is None


class TestPathMatching:
    @pytest.mark.parametrize(
        ("pattern", "url", "matched"),
        [
            ("/lov", "/lov", True),
            ("/lov", "/lov/", False),
            ("/lov/*", "/lov/", True),
            ("/lov/*", "/lov/nl-1/", True),
            ("/lov/*", "/lov", False),
            ("/lov/*", "/forskrift/x", False),
            ("/robots.txt", "/robots.txt", True),
            ("/robots.txt", "/Robots.TXT", True),
        ],
    )
    def test_caddys_path_matcher(self, pattern: str, url: str, matched: bool) -> None:
        assert matches_path((pattern,), url) is matched

    def test_any_one_pattern_is_enough(self) -> None:
        assert matches_path(("/forskrift", "/lov/*"), LAW_URL) is True

    def test_no_patterns_match_nothing(self) -> None:
        assert matches_path((), LAW_URL) is False


class TestReadingTheRoutesThemselves:
    def test_every_root_the_configuration_names(self, proposed: object, world: Path) -> None:
        release = site(world, "lovspor-releases", RELEASE_ID)

        assert roots(proposed) == (
            (release / "corpus").as_posix(),
            (release / "site").as_posix(),
        )

    def test_the_old_configuration_names_the_symlink_and_the_landing_root(
        self, previous: object, world: Path
    ) -> None:
        assert roots(previous) == (
            site(world, "lovspor-current").as_posix(),
            site(world, "lovspor").as_posix(),
        )

    def test_every_path_a_matcher_names(self, previous: object) -> None:
        found = matcher_paths(previous)

        assert "/mcp/*" in found and "/robots.txt" in found
        assert REDIRECT_SOURCE in found and GONE_PREFIX + "*" in found

    def test_both_configurations_name_the_same_host(
        self, previous: object, proposed: object
    ) -> None:
        assert hosts(previous) == hosts(proposed) == ("lovspor.test",)


def _routes(routes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"apps": {"http": {"servers": {"srv0": {"routes": routes}}}}}


def _one_route(handle: list[dict[str, Any]], match: Any = None) -> dict[str, Any]:
    route: dict[str, Any] = {"handle": handle}
    if match is not None:
        route["match"] = match
    return _routes([route])


class TestResolvingAFileUnderARoot:
    def test_a_directory_url_resolves_to_its_index(self, world: Path) -> None:
        root = site(world, "lovspor-current")

        assert served_file(root, LAW_URL) == root / LAW_FILE

    def test_a_named_file_resolves_to_itself(self, world: Path) -> None:
        root = site(world, "lovspor-current")

        assert served_file(root, "/robots.txt") == root / "robots.txt"

    def test_the_root_url_resolves_to_the_roots_own_index(self, world: Path) -> None:
        root = site(world, "lovspor")

        assert served_file(root, "/") == root / "index.html"

    def test_a_directory_without_an_index_resolves_to_nothing(self, world: Path) -> None:
        assert served_file(site(world, "lovspor-current"), "/lov/") is None

    def test_a_name_no_file_carries_resolves_to_nothing(self, world: Path) -> None:
        assert served_file(site(world, "lovspor"), "/nowhere.txt") is None


class TestWhatTheConfigurationHides:
    def test_the_imported_snippets_and_the_caddyfile_itself(self, proposed: object) -> None:
        found = hidden_paths(proposed)

        assert any(name.endswith("/corpus/redirects.caddy") for name in found)
        assert any(name.endswith("/release.caddy") for name in found)

    def test_a_configuration_that_hides_nothing_names_nothing(self) -> None:
        assert hidden_paths({"apps": {}}) == ()


class TestDescribingAnAnswer:
    def test_a_served_file_names_its_root(self) -> None:
        answer = Answer(
            handler="file_server",
            root="/var/www/x",
            served="robots.txt",
            digest="d",
            status=200,
            location=None,
            upstream=None,
        )

        assert answer.describe() == "file_server 200 robots.txt from /var/www/x"

    def test_a_static_response_says_it_serves_no_file(self) -> None:
        answer = Answer(
            handler="static_response",
            root=None,
            served=None,
            digest=None,
            status=410,
            location=None,
            upstream=None,
        )

        assert answer.describe() == "static_response 410 no file from no root"


class TestJsonTheWalkCannotRead:
    """Refused by name, never skipped: what a walk cannot read it cannot report on."""

    def test_a_route_that_is_not_an_object(self) -> None:
        with pytest.raises(UnroutableConfigError, match="not a route"):
            answer_for(_routes(["a route"]), "/")  # type: ignore[list-item]

    def test_a_handler_that_is_not_an_object(self) -> None:
        with pytest.raises(UnroutableConfigError, match="not a handler"):
            answer_for(_one_route(["file_server"]), "/")  # type: ignore[list-item]

    def test_a_matcher_set_that_is_not_an_object(self) -> None:
        with pytest.raises(UnroutableConfigError, match="not a matcher set"):
            answer_for(_one_route([{"handler": "file_server"}], match=["/lov"]), "/")


class TestHandlersWithAShapeCaddyDoesNotEmit:
    def test_a_location_that_is_not_a_list_is_not_read(self) -> None:
        config = _one_route(
            [{"handler": "static_response", "status_code": 301, "headers": {"Location": "/x"}}]
        )

        answer = answer_for(config, "/")

        assert answer is not None and answer.location is None

    @pytest.mark.parametrize("upstreams", ["127.0.0.1:8000", [], None])
    def test_upstreams_caddy_would_not_write_name_no_upstream(self, upstreams: Any) -> None:
        config = _one_route([{"handler": "reverse_proxy", "upstreams": upstreams}])

        answer = answer_for(config, "/")

        assert answer is not None and answer.upstream is None

    def test_a_status_code_that_is_not_a_number_is_a_404(self) -> None:
        config = _one_route([{"handler": "static_response", "status_code": "410"}])

        answer = answer_for(config, "/")

        assert answer is not None and answer.status == NOT_FOUND

    def test_a_proxied_url_is_answered_and_the_status_is_the_upstreams_to_give(self) -> None:
        """The dry-run cannot know what the upstream replies; both sides get the same
        placeholder, and `_answered` must not read it as a 404."""
        config = _one_route([{"handler": "reverse_proxy", "upstreams": [{"dial": "127.0.0.1:1"}]}])

        answer = answer_for(config, "/")

        assert answer is not None and answer.status == 200

    def test_a_file_server_with_no_root_in_effect_answers_nothing(self) -> None:
        answer = answer_for(_one_route([{"handler": "file_server"}]), "/")

        assert answer is not None
        assert answer.status == NOT_FOUND and answer.root is None

    def test_a_file_server_whose_hide_is_not_a_list_hides_nothing(self, world: Path) -> None:
        root = site(world, "lovspor")
        config = _one_route(
            [
                {"handler": "vars", "root": root.as_posix()},
                {"handler": "file_server", "hide": "index.html"},
            ]
        )

        answer = answer_for(config, "/")

        assert answer is not None and answer.status == 200

    def test_a_hidden_bare_name_hides_the_file_wherever_it_sits(self, world: Path) -> None:
        root = site(world, "lovspor")
        config = _one_route(
            [
                {"handler": "vars", "root": root.as_posix()},
                {"handler": "file_server", "hide": ["index.html"]},
            ]
        )

        answer = answer_for(config, "/")

        assert answer is not None and answer.status == NOT_FOUND

    def test_a_file_server_that_hides_nothing_at_all_serves(self, world: Path) -> None:
        assert hidden_paths({"handler": "file_server"}) == ()


class TestAConsumedGroupIsSkipped:
    def test_a_matched_group_member_that_answers_nothing_still_consumes_its_group(
        self, world: Path
    ) -> None:
        """Caddy's ``handle`` blocks are mutually exclusive whether or not the chosen one
        responds — so a later member of the same group must not be reached."""
        root = site(world, "lovspor").as_posix()
        config = _routes(
            [
                {"group": "g", "handle": [{"handler": "vars", "root": root}]},
                {"group": "g", "handle": [{"handler": "static_response", "status_code": 410}]},
            ]
        )

        assert answer_for(config, "/") is None

    def test_a_route_outside_the_group_is_still_reached(self, world: Path) -> None:
        root = site(world, "lovspor").as_posix()
        config = _routes(
            [
                {"group": "g", "handle": [{"handler": "vars", "root": root}]},
                {"group": "g", "handle": [{"handler": "static_response", "status_code": 410}]},
                {"handle": [{"handler": "file_server"}]},
            ]
        )

        answer = answer_for(config, "/")

        assert answer is not None and answer.status == 200


class TestUrlsWithAwkwardNames:
    def test_a_leading_segment_is_not_eaten_character_by_character(self, world: Path) -> None:
        """`lstrip` takes a character SET; a name starting with one of them would lose it."""
        root = site(world, "lovspor")
        (root / "X.txt").write_text("x\n", encoding="utf-8")

        assert served_file(root, "/X.txt") == root / "X.txt"


class TestWhatCountsAsAUrlToAskAbout:
    """One rule set, stated once: the evaluator refuses every input the derivers skip.

    The dry-run's URL set comes from filenames and from Caddy matcher
    literals, so an input outside that shape is not a URL this model can
    answer — it is a question about something else. Each refusal names the
    input and the rule it broke, and each shape below reaches an answer
    only by escaping the root the answer would claim to come from.
    """

    @pytest.fixture
    def rooted(self, world: Path) -> object:
        return _one_route(
            [
                {"handler": "vars", "root": site(world, "lovspor").as_posix()},
                {"handler": "file_server"},
            ]
        )

    @pytest.mark.parametrize(
        ("url", "rule"),
        [
            ("", "a served URL is absolute"),
            ("lov/nl-1/", "a served URL is absolute"),
            ("http://lovspor.test/lov/nl-1/", "a served URL is absolute"),
            ("//etc/passwd", "a second leading slash is a network-path reference, not a path"),
            ("/lov/../../etc/passwd", ".. climbs out of the root"),
            ("/../etc/passwd", ".. climbs out of the root"),
            ("/lov/./nl-1/", ". is not a path segment"),
            ("/./lov/", ". is not a path segment"),
            ("/lov//nl-1/", "an empty path segment"),
            ("/lov//", "an empty path segment"),
            ("/lov/%2e%2e%2fetc/passwd", "percent-encoding is not decoded here"),
            ("/lov\\..\\..\\etc/passwd", "a backslash is not a path separator here"),
            ("/lov/nl-1/?x=1", "a query string is not part of the path"),
            ("/lov/nl-1/#top", "a fragment is not part of the path"),
        ],
    )
    def test_a_shape_that_is_not_a_path_is_refused_by_name(
        self, rooted: object, url: str, rule: str
    ) -> None:
        """The whole message, not a substring of it: a refusal that does not say which rule
        it applied leaves the operator to guess, and the first offending segment is as much
        a refusal as the last."""
        with pytest.raises(UnroutableConfigError) as caught:
            answer_for(rooted, url)

        assert str(caught.value) == f"not a served URL ({rule}): {url!r}"

    @pytest.mark.parametrize("url", ["/", "/robots.txt", "/lov/nl-19140101-001/", "/en/"])
    def test_the_shapes_the_derivers_produce_are_asked(self, previous: object, url: str) -> None:
        assert answer_for(previous, url) is not None

    def test_a_trailing_slash_is_the_one_thing_resolved_rather_than_refused(
        self, world: Path
    ) -> None:
        """Caddy's own ``file_server`` serves a directory's index; that is not a URL rewrite,
        and ``/lov`` and ``/lov/`` stay two different URLs to every matcher."""
        root = site(world, "lovspor-current")

        assert served_file(root, "/lov/nl-19140101-001/") == root / LAW_FILE
        assert is_servable_url("/lov") and is_servable_url("/lov/")


class TestContainment:
    def test_a_symlink_inside_the_root_that_lands_outside_it_is_refused(self, world: Path) -> None:
        """Name-level containment is not containment: the join stays under the root and the
        resolution does not. Proven after resolving both sides, never by string prefix."""
        root = site(world, "lovspor")
        (root / "escape.txt").symlink_to(world / "www" / "lovspor-current" / "robots.txt")

        with pytest.raises(UnroutableConfigError) as caught:
            served_file(root, "/escape.txt")

        assert str(caught.value) == f"not a served URL (it resolves outside {root}): '/escape.txt'"

    def test_a_directorys_index_that_lands_outside_the_root_is_refused(self, world: Path) -> None:
        """The second join is checked too: the escape can be the index file, not the path."""
        root = site(world, "lovspor")
        (root / "kapittel").mkdir()
        (root / "kapittel" / "index.html").symlink_to(site(world, "lovspor-current", "robots.txt"))

        with pytest.raises(UnroutableConfigError) as caught:
            served_file(root, "/kapittel/")

        assert str(caught.value) == f"not a served URL (it resolves outside {root}): '/kapittel/'"

    def test_a_sibling_root_sharing_a_name_prefix_is_outside(self, world: Path) -> None:
        """``/var/www/lovspor-current`` starts with ``/var/www/lovspor`` and is another tree."""
        root = site(world, "lovspor")
        neighbour = site(world, "lovspor-current") / "robots.txt"

        assert not contained(root, neighbour)
        assert contained(root, root / "index.html")

    def test_the_root_itself_is_inside_itself(self, world: Path) -> None:
        root = site(world, "lovspor")

        assert contained(root, root)

    def test_a_root_that_is_a_symlink_still_contains_its_own_files(self, world: Path) -> None:
        """The OLD configuration serves through ``lovspor-current``; resolving both sides is
        what keeps that legitimate while the escape above is not."""
        root = site(world, "lovspor-current")

        assert contained(root, root / "robots.txt")
