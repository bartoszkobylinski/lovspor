"""The staged rehearsal of the first migration (ADR-0014 Validation (g)).

The second instance is the *droplet* of ``migrate_fixtures`` with its own
unit name and its own TCP admin address: on the real box ``localhost:2019``
belongs to the Caddy the rehearsal must not disturb, so the rehearsal's
copy of the previous Caddyfile names an address of its own. That keeps the
one property every address assertion rests on — ``caddy reload`` derives
the address from the file it supplies, and that address is not the socket.

The rejected-load fixture is a real file on the droplet (a block binding a
port already taken); here the file is the ordinary source and ``FakeCaddy``
refuses the load, because what is under test is what the rehearsal asserts
about a refused load, not what makes Caddy refuse one.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, Completed, ConfigPair, adapt, config_pair
from lovspor.release.envelope import read_marker
from lovspor.release.errors import RehearsalFailedError
from lovspor.release.migrate import SOCKET_MODE, drop_in_text
from lovspor.release.rehearsal import (
    Rehearsal,
    RehearsalFixtures,
    Step,
    after_cutover,
    before_cutover,
    cutover,
    hand_provisioned_socket_fails,
    plain_reload_refused,
    rehearse,
    rejected_cutover,
    restarts,
    rollback,
    rolled_back,
    start_on_previous,
    steady_reload,
    steady_state,
)
from tests.unit.caddy_fakes import FakeCaddy, toy_adapt
from tests.unit.migrate_fixtures import OLD_CADDYFILE, Droplet, Sabotaged, make_droplet
from tests.unit.release_fixtures import World, build, make_world

REHEARSAL_TCP = "localhost:2029"
REHEARSAL_UNIT = "caddy-rehearsal"
STOCK_SHOWN = "ExecReload={ path=/usr/bin/caddy ; argv[]=/usr/bin/caddy reload }\n"


class Staged(NamedTuple):
    droplet: Droplet
    plan: Rehearsal

    @property
    def caddy(self) -> FakeCaddy:
        return self.droplet.caddy

    def running(self, address: str) -> ConfigPair:
        return config_pair(self.caddy.running_config_at(address))

    def show_answering(self, nth: int, answer: Completed) -> Rehearsal:
        """The same plan, with the ``nth`` ``systemctl show`` answered by ``answer``.

        The migration makes its own ``systemctl show`` call before the
        rehearsal's, and it has its own assertion about the answer — so
        sabotaging every call would test the migration's check, not this
        module's.
        """
        seen: list[int] = []

        def show(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            seen.append(1)
            return answer if len(seen) == nth else self.caddy.run(argv, env)

        runner = Sabotaged(self.caddy, ("systemctl", "show"), show)
        return replace(self.plan, plane=replace(self.plan.plane, runner=runner))

    def reload_answering(self, answer: Completed) -> Rehearsal:
        runner = Sabotaged(self.caddy, ("systemctl", "reload"), answer)
        return replace(self.plan, plane=replace(self.plan.plane, runner=runner))


def names(steps: tuple[Step, ...]) -> list[str]:
    return [step.name for step in steps]


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def envelopes(world: World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    releases = tmp_path_factory.mktemp("source") / "releases"
    content_id = build(world, releases).release_content_id
    return releases, content_id, content_id


@pytest.fixture
def staged(
    envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Staged:
    droplet = make_droplet(envelopes, tmp_path, monkeypatch)
    previous = "{\n\tadmin " + REHEARSAL_TCP + "\n}\n" + OLD_CADDYFILE
    droplet.plane.caddyfile.write_text(previous, encoding="utf-8")
    Path(str(droplet.plane.caddyfile) + ".old").write_text(previous, encoding="utf-8")
    droplet.caddy.restart()
    source = droplet.host.caddyfile_source.read_text(encoding="utf-8")
    fixtures = RehearsalFixtures(
        rejected=_written(tmp_path / "app" / "Caddyfile.rejected", source),
        unsuffixed=_written(tmp_path / "app" / "Caddyfile.unsuffixed", source.replace("|0660", "")),
    )
    host = replace(droplet.host, tcp_admin=REHEARSAL_TCP, unit=REHEARSAL_UNIT)
    return Staged(droplet, Rehearsal(droplet.plane, host, fixtures, droplet.a))


def _written(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _released(staged: Staged) -> dict[str, object]:
    """The configuration the new Caddyfile composes with the release's fragment."""
    fragment = staged.plan.plane.releases / staged.plan.content_id / "release.caddy"
    return toy_adapt(staged.plan.host.caddyfile_source, {FRAGMENT_ENV: str(fragment)})


def _everywhere(staged: Staged) -> object:
    """An admin client that answers wherever it is asked: a stray listener beside the socket."""

    def connect(address: str) -> FakeCaddy:
        del address
        return staged.caddy

    return connect


