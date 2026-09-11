"""The staged first-migration rehearsal: the URL dry-run (ADR-0014 Validation).

Every configuration is a committed `caddy adapt` capture
(`tests/unit/fixtures/caddy_adapt/`), rehosted onto the world
`staged_fixtures.build_world` materialises under `tmp_path`, and handed to
the rehearsal by a runner that plays `caddy validate` and `caddy adapt`
and nothing else. Nothing here runs Caddy, systemd or the network: the
whole point of the seam is that what decides is Python.

Each negative fixture takes away exactly one of the properties the
dry-run asserts, so the sub-step its refusal names is evidence that the
assertion — and not one of its neighbours — is what detected the change.
"""

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from lovspor.release.answers import Answer, is_servable_url
from lovspor.release.caddy import FRAGMENT_ENV, Completed
from lovspor.release.envelope import RECORD_NAME
from lovspor.release.errors import RehearsalFailedError
from lovspor.release.staged import (
    OBSERVATORY_URL,
    PROBE_SEGMENT,
    StagedPlan,
    _changed,
    _first_difference,
    answers_for,
    candidate_urls,
    digests,
    matcher_urls,
    staged_rehearsal,
    symlinked_component,
    tree_urls,
)
from tests.unit.staged_fixtures import (
    FLAT_RELEASE,
    LIVE_SYMLINK,
    RELEASE_ID,
    build_world,
    load_adapted,
)

REPO = Path(__file__).resolve().parents[2]
LAW_URL = "/lov/nl-19140101-001/"


class FixtureRunner:
    """``caddy validate`` and ``caddy adapt``, answered from the committed captures.

    One queue per Caddyfile; the last entry repeats, so a scenario whose
    second reading must differ says so by pushing two.
    """

    def __init__(self, previous: list[object], proposed: list[object], files: tuple[Path, Path]):
        self.adapted = {files[0]: list(previous), files[1]: list(proposed)}
        self.proposed = files[1]
        self.fragment = files[0].parent / "www" / "lovspor-releases" / RELEASE_ID / "release.caddy"
        self.validate_returncode = 0
        self.tamper: Path | None = None

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        caddyfile = Path(argv[argv.index("--config") + 1])
        assert caddyfile in self.adapted, f"neither Caddyfile: {caddyfile}"
        assert env[FRAGMENT_ENV] == str(self.fragment), env[FRAGMENT_ENV]
        if argv[1] == "validate":
            refusal = "the fixture refuses this file" if self.validate_returncode else ""
            return Completed(self.validate_returncode, "", refusal)
        if self.tamper is not None and caddyfile == self.proposed:
            self.tamper.write_text("tampered\n", encoding="utf-8")
            self.tamper = None
        queue = self.adapted[caddyfile]
        return Completed(0, json.dumps(queue.pop(0) if len(queue) > 1 else queue[0]), "")


@pytest.fixture
def world(tmp_path: Path) -> Path:
    build_world(tmp_path, REPO)
    return tmp_path


def make_plan(world: Path, proposed: str = "proposed.json") -> StagedPlan:
    return plan_for(world, [load_adapted("previous.json", world)], [load_adapted(proposed, world)])


def plan_for(world: Path, previous: list[object], proposed: list[object]) -> StagedPlan:
    """A plan whose runner answers each Caddyfile from its own queue of captures."""
    files = (world / "Caddyfile.previous", world / "Caddyfile.proposed")
    runner = FixtureRunner(previous, proposed, files)
    return StagedPlan(runner, files[0], files[1], world / "www" / "lovspor-releases" / RELEASE_ID)


