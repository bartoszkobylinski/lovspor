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
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, Completed
from lovspor.release.envelope import RECORD_NAME
from lovspor.release.errors import RehearsalFailedError
from lovspor.release.staged import (
    OBSERVATORY_URL,
    PROBE_SEGMENT,
    StagedPlan,
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
        self.validate_returncode = 0
        self.tamper: Path | None = None

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        caddyfile = Path(argv[argv.index("--config") + 1])
        assert env[FRAGMENT_ENV]
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


def corpus_of(world: Path) -> Path:
    return world / "www" / "lovspor-releases" / RELEASE_ID / "corpus"


def site_of(world: Path) -> Path:
    return world / "www" / "lovspor-releases" / RELEASE_ID / "site"


class TestThePassingDryRun:
    def test_it_names_every_assertion_it_made(self, world: Path) -> None:
        report = staged_rehearsal(make_plan(world))

        assert [step.name for step in report.steps] == [
            "staged.validate",
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
        assert FLAT_RELEASE in caught.value.detail


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

    def test_a_configuration_caddy_refuses_is_the_first_refusal(self, world: Path) -> None:
        plan = make_plan(world)
        plan.runner.validate_returncode = 1  # type: ignore[union-attr]

        with pytest.raises(RehearsalFailedError) as caught:
            staged_rehearsal(plan)

        assert caught.value.step == "staged.validate"


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
        with pytest.raises(RehearsalFailedError, match="outside"):
            symlinked_component(world / "elsewhere" / "corpus", world / "www")


class TestTheUrlSetTheOldConfigurationOffers:
    """Derived from the old configuration alone, so the new one cannot narrow it."""

    def test_a_tree_offers_its_files_and_its_index_directories(self, world: Path) -> None:
        found = tree_urls(corpus_of(world))

        assert "/robots.txt" in found
        assert "/lov/nl-19140101-001/" in found
        assert "/lov/nl-19140101-001/index.html" not in found
        assert "/sitemaps/sitemap-lover-1.xml" in found

    def test_a_root_index_is_the_root_url(self, world: Path) -> None:
        assert tree_urls(world / "www" / "lovspor") == ("/",)

    def test_a_matcher_path_is_a_url_and_a_trailing_wildcard_is_one_probe(self) -> None:
        assert matcher_urls(("/robots.txt", "/lov/*")) == ("/robots.txt", f"/lov/{PROBE_SEGMENT}")

    def test_a_wildcard_anywhere_but_the_end_is_not_asked_about(self) -> None:
        assert matcher_urls(("/lov/*/paragraf",)) == ()

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