class TestStartOnPrevious:
    def test_reads_the_previous_configuration_off_tcp(self, staged: Staged) -> None:
        step = start_on_previous(staged.plan)

        assert step.name == "i"
        assert REHEARSAL_TCP in step.detail
        assert "no socket" in step.detail

    def test_an_instance_that_does_not_answer_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.caddy.admin_up = False

        with pytest.raises(RehearsalFailedError, match="nothing answers") as raised:
            start_on_previous(staged.plan)

        assert raised.value.step == "i"

    def test_an_instance_already_running_a_release_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.caddy.load(_released(staged))

        with pytest.raises(RehearsalFailedError, match="already runs release"):
            start_on_previous(staged.plan)

    def test_a_socket_that_already_exists_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.plan.host.socket.parent.mkdir(parents=True, exist_ok=True)
        staged.plan.host.socket.touch()

        with pytest.raises(RehearsalFailedError, match="exists before the cutover"):
            start_on_previous(staged.plan)


class TestCutover:
    def test_walks_the_address_transition_at_the_procedures_own_checkpoints(
        self, staged: Staged
    ) -> None:
        steps = cutover(staged.plan)

        assert names(steps) == ["ii.before-c", "ii.after-c", "ii.exec-reload", "ii"]
        assert staged.caddy.admin_address == staged.plan.host.socket_admin
        marker = read_marker(staged.plan.plane.releases)
        assert marker is not None
        assert marker.active == staged.plan.content_id

    def test_the_socket_that_answers_after_the_load_names_the_envelope(
        self, staged: Staged
    ) -> None:
        steps = cutover(staged.plan)

        assert staged.plan.content_id[:12] in steps[1].detail
        assert staged.running(staged.plan.host.socket_admin).release_id == staged.plan.content_id

    def test_the_before_check_reads_the_socket_name_not_what_it_points_at(
        self, staged: Staged
    ) -> None:
        staged.plan.host.socket.parent.mkdir(parents=True, exist_ok=True)
        staged.plan.host.socket.symlink_to(staged.plan.plane.caddyfile.parent / "nowhere")

        with pytest.raises(RehearsalFailedError, match="exists before"):
            before_cutover(staged.plan)

    def test_a_socket_that_does_not_answer_after_the_load_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        with pytest.raises(RehearsalFailedError, match="does not answer after"):
            after_cutover(staged.plan)

    def test_tcp_still_answering_after_the_load_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        host = replace(staged.plan.host, admin_client=_everywhere(staged))

        with pytest.raises(RehearsalFailedError, match="still answers after"):
            after_cutover(replace(staged.plan, host=host))

    def test_a_socket_running_another_release_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)

        with pytest.raises(RehearsalFailedError, match="not release 000000000000"):
            after_cutover(replace(staged.plan, content_id="0" * 64))

    def test_an_exec_reload_that_does_not_name_the_socket_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        plan = staged.show_answering(2, Completed(0, STOCK_SHOWN, ""))

        with pytest.raises(RehearsalFailedError, match="ExecReload does not name") as raised:
            cutover(plan)

        assert raised.value.step == "ii"

    def test_an_exec_reload_that_cannot_be_read_ends_the_rehearsal(self, staged: Staged) -> None:
        plan = staged.show_answering(2, Completed(1, "", "Unit caddy-rehearsal not loaded."))

        with pytest.raises(RehearsalFailedError, match="ExecReload failed"):
            cutover(plan)


class TestRejectedCutover:
    def test_a_refused_load_leaves_tcp_answering_the_socket_absent_and_r_unmoved(
        self, staged: Staged
    ) -> None:
        staged.caddy.fail_reloads = 1
        before = staged.running(REHEARSAL_TCP)

        steps = rejected_cutover(staged.plan)

        assert names(steps) == ["ii.rejected", "ii.abandoned"]
        assert staged.caddy.admin_address == REHEARSAL_TCP
        assert not staged.plan.host.socket.exists()
        assert staged.running(REHEARSAL_TCP) == before

    def test_the_abandon_leaves_the_host_ready_for_the_real_cutover(self, staged: Staged) -> None:
        staged.caddy.fail_reloads = 1

        rejected_cutover(staged.plan)

        assert staged.plan.plane.caddyfile.read_text(encoding="utf-8").startswith("{\n\tadmin ")
        assert not staged.plan.plane.fragment.exists()
        assert not staged.plan.host.previous_caddyfile.exists()
        assert names(cutover(staged.plan))[-1] == "ii"

    def test_a_fixture_the_instance_accepts_ends_the_rehearsal(self, staged: Staged) -> None:
        """The negative fixture proves nothing unless the load is actually refused."""
        with pytest.raises(RehearsalFailedError, match="must be refused at load") as raised:
            rejected_cutover(staged.plan)

        assert raised.value.step == "ii.rejected"

    def test_an_instance_that_does_not_answer_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.caddy.admin_up = False

        with pytest.raises(RehearsalFailedError, match="nothing answers"):
            rejected_cutover(staged.plan)