def dropping(config: object, *indices: int) -> object:
    """One capture with named routes of the site block taken out, by index.

    A hand edit of real `caddy adapt` output, and labelled as one: the
    route shapes below are ones neither Caddyfile produces — a
    configuration that reaches no route at all for a URL — so there is
    nothing to capture.
    """
    routes = config["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]["routes"]  # type: ignore[index]
    for index in sorted(indices, reverse=True):
        del routes[index]
    return config


APP_ROUTE, CORPUS_ROUTE, CATCH_ALL = 1, 2, 3
"""The site block's routes, in the order Caddy adapts both Caddyfiles."""


def hosting(config: object, *names: str) -> object:
    """One capture with the site block matched on ``names``, or on no host at all."""
    route = config["apps"]["http"]["servers"]["srv0"]["routes"][0]  # type: ignore[index]
    if names:
        route["match"] = [{"host": list(names)}]
    else:
        route.pop("match", None)
    return config


def corpus_of(world: Path) -> Path:
    return world / "www" / "lovspor-releases" / RELEASE_ID / "corpus"


def site_of(world: Path) -> Path:
    return world / "www" / "lovspor-releases" / RELEASE_ID / "site"


class TestThePassingDryRun:
    def test_it_names_every_assertion_it_made(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))

        assert [step.name for step in report.steps] == [
            "staged.validate",
            "staged.host",
            "staged.corpus",
            "staged.proxied",
            "staged.site",
            "staged.symlinks",
            "staged.rollback",
        ]

    def test_it_counts_the_corpus_urls_it_compared(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))
        corpus = next(step for step in report.steps if step.name == "staged.corpus")

        assert "8 corpus URLs" in corpus.detail

    def test_the_site_step_names_the_envelopes_site_tree(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))
        site = next(step for step in report.steps if step.name == "staged.site")

        assert site_of(world).as_posix() in site.detail

    def test_a_url_the_new_answers_and_the_old_does_not_is_no_obstacle(self, world: Path) -> None:
        """``/site-manifest.json`` is outside the old matcher: the new configuration
        answers strictly more, which is the direction the dry-run allows."""
        assert staged_rehearsal(make_plan(world)).steps

    def test_the_landing_pages_bytes_may_differ(self, world: Path) -> None:
        """The migrated site is a rebuild of the hand-written page, so only the root
        and the status are asserted for it; its text is the Observatory golden test's."""
        (site_of(world) / "index.html").write_text("<h1>rebuilt</h1>\n", encoding="utf-8")

        assert staged_rehearsal(make_plan(world)).steps


class TestTheHostBothConfigurationsServe:
    def test_a_new_site_block_on_another_name_is_refused(self, world: Path) -> None:
        """The walk takes a ``host`` matcher as satisfied — it is asking about paths — so a
        renamed site block would answer every URL here and none of them on the box."""
        text = json.dumps(load_adapted("proposed.json", world))
        renamed = json.loads(text.replace("lovspor.test", "lovspor.example"))
        plan = plan_for(world, [load_adapted("previous.json", world)], [renamed])

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.host"
        assert caught.value.detail == (
            "the old configuration serves ('lovspor.test',) and the new ('lovspor.example',)"
        )

    def test_an_old_configuration_matching_on_no_host_is_named_as_such(self, world: Path) -> None:
        plan = plan_for(
            world,
            [hosting(load_adapted("previous.json", world))],
            [load_adapted("proposed.json", world)],
        )

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.detail.startswith("the old configuration serves no host and the new")

    def test_a_new_configuration_matching_on_no_host_is_named_as_such(self, world: Path) -> None:
        plan = plan_for(
            world,
            [load_adapted("previous.json", world)],
            [hosting(load_adapted("proposed.json", world))],
        )

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.detail.endswith("and the new no host")

    def test_the_step_lists_every_host_they_both_serve(self, world: Path) -> None:
        names = ("lovspor.test", "www.lovspor.test")
        plan = plan_for(
            world,
            [hosting(load_adapted("previous.json", world), *names)],
            [hosting(load_adapted("proposed.json", world), *names)],
        )

        step = next(one for one in staged_rehearsal(plan).steps if one.name == "staged.host")

        assert step.detail == "both configurations serve lovspor.test, www.lovspor.test"


class TestACorpusUrlTheNewConfigurationDrops:
    def test_it_is_refused_at_the_corpus_step(self, world: Path) -> None:
        plan = make_plan(world, "proposed-drops-robots.json")

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.corpus"
        assert "/robots.txt" in caught.value.detail


class TestACorpusUrlAnsweredFromTheWrongTree:
    def test_the_right_bytes_from_the_flat_release_are_still_refused(self, world: Path) -> None:
        plan = make_plan(world, "proposed-wrong-root.json")

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.corpus"
        assert "is served from" in caught.value.detail
        assert FLAT_RELEASE in caught.value.detail
        assert corpus_of(world).as_posix() in caught.value.detail


