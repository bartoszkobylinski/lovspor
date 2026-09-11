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
    hosts,
    matcher_paths,
    matches_path,
    roots,
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
        self, previous: object
    ) -> None:
        """The old ``@corpus`` list predates ADR-0013's manifest, so it falls to the landing
        root, which does not hold it. The new configuration answers it — a difference in the
        direction the dry-run allows."""
        answer = answer_for(previous, "/site-manifest.json")

        assert answer is not None
        assert answer.status == NOT_FOUND and answer.served is None


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