class TestPlainReloadRefused:
    def test_the_stock_line_cannot_reach_the_running_socket(self, staged: Staged) -> None:
        cutover(staged.plan)
        kept = staged.plan.plane.caddyfile.read_bytes()

        step = plain_reload_refused(staged.plan)

        assert step.name == "iii.plain-reload"
        assert staged.plan.plane.caddyfile.read_bytes() == kept
        assert staged.plan.host.drop_in.read_text(encoding="utf-8") == drop_in_text(
            staged.plan.host, with_exec_reload=True
        )

    def test_a_stock_reload_that_reached_the_socket_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        """Had it succeeded, the rollback's explicit --address would be redundant."""
        cutover(staged.plan)
        plan = staged.reload_answering(Completed(0, "", ""))

        with pytest.raises(RehearsalFailedError, match="reached the socket") as raised:
            plain_reload_refused(plan)

        assert raised.value.step == "iii.plain-reload"

    def test_takes_away_the_exec_reload_pair_and_nothing_else(self, staged: Staged) -> None:
        """Dropping RuntimeDirectory= too would change three things and risk the running socket."""
        cutover(staged.plan)
        seen: list[tuple[str, bytes]] = []

        def reload(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            seen.append(
                (
                    staged.plan.host.drop_in.read_text(encoding="utf-8"),
                    staged.plan.plane.caddyfile.read_bytes(),
                )
            )
            return Completed(1, "", "Job for caddy-rehearsal.service failed")

        runner = Sabotaged(staged.caddy, ("systemctl", "reload"), reload)
        plain_reload_refused(replace(staged.plan, plane=replace(staged.plan.plane, runner=runner)))

        (drop_in, caddyfile) = seen[0]
        assert drop_in == drop_in_text(staged.plan.host, with_exec_reload=False)
        assert caddyfile == staged.plan.host.previous_caddyfile.read_bytes()

    def test_the_instance_is_left_on_the_socket(self, staged: Staged) -> None:
        cutover(staged.plan)

        plain_reload_refused(staged.plan)

        assert staged.caddy.admin_address == staged.plan.host.socket_admin


class TestRollback:
    def test_the_way_back_reaches_the_socket_and_leaves_tcp_answering(self, staged: Staged) -> None:
        cutover(staged.plan)

        steps = rollback(staged.plan)

        assert names(steps) == ["iii.plain-reload", "iii"]
        assert staged.caddy.admin_address == REHEARSAL_TCP
        assert not staged.plan.host.socket.exists()
        assert read_marker(staged.plan.plane.releases) is None

    def test_the_previous_configuration_is_what_runs_afterwards(self, staged: Staged) -> None:
        before = staged.running(REHEARSAL_TCP)
        cutover(staged.plan)

        rollback(staged.plan)

        assert staged.running(REHEARSAL_TCP) == before
        assert staged.running(REHEARSAL_TCP).release_id is None

    def test_a_configuration_that_still_names_a_release_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        """The ADR's own wording: no `vars` handler afterwards, the symlink roots."""
        previous = self._previous(staged)
        host = replace(staged.plan.host, admin_client=_everywhere(staged))

        with pytest.raises(RehearsalFailedError, match="still names release"):
            rolled_back(replace(staged.plan, host=host), previous)

    def test_a_socket_that_still_answers_ends_the_rehearsal(self, staged: Staged) -> None:
        previous = self._previous(staged)
        rollback(staged.plan)
        host = replace(staged.plan.host, admin_client=_everywhere(staged))

        with pytest.raises(RehearsalFailedError, match="still answers after the way back"):
            rolled_back(replace(staged.plan, host=host), previous)

    def _previous(self, staged: Staged) -> ConfigPair:
        """The pre-envelope pair, read while the backup still exists; then the envelope is live."""
        cutover(staged.plan)
        return adapt(
            staged.plan.plane.runner,
            staged.plan.host.previous_caddyfile,
            staged.plan.plane.fragment,
        )

    def test_an_exec_reload_still_naming_the_socket_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        cutover(staged.plan)
        shown = f"ExecReload=[ argv[]=caddy reload --address {staged.plan.host.socket_admin} ]\n"
        plan = staged.show_answering(2, Completed(0, shown, ""))

        with pytest.raises(RehearsalFailedError, match="ExecReload still names") as raised:
            rollback(plan)

        assert raised.value.step == "iii"


class TestSteadyState:
    def test_one_reload_through_the_drop_in_reaches_the_socket_and_moves_nothing(
        self, staged: Staged
    ) -> None:
        steps = steady_state(staged.plan)

        assert names(steps)[-1] == "iv"
        assert staged.caddy.admin_address == staged.plan.host.socket_admin
        assert staged.caddy.reloads >= 2

    def test_r_is_the_same_pair_before_and_after(self, staged: Staged) -> None:
        cutover(staged.plan)
        before = staged.running(staged.plan.host.socket_admin)

        steady_reload(staged.plan)

        assert staged.running(staged.plan.host.socket_admin) == before

    def test_a_reload_that_fails_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        staged.caddy.fail_reloads = 1

        with pytest.raises(RehearsalFailedError, match="systemctl reload") as raised:
            steady_reload(staged.plan)

        assert raised.value.step == "iv"

    def test_a_reload_that_moved_r_ends_the_rehearsal(self, staged: Staged) -> None:
        """A steady-state reload swaps the fragment, never the release that is live."""
        cutover(staged.plan)

        def moved(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            staged.caddy.load({"apps": {"http": {"servers": {"srv0": {"routes": []}}}}})
            return Completed(0, "", "")

        runner = Sabotaged(staged.caddy, ("systemctl", "reload"), moved)

        with pytest.raises(RehearsalFailedError, match="the reload moved R"):
            steady_reload(replace(staged.plan, plane=replace(staged.plan.plane, runner=runner)))


class TestRestarts:
    def test_the_four_facts_hold_after_each_of_two_restarts(self, staged: Staged) -> None:
        cutover(staged.plan)

        steps = restarts(staged.plan)

        assert names(steps) == ["v.1", "v.2", "v.by-hand"]
        assert str(staged.plan.host.socket) in steps[0].detail
        assert "0660" in steps[1].detail
        assert staged.caddy.restarts >= 2

    def test_a_fact_that_stops_holding_ends_the_rehearsal_naming_the_restart(
        self, staged: Staged
    ) -> None:
        cutover(staged.plan)
        staged.droplet.ownership.groups["lovspor-release"] += 1

        with pytest.raises(RehearsalFailedError, match="admin socket precondition") as raised:
            restarts(staged.plan)

        assert raised.value.step == "v.1"

    def test_a_restart_that_fails_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        runner = Sabotaged(staged.caddy, ("systemctl", "restart"), Completed(1, "", "nope"))
        plan = replace(staged.plan, plane=replace(staged.plan.plane, runner=runner))

        with pytest.raises(RehearsalFailedError, match="systemctl restart"):
            restarts(plan)

    def test_a_hand_provisioned_socket_does_not_survive_the_restart(self, staged: Staged) -> None:
        cutover(staged.plan)

        step = hand_provisioned_socket_fails(staged.plan)

        assert step.name == "v.by-hand"
        assert "mode" in step.detail

    def test_the_fixture_leaves_the_instance_as_it_found_it(self, staged: Staged) -> None:
        cutover(staged.plan)
        kept = staged.plan.plane.caddyfile.read_bytes()

        hand_provisioned_socket_fails(staged.plan)

        assert staged.plan.plane.caddyfile.read_bytes() == kept
        assert staged.plan.host.socket.stat().st_mode & 0o777 == SOCKET_MODE

    def test_a_fixture_that_still_carries_the_suffix_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        """The fixture proves nothing unless the recreated socket really loses the mode."""
        cutover(staged.plan)
        fixtures = replace(staged.plan.fixtures, unsuffixed=staged.plan.host.caddyfile_source)

        with pytest.raises(RehearsalFailedError, match="passed the four facts") as raised:
            hand_provisioned_socket_fails(replace(staged.plan, fixtures=fixtures))

        assert raised.value.step == "v.by-hand"


class TestRehearse:
    def test_walks_every_sub_step_of_validation_g_in_order(self, staged: Staged) -> None:
        staged.caddy.fail_reloads = 1

        report = rehearse(staged.plan)

        assert names(report.steps) == [
            "i",
            "ii.rejected",
            "ii.abandoned",
            "ii.before-c",
            "ii.after-c",
            "ii.exec-reload",
            "ii",
            "iii.plain-reload",
            "iii",
            "ii.before-c",
            "ii.after-c",
            "ii.exec-reload",
            "ii",
            "iv",
            "v.1",
            "v.2",
            "v.by-hand",
        ]

    def test_leaves_the_second_instance_cut_over_on_its_socket(self, staged: Staged) -> None:
        staged.caddy.fail_reloads = 1

        rehearse(staged.plan)

        assert staged.caddy.admin_address == staged.plan.host.socket_admin
        marker = read_marker(staged.plan.plane.releases)
        assert marker is not None
        assert marker.active == staged.plan.content_id

    def test_every_step_reports_what_it_read(self, staged: Staged) -> None:
        staged.caddy.fail_reloads = 1

        described = rehearse(staged.plan).describe()

        assert len(described) == 17
        assert described[0].startswith("i: ")
        assert all(": " in line for line in described)