class TestASymlinkOnAServingPath:
    def test_identical_answers_through_a_symlink_are_refused(self, world: Path) -> None:
        """The fixture serves the same files with the same names — every URL assertion
        passes — and is one `ln -sfn` away from serving another release."""
        plan = make_plan(world, "proposed-symlinked-root.json")

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.symlinks"
        assert LIVE_SYMLINK in caught.value.detail

    def test_a_symlinked_file_inside_the_envelope_is_refused(self, world: Path) -> None:
        """The same bytes under the same name, so every URL assertion still passes."""
        page = corpus_of(world) / "robots.txt"
        beside = corpus_of(world) / "robots-real.txt"
        beside.write_bytes(page.read_bytes())
        page.unlink()
        page.symlink_to(beside)

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(make_plan(world))

        assert caught.value.step == "staged.symlinks"

    def test_a_symlink_above_the_deployment_root_is_not_the_dry_runs_business(
        self, world: Path
    ) -> None:
        """``/var`` is a symlink on macOS and ``tmp_path`` sits under it. The assertion is
        about the deployment's own symlinks, so it starts at the releases root's parent."""
        assert symlinked_component(corpus_of(world), world / "www") is None


class TestTheRollback:
    def test_a_file_changed_under_the_dry_run_is_refused(self, world: Path) -> None:
        """The record, which no URL serves: a file the URL assertions cannot notice."""
        plan = make_plan(world)
        plan.runner.tamper = plan.release / RECORD_NAME  # type: ignore[union-attr]

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.rollback"
        assert RECORD_NAME in caught.value.detail

    def test_a_file_of_the_old_tree_changed_under_the_dry_run_is_refused(self, world: Path) -> None:
        """The old redirect map: hidden from ``file_server``, so no answer moves with it."""
        plan = make_plan(world)
        plan.runner.tamper = world / "www" / "lovspor-current" / "redirects.caddy"  # type: ignore[union-attr]

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.rollback"
        assert "redirects.caddy changed under the dry-run" in caught.value.detail

    def test_the_previous_configuration_must_still_answer_as_it_did(self, world: Path) -> None:
        moved = load_adapted("proposed.json", world)
        del moved["admin"]  # type: ignore[union-attr]
        plan = plan_for(
            world,
            [load_adapted("previous.json", world), moved],
            [load_adapted("proposed.json", world)],
        )

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.rollback"
        assert caught.value.detail.startswith(
            "the previous configuration no longer answers as it did — /"
        )

    def test_the_previous_configuration_must_come_back_on_tcp(self, world: Path) -> None:
        socketed = load_adapted("previous.json", world)
        socketed["admin"] = {"listen": "unix//run/caddy/admin.sock"}  # type: ignore[index]
        readings = [load_adapted("previous.json", world), socketed]
        plan = plan_for(world, readings, [load_adapted("proposed.json", world)])

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.rollback"
        assert "unix/" in caught.value.detail


class TestTheProxiedSurface:
    def test_a_moved_upstream_is_refused(self, world: Path) -> None:
        text = json.dumps(load_adapted("proposed.json", world))
        moved = json.loads(text.replace("127.0.0.1:8000", "127.0.0.1:9"))
        plan = plan_for(world, [load_adapted("previous.json", world)], [moved])

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.proxied"
        assert "/mcp" in caught.value.detail


class TestTheSiteTree:
    def test_an_envelope_without_the_observatory_page_is_refused(self, world: Path) -> None:
        (site_of(world) / "observatory" / "index.html").unlink()

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(make_plan(world))

        assert caught.value.step == "staged.site"
        assert OBSERVATORY_URL in caught.value.detail


