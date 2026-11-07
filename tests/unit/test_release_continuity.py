"""URL continuity enforced inside the release transaction, fail-closed (#820).

Two real envelopes: A serves ``/lov/testloven/``; B renames it to
``/lov/nyloven/`` and 301s every old URL. Each test gets its own releases
root and Caddy host in a box (``control_host``). A candidate is broken by
editing its own ``corpus/`` the way a faulty build would leave it; the live
release is damaged the way a disk or a hand edit would.
"""

import fcntl
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.release import commands, control
from lovspor.release.continuity import require_continuity, transaction_lock
from lovspor.release.control import ControlPlane, commit_release, read_triple, rollback
from lovspor.release.envelope import CORPUS_DIR, MARKER_NAME, Marker, read_marker
from lovspor.release.errors import (
    ContinuityRefusedError,
    IncompleteEnvelopeError,
    TransactionLockedError,
    UnreconciledError,
)
from lovspor.release.reconcile import prune, reconcile
from lovspor.site.fingerprint import release_content_id
from tests.unit.cli_output import said
from tests.unit.control_host import Host, make_host, two_envelopes
from tests.unit.release_fixtures import World, make_world

runner = CliRunner()

OLD = "/lov/testloven/"
NEW = "/lov/nyloven/"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def envelopes(world: World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    return two_envelopes(world, tmp_path_factory.mktemp("source") / "releases")


@pytest.fixture
def host(envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    return make_host(envelopes, tmp_path, monkeypatch)


@pytest.fixture
def live_a(host: Host) -> Host:
    host.make_live(host.a)
    return host


@pytest.fixture
def live_b(host: Host) -> Host:
    host.make_live(host.b, previous=host.a)
    return host


def _corpus(host: Host, content_id: str) -> Path:
    return host.releases / content_id / CORPUS_DIR


def _edit_map(host: Host, content_id: str, edit: Callable[[dict[str, Any]], None]) -> None:
    path = _corpus(host, content_id) / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def _drop_redirects(*sources: str) -> Callable[[dict[str, Any]], None]:
    def edit(data: dict[str, Any]) -> None:
        data["redirects"] = [r for r in data["redirects"] if r["from"] not in sources]

    return edit


def _refused_untouched(host: Host, content_id: str, match: str) -> ContinuityRefusedError:
    """Commit ``content_id``, expect the refusal, and prove nothing public moved."""
    before = host.snapshot()
    running = host.running()
    with pytest.raises(ContinuityRefusedError, match=match) as caught:
        commit_release(host.plane, content_id)
    assert host.snapshot() == before
    assert host.running() == running
    assert host.caddy.reloads == 0
    assert not host.plane.next_fragment.exists()
    return caught.value


class TestAcceptedReleases:
    def test_a_rename_that_301s_every_old_url_goes_live(self, live_a: Host) -> None:
        report = commit_release(live_a.plane, live_a.b)

        assert (report.active, report.previous, report.already_live) == (live_a.b, live_a.a, False)
        assert read_marker(live_a.releases) == Marker(active=live_a.b, previous=live_a.a)
        assert live_a.running() == live_a.pair_of(live_a.b)

    def test_retired_urls_answering_410_go_live(self, live_a: Host) -> None:
        """Retiring is a legitimate fate: dropping a page's 301 and its twin's together leaves
        both under the namespace's 410, one fate for both."""
        provision = OLD + "paragraf/1/"
        _edit_map(live_a, live_a.b, _drop_redirects(provision, provision + "index.json"))

        assert commit_release(live_a.plane, live_a.b).active == live_a.b

    def test_the_report_counts_what_the_candidate_answers(self, live_a: Host) -> None:
        report = require_continuity(live_a.releases, live_a.a, live_a.b)

        assert report is not None
        assert report.moved == 4
        assert report.summary().endswith("none missing")

    def test_first_deployment_with_nothing_ever_live_is_not_judged(self, host: Host) -> None:
        """No marker and R = D naming no release: the triple proves the bootstrap."""
        assert read_triple(host.plane).marker is None

        assert commit_release(host.plane, host.a).active == host.a
        assert read_marker(host.releases) == Marker(active=host.a, previous=None)

    def test_a_proven_bootstrap_has_no_report(self, host: Host) -> None:
        assert require_continuity(host.releases, None, host.a) is None


class TestRefusedCandidates:
    def test_a_url_the_live_release_serves_that_would_404(self, live_b: Host) -> None:
        """B live, A committed forward: A neither serves nor redirects /lov/nyloven/."""
        error = _refused_untouched(live_b, live_b.a, "would answer 404")

        message = str(error)
        assert NEW in message
        assert message.startswith(
            f"release {live_b.a[:12]} not committed: against the live release {live_b.b[:12]}, "
        )
        assert message.endswith(
            "Publish a release that keeps the URL live, 301s it to a live page "
            "or retires it with 410 (docs/url-contract.md §4)"
        )

    def test_a_missing_json_twin_is_refused(self, live_a: Host) -> None:
        (_corpus(live_a, live_a.b) / "lov/nyloven/index.json").unlink()

        _refused_untouched(live_a, live_a.b, "which is not a live page")

    def test_a_dangling_redirect_is_refused(self, live_a: Host) -> None:
        def dangle(data: dict[str, Any]) -> None:
            for entry in data["redirects"]:
                if entry["from"] == OLD:
                    entry["to"] = "/lov/finnes-ikke/"

        _edit_map(live_a, live_a.b, dangle)

        _refused_untouched(live_a, live_a.b, "/lov/finnes-ikke/, which is not a live page")

    def test_a_page_and_twin_with_different_fates_are_refused(self, live_a: Host) -> None:
        """The page still 301s, its twin falls to the 410: one entity, two answers."""
        _edit_map(live_a, live_a.b, _drop_redirects(OLD + "index.json"))

        _refused_untouched(live_a, live_a.b, "its JSON twin answers gone")

    def test_an_unreadable_candidate_map_is_refused(self, live_a: Host) -> None:
        (_corpus(live_a, live_a.b) / "redirect-map.json").unlink()

        _refused_untouched(live_a, live_a.b, "redirect-map.json is missing")

    def test_the_refusal_exits_one_from_the_command(
        self, live_b: Host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The command's plane is replaced by the one over the fake, as test_release_cli does."""
        monkeypatch.setattr(commands, "_plane", lambda *_: live_b.plane)

        result = runner.invoke(app, ["release", "commit", live_b.a])

        assert result.exit_code == 1
        assert said("release refused", result.output)
        assert said("would answer 404", result.output)


class TestTheLiveReleaseMustBeIntact:
    def test_a_live_release_whose_tree_changed_is_refused(self, live_a: Host) -> None:
        """A truncated previous tree would be compared against fewer URLs; never."""
        (_corpus(live_a, live_a.a) / "lov/testloven/paragraf/1/index.json").unlink()

        found = release_content_id(_corpus(live_a, live_a.a), live_a.releases / live_a.a / "site")
        error = _refused_untouched(live_a, live_a.b, "changed after it went live")

        assert str(error) == (
            f"the live release's trees hash to {found[:12]}, not {live_a.a[:12]}: it was "
            "changed after it went live; URL continuity cannot be checked, nothing is committed"
        )

    @pytest.mark.parametrize("payload", [b"{", b"[]"], ids=["not-json", "not-an-object"])
    def test_live_site_facts_that_do_not_parse_are_refused(
        self, live_a: Host, payload: bytes
    ) -> None:
        (live_a.releases / live_a.a / "site/site-facts.json").write_bytes(payload)

        error = _refused_untouched(live_a, live_a.b, "is unreadable")

        assert isinstance(error.__cause__, ValueError)
        assert str(error) == (
            f"the live release {live_a.a[:12]} is unreadable: {error.__cause__}; "
            "nothing is committed"
        )

    def test_a_live_release_missing_its_corpus_is_refused(self, live_a: Host) -> None:
        shutil.rmtree(_corpus(live_a, live_a.a))

        error = _refused_untouched(live_a, live_a.b, "is not a complete envelope")

        assert str(error) == (
            f"the live release {live_a.a[:12]} is not a complete envelope at "
            f"{live_a.releases / live_a.a}; "
            "URL continuity cannot be checked, so nothing is committed"
        )

    def test_a_marker_that_is_not_json_is_refused(self, live_a: Host) -> None:
        (live_a.releases / MARKER_NAME).write_text("{", encoding="utf-8")
        before = live_a.snapshot()

        with pytest.raises(IncompleteEnvelopeError, match="is not a marker"):
            commit_release(live_a.plane, live_a.b)
        assert live_a.snapshot() == before
        assert live_a.caddy.reloads == 0

    def test_a_missing_marker_beside_a_served_release_is_not_a_bootstrap(
        self, live_a: Host
    ) -> None:
        """R names A with no marker: the reloaded-marker-missing row, refused, never skipped."""
        (live_a.releases / MARKER_NAME).unlink()

        with pytest.raises(UnreconciledError, match="reloaded_marker_missing"):
            commit_release(live_a.plane, live_a.b)
        assert live_a.caddy.reloads == 0


class _FlipAfterFirstRead:
    """The admin endpoint, whose host another operator switches to B right after the
    transaction's first read of R: the race between the check and the stage."""

    def __init__(self, host: Host) -> None:
        self.host = host
        self.reads = 0
        self.after_flip: dict[str, bytes] = {}

    def running_config(self) -> object:
        self.reads += 1
        answer = self.host.caddy.running_config()
        if self.reads == 1:
            self.host.make_live(self.host.b, previous=self.host.a)
            self.after_flip = self.host.snapshot()
        return answer


class TestConcurrency:
    def test_continuity_is_checked_while_the_transaction_lock_is_held(
        self, live_a: Host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The URL-contract concurrency promise covers judging, before staging starts."""
        checked = []

        def judge(releases: Path, live: str | None, candidate: str) -> object:
            assert not live_a.plane.next_fragment.exists()
            with pytest.raises(TransactionLockedError), transaction_lock(releases):
                pytest.fail("the continuity check ran without the transaction lock")
            checked.append((live, candidate))
            return require_continuity(releases, live, candidate)

        monkeypatch.setattr(control, "require_continuity", judge)

        assert commit_release(live_a.plane, live_a.b).active == live_a.b
        assert checked == [(live_a.a, live_a.b)]

    def test_a_live_release_changed_during_the_check_is_refused(self, live_a: Host) -> None:
        flip = _FlipAfterFirstRead(live_a)
        plane = ControlPlane(
            live_a.releases, live_a.plane.caddyfile, live_a.plane.fragment, live_a.caddy, flip
        )
        with pytest.raises(ContinuityRefusedError, match="the live state changed") as caught:
            commit_release(plane, live_a.b)

        assert "run the release again" in str(caught.value)
        assert not live_a.plane.next_fragment.exists()
        assert live_a.snapshot() == flip.after_flip
        assert live_a.caddy.reloads == 0

    @pytest.mark.parametrize(
        "command",
        [
            lambda h: commit_release(h.plane, h.b),
            lambda h: rollback(h.plane),
            lambda h: reconcile(h.plane, "complete"),
            lambda h: prune(h.plane),
        ],
        ids=["commit", "rollback", "reconcile", "prune"],
    )
    def test_a_held_lock_refuses_every_mutating_command_untouched(
        self, live_b: Host, command: Callable[[Host], object]
    ) -> None:
        before = live_b.snapshot()
        descriptor = os.open(live_b.releases, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(TransactionLockedError, match="another release transaction"):
                command(live_b)
        finally:
            os.close(descriptor)
        assert live_b.snapshot() == before
        assert live_b.caddy.reloads == 0

    def test_the_lock_is_released_after_a_refusal(self, live_b: Host) -> None:
        with pytest.raises(ContinuityRefusedError):
            commit_release(live_b.plane, live_b.a)
        with transaction_lock(live_b.releases):
            pass

    def test_a_releases_root_that_does_not_exist_is_named(self, tmp_path: Path) -> None:
        with (
            pytest.raises(TransactionLockedError, match="cannot open"),
            transaction_lock(tmp_path / "absent"),
        ):
            pass


class TestRollbackAndRecovery:
    @pytest.mark.parametrize("tree", ["corpus", "site"])
    def test_rollback_does_not_depend_on_the_tree_it_leaves(self, live_b: Host, tree: str) -> None:
        """URL contract §4 exempts emergency rollback from the departing tree's check."""
        shutil.rmtree(live_b.releases / live_b.b / tree)

        report = rollback(live_b.plane)

        assert (report.active, report.previous) == (live_b.a, live_b.b)
        assert live_b.running() == live_b.pair_of(live_b.a)
        assert read_marker(live_b.releases) == Marker(active=live_b.a, previous=live_b.b)

    @pytest.mark.parametrize(
        "relative",
        ["corpus/lov/testloven/index.html", "site/site-facts.json"],
        ids=["corpus", "site"],
    )
    def test_reconcile_complete_refuses_a_damaged_previous_tree(
        self, live_a: Host, relative: str
    ) -> None:
        """Recovery applies the same intact-previous-tree check as commit (§4)."""
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")
        path = live_a.releases / live_a.a / relative
        if relative.endswith(".json"):
            facts = json.loads(path.read_bytes())
            facts["lovspor_commit"] = "0" * 40
            path.write_text(json.dumps(facts), encoding="utf-8")
        else:
            path.write_bytes(path.read_bytes() + b"\n")
        before = live_a.snapshot()
        running = live_a.running()

        with pytest.raises(ContinuityRefusedError, match="changed after it went live"):
            reconcile(live_a.plane, "complete")

        assert live_a.snapshot() == before
        assert live_a.running() == running
        assert live_a.caddy.reloads == 0
        assert not live_a.plane.next_fragment.exists()

    def test_rollback_restores_the_previous_release_without_judging_it(self, live_b: Host) -> None:
        """A cannot pass continuity against B (it 404s /lov/nyloven/), and rollback is the
        documented exception: it restores the release served immediately before."""
        with pytest.raises(ContinuityRefusedError):
            require_continuity(live_b.releases, live_b.b, live_b.a)

        report = rollback(live_b.plane)

        assert (report.active, report.previous) == (live_b.a, live_b.b)
        assert live_b.running() == live_b.pair_of(live_b.a)
        assert read_marker(live_b.releases) == Marker(active=live_b.a, previous=live_b.b)
        assert not live_b.plane.next_fragment.exists()

    def test_reconcile_complete_judges_a_hand_staged_fragment(self, live_b: Host) -> None:
        """D = A written by hand beside R = M = B: --complete would make A live unjudged."""
        live_b.plane.fragment.write_text(live_b.fragment_of(live_b.a), encoding="utf-8")
        before = live_b.snapshot()

        with pytest.raises(ContinuityRefusedError, match="would answer 404"):
            reconcile(live_b.plane, "complete")
        assert live_b.snapshot() == before
        assert live_b.caddy.reloads == 0

        assert reconcile(live_b.plane, "abandon").live == live_b.b

    def test_reconcile_complete_of_a_crashed_commit_that_passed_goes_live(
        self, live_a: Host
    ) -> None:
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")

        assert reconcile(live_a.plane, "complete").live == live_a.b
        assert read_marker(live_a.releases) == Marker(active=live_a.b, previous=live_a.a)