class TestWhatIsCheckedBeforeAnythingIsAdapted:
    def test_an_incomplete_envelope_is_refused(self, world: Path) -> None:
        (world / "www" / "lovspor-releases" / RELEASE_ID / RECORD_NAME).unlink()

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(make_plan(world))

        assert caught.value.step == "staged.validate"
        assert RECORD_NAME in caught.value.detail

    def test_every_missing_part_is_named_in_one_line(self, world: Path) -> None:
        release = world / "www" / "lovspor-releases" / RELEASE_ID
        (release / RECORD_NAME).unlink()
        shutil.rmtree(release / "site")

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(make_plan(world))

        assert f"site/, {RECORD_NAME}" in caught.value.detail

    def test_a_configuration_caddy_refuses_is_the_first_refusal(self, world: Path) -> None:
        plan = make_plan(world)
        plan.runner.validate_returncode = 1  # type: ignore[union-attr]

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.validate"
        assert "the fixture refuses this file" in caught.value.detail

    def test_the_step_names_both_files_it_had_caddy_accept(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))

        assert report.steps[0].detail == (
            "caddy validate accepts Caddyfile.previous and Caddyfile.proposed"
        )


class TestDigests:
    def test_every_file_under_a_tree_by_its_bytes(self, world: Path) -> None:
        taken = dict(digests((corpus_of(world),)))
        robots = corpus_of(world) / "robots.txt"

        assert taken[robots.as_posix()] == hashlib.sha256(robots.read_bytes()).hexdigest()

    def test_two_readings_of_an_untouched_tree_agree(self, world: Path) -> None:
        assert digests((corpus_of(world),)) == digests((corpus_of(world),))

    def test_a_changed_byte_changes_the_reading(self, world: Path) -> None:
        before = digests((corpus_of(world),))
        (corpus_of(world) / "robots.txt").write_text("User-agent: x\n", encoding="utf-8")

        assert digests((corpus_of(world),)) != before


class TestSymlinkedComponent:
    def test_it_names_the_first_symlink_below_the_boundary(self, world: Path) -> None:
        found = symlinked_component(world / "www" / LIVE_SYMLINK / "corpus", world / "www")

        assert found == world / "www" / LIVE_SYMLINK

    def test_a_path_the_boundary_is_no_ancestor_of_is_refused(self, world: Path) -> None:
        with pytest.raises(RehearsalFailedError) as caught:
            symlinked_component(world / "elsewhere" / "corpus", world / "www")

        assert caught.value.step == "staged.symlinks"
        assert "outside the deployment" in caught.value.detail


class TestTheUrlSetTheOldConfigurationOffers:
    """Derived from the old configuration alone, so the new one cannot narrow it."""

    def test_a_tree_offers_its_files_and_its_index_directories(self, world: Path) -> None:
        found = tree_urls(corpus_of(world))

        assert "/robots.txt" in found
        assert "/lov/nl-19140101-001/" in found
        assert "/lov/nl-19140101-001/index.html" not in found
        assert "/sitemaps/sitemap-lover-1.xml" in found

    def test_a_root_index_is_the_root_url(self, world: Path) -> None:
        assert tree_urls(world / "www" / "lovspor") == ("/", "/om/")

    def test_a_matcher_path_is_a_url_and_a_trailing_wildcard_is_one_probe(self) -> None:
        assert matcher_urls(("/robots.txt", "/lov/*")) == ("/robots.txt", f"/lov/{PROBE_SEGMENT}")

    @pytest.mark.parametrize("pattern", ["/lov/*/paragraf", "/lov/*x"])
    def test_a_wildcard_anywhere_but_the_end_is_not_asked_about(self, pattern: str) -> None:
        assert matcher_urls((pattern,)) == ()

    def test_a_matcher_that_is_not_a_path_is_not_asked_about(self) -> None:
        """``*`` alone, or anything without a leading slash, is not a URL this can ask."""
        assert matcher_urls(("*", "lov")) == ()

    def test_candidates_come_from_both_the_trees_and_the_matchers(self, world: Path) -> None:
        found = candidate_urls(load_adapted("previous.json", world))

        assert "/lov/nl-19140101-001/" in found
        assert "/mcp" in found and "/" in found
        assert list(found) == list(dict.fromkeys(found))

    def test_a_root_that_is_not_a_directory_contributes_nothing(self, world: Path) -> None:
        (world / "www" / "lovspor-current").unlink()

        found = candidate_urls(load_adapted("previous.json", world))

        assert "/lov/nl-19140101-001/" not in found
        assert "/robots.txt" in found  # still named by the old @corpus matcher


class TestAnswersFor:
    def test_a_url_no_route_reaches_is_left_out(self, world: Path) -> None:
        config = {"apps": {"http": {"servers": {"srv0": {"routes": []}}}}}

        assert answers_for(config, ("/", "/robots.txt")) == {}

    def test_each_url_is_asked_once_and_keyed_by_itself(self, world: Path) -> None:
        found = answers_for(load_adapted("previous.json", world), ("/", "/robots.txt"))

        assert sorted(found) == ["/", "/robots.txt"]
        assert found["/robots.txt"].served == "robots.txt"


class TestASiteUrlAnsweredFromTheWrongTree:
    def test_the_landing_page_from_outside_the_envelope_is_refused(self, world: Path) -> None:
        """It answers 200 with the right text — from the tree the migration is retiring."""
        plan = make_plan(world, "proposed-wrong-site-root.json")

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.site"
        assert "is served from" in caught.value.detail
        assert site_of(world).as_posix() in caught.value.detail


class TestTheMapTheNewConfigurationServesUnder:
    def test_the_old_trees_redirect_map_is_refused(self, world: Path) -> None:
        """Every response compares equal — the old map is what the old answers came from —
        so only Caddy's own record of what the file imported catches this."""
        plan = make_plan(world, "proposed-foreign-map.json")

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.corpus"
        assert "not the release's own" in caught.value.detail
        assert "lovspor-current" in caught.value.detail


class TestAConfigurationThatReachesNoRouteAtAll:
    def test_a_corpus_url_no_route_reaches_is_refused(self, world: Path) -> None:
        stripped = dropping(load_adapted("proposed.json", world), CORPUS_ROUTE, CATCH_ALL)
        plan = plan_for(world, [load_adapted("previous.json", world)], [stripped])

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.corpus"
        assert "the new nothing" in caught.value.detail

    def test_a_site_url_no_route_reaches_is_refused(self, world: Path) -> None:
        stripped = dropping(load_adapted("proposed.json", world), CATCH_ALL)
        plan = plan_for(world, [load_adapted("previous.json", world)], [stripped])

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.site"
        assert "answers nothing" in caught.value.detail

    def test_an_old_configuration_with_no_corpus_at_all_is_refused(self, world: Path) -> None:
        """Nothing to compare is not a pass: it is the dry-run reading the wrong old file."""
        stripped = dropping(load_adapted("previous.json", world), CORPUS_ROUTE)
        plan = plan_for(world, [stripped], [load_adapted("proposed.json", world)])

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.corpus"
        assert caught.value.detail == "the old configuration answers no corpus URL at all"


class TestTheStepsCountWhatTheyChecked:
    def test_the_site_step_counts_the_two_named_urls_and_the_old_tree(self, world: Path) -> None:
        """``/om/`` is in the class because the old configuration answers it, not by name."""
        report = staged_rehearsal(make_plan(world))
        site = next(step for step in report.steps if step.name == "staged.site")

        assert site.detail.startswith("3 site URLs")

    def test_the_symlink_step_counts_the_roots_as_well_as_the_files(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))
        symlinks = next(step for step in report.steps if step.name == "staged.symlinks")

        assert symlinks.detail.startswith("11 serving paths")

    def test_the_proxied_step_counts_the_app_surface(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))
        proxied = next(step for step in report.steps if step.name == "staged.proxied")

        assert proxied.detail.startswith("6 proxied URLs")

    def test_the_rollback_step_counts_the_files_it_found(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))
        rollback = next(step for step in report.steps if step.name == "staged.rollback")

        assert "files as found" in rollback.detail
        assert rollback.detail.split()[-4].isdigit()


class TestTheTwoMessagesTheRollbackComposes:
    def _answer(self, status: int) -> Answer:
        return Answer(
            handler="file_server",
            root="/var/www/x",
            served="robots.txt",
            digest="d",
            status=status,
            location=None,
            upstream=None,
        )

    def test_the_first_url_whose_answer_moved(self) -> None:
        before = {"/a": self._answer(200), "/b": self._answer(200)}
        after = {"/a": self._answer(200), "/b": self._answer(410)}

        assert _first_difference(before, after).startswith("/b: file_server 410")

    def test_a_url_the_previous_configuration_stopped_answering(self) -> None:
        before = {"/a": self._answer(200)}

        assert "nothing, not file_server 200" in _first_difference(before, {})

    def test_the_first_file_whose_bytes_moved(self) -> None:
        taken = (("/a", "one"), ("/b", "two"))

        assert _changed(taken, (("/a", "one"), ("/b", "three"))) == "/b"

    def test_the_first_by_name_when_two_files_moved(self) -> None:
        assert _changed((("/a", "one"),), (("/b", "two"),)) == "/a"

    def test_urls_the_previous_configuration_gained_are_counted(self) -> None:
        """Reachable only by calling this directly: the caller passes the same key set."""
        answer = self._answer(200)

        assert _first_difference({"/a": answer}, {"/a": answer, "/b": answer}).startswith("1 URLs")

    def test_two_identical_readings_name_nothing(self) -> None:
        taken = (("/a", "one"),)

        assert _changed(taken, taken) == "nothing"


class TestTheDeriversAskNothingTheEvaluatorRefuses:
    """One predicate, both ends: a dry-run must never fail on its own question."""

    @pytest.mark.parametrize("name", ["q?x.html", "a%2e.html", "b#c.html", "d\\e.html"])
    def test_a_file_name_that_was_never_a_url_is_skipped(self, world: Path, name: str) -> None:
        (corpus_of(world) / name).write_text("x\n", encoding="utf-8")

        assert all(is_servable_url(url) for url in tree_urls(corpus_of(world)))
        assert f"/{name}" not in tree_urls(corpus_of(world))

    def test_every_url_a_tree_offers_is_one_the_evaluator_answers(self, world: Path) -> None:
        found = tree_urls(corpus_of(world))

        assert found
        assert all(is_servable_url(url) for url in found)

    @pytest.mark.parametrize("pattern", ["//etc/passwd", "/lov/../x", "/lov?x", "*", "lov"])
    def test_a_matcher_that_is_not_a_servable_url_is_dropped(self, pattern: str) -> None:
        assert matcher_urls((pattern,)) == ()

    def test_every_candidate_of_the_old_configuration_is_askable(self, world: Path) -> None:
        found = candidate_urls(load_adapted("previous.json", world))

        assert found
        assert all(is_servable_url(url) for url in found)


class TestACaddyfileTheDryRunCannotRead:
    """Named here, before Caddy is asked, so the refusal is the file and not caddy's stderr.

    The world's state, so exit 1 and a named refusal — the convention every
    command in ``commands.py`` follows. The CLI judges the request alone.
    """

    def test_a_configuration_that_is_not_there(self, world: Path) -> None:
        plan = make_plan(world)
        plan.previous.unlink()

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.validate"
        assert caught.value.detail == (f"the previous Caddyfile {plan.previous} is not a file")

    def test_a_directory_where_a_configuration_should_be(self, world: Path) -> None:
        plan = make_plan(world)
        plan.proposed.unlink()
        plan.proposed.mkdir()

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.validate"
        assert caught.value.detail == (f"the proposed Caddyfile {plan.proposed} is a directory")

    def test_a_configuration_the_dry_run_may_not_open(self, world: Path) -> None:
        plan = make_plan(world)
        plan.previous.chmod(0o000)
        if os.access(plan.previous, os.R_OK):
            pytest.skip("this identity reads a 0000 file; the mode cannot express the refusal")

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.detail.endswith("is not readable")

    def test_both_configurations_are_checked(self, world: Path) -> None:
        """The proposed one too: only checking the first leaves half the run unguarded."""
        plan = make_plan(world)
        plan.proposed.unlink()

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert "proposed Caddyfile" in caught.value.detail

    def test_an_incomplete_envelope_is_named_before_either_configuration(self, world: Path) -> None:
        """The envelope is what the comparison is against; a missing tree is the first word."""
        plan = make_plan(world)
        plan.previous.unlink()
        (plan.release / RECORD_NAME).unlink()

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert RECORD_NAME in caught.value.detail
