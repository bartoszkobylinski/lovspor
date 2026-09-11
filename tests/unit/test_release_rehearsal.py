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

Every refusal is asserted by its sub-step *and* by what it says: the
operator reads one line and acts on it, and a rehearsal that named the
wrong sub-step would send them to the wrong part of the runbook.
"""

import stat
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, Completed, ConfigPair, adapt, config_pair
from lovspor.release.envelope import read_marker
from lovspor.release.errors import RehearsalFailedError, UnobservableError
from lovspor.release.migrate import SOCKET_MODE, drop_in_text
from lovspor.release.rehearsal import (
    Rehearsal,
    RehearsalFixtures,
    Step,
    abandoned,
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


class At:
    """An ``AdminClient`` bound to one address, answering only if that address is served."""

    def __init__(self, caddy: FakeCaddy, answers: bool) -> None:
        self.caddy = caddy
        self.answers = answers

    def running_config(self) -> object:
        if not self.answers:
            raise UnobservableError("admin_unreachable", "connection refused")
        return self.caddy.running_config()


class Answering:
    """A client factory answering at exactly these addresses — and no other, ``None`` included.

    ``FakeCaddy.admin_client`` answers wherever the instance happens to be,
    which cannot tell "the address the code asked about" from "any address
    at all": a call that lost its argument would read the same.
    """

    def __init__(self, caddy: FakeCaddy, *addresses: str) -> None:
        self.caddy = caddy
        self.addresses = set(addresses)

    def __call__(self, address: str) -> At:
        return At(self.caddy, address in self.addresses)


class Staged(NamedTuple):
    droplet: Droplet
    plan: Rehearsal

    @property
    def caddy(self) -> FakeCaddy:
        return self.droplet.caddy

    def running(self, address: str) -> ConfigPair:
        return config_pair(self.caddy.running_config_at(address))

    def answering(self, *addresses: str) -> Rehearsal:
        """The same plan, reading the instance through a client bound to these addresses."""
        host = replace(self.plan.host, admin_client=Answering(self.caddy, *addresses))
        return replace(self.plan, host=host)

    def with_runner(self, prefix: tuple[str, ...], answer: object) -> Rehearsal:
        """The same plan, with one command answered by ``answer`` instead of the instance."""
        runner = Sabotaged(self.caddy, prefix, answer)  # type: ignore[arg-type]
        return replace(self.plan, plane=replace(self.plan.plane, runner=runner))

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

        return self.with_runner(("systemctl", "show"), show)


def names(steps: tuple[Step, ...]) -> list[str]:
    return [step.name for step in steps]


def details(steps: tuple[Step, ...]) -> dict[str, str]:
    return {step.name: step.detail for step in steps}


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


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestStartOnPrevious:
    def test_reads_the_previous_configuration_off_tcp(self, staged: Staged) -> None:
        step = start_on_previous(staged.plan)

        expected = staged.running(REHEARSAL_TCP).describe()
        assert step == Step(name="i", detail=f"{REHEARSAL_TCP} answers {expected}; no socket")

    def test_an_instance_that_does_not_answer_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.caddy.admin_up = False

        with pytest.raises(RehearsalFailedError) as raised:
            start_on_previous(staged.plan)

        assert raised.value.step == "i"
        assert raised.value.detail == f"nothing answers on {REHEARSAL_TCP}"

    def test_an_instance_already_running_a_release_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.caddy.load(_released(staged))

        with pytest.raises(RehearsalFailedError) as raised:
            start_on_previous(staged.plan)

        assert raised.value.step == "i"
        assert raised.value.detail == (
            f"{REHEARSAL_TCP} already runs release {staged.plan.content_id}"
        )

    def test_a_socket_that_already_exists_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.plan.host.socket.parent.mkdir(parents=True, exist_ok=True)
        staged.plan.host.socket.touch()

        with pytest.raises(RehearsalFailedError) as raised:
            start_on_previous(staged.plan)

        assert raised.value.step == "i"
        assert raised.value.detail == f"{staged.plan.host.socket} exists before the cutover"

    def test_it_is_the_tcp_address_that_is_read_and_no_other(self, staged: Staged) -> None:
        with pytest.raises(RehearsalFailedError, match="nothing answers"):
            start_on_previous(staged.answering("somewhere-else"))


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

    def test_every_step_says_what_it_read(self, staged: Staged) -> None:
        said = details(cutover(staged.plan))
        socket = staged.plan.host.socket_admin
        running = staged.running(socket).describe()

        assert said["ii.before-c"] == f"{staged.plan.host.socket} does not exist"
        assert said["ii.after-c"] == f"the socket answers {running}; TCP refuses"
        assert said["ii.exec-reload"] == f"ExecReload names --address {socket}"
        assert said["ii"] == f"migrated {staged.plan.content_id[:12]} to {socket}"

    def test_the_before_check_reads_the_socket_name_not_what_it_points_at(
        self, staged: Staged
    ) -> None:
        staged.plan.host.socket.parent.mkdir(parents=True, exist_ok=True)
        staged.plan.host.socket.symlink_to(staged.plan.plane.caddyfile.parent / "nowhere")

        with pytest.raises(RehearsalFailedError) as raised:
            before_cutover(staged.plan)

        assert raised.value.step == "ii"
        assert raised.value.detail == f"{staged.plan.host.socket} exists before (c)"

    def test_a_socket_that_does_not_answer_after_the_load_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        with pytest.raises(RehearsalFailedError) as raised:
            after_cutover(staged.plan)

        assert raised.value.step == "ii"
        assert raised.value.detail == f"{staged.plan.host.socket_admin} does not answer after (c)"

    def test_tcp_still_answering_after_the_load_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        plan = staged.answering(staged.plan.host.socket_admin, REHEARSAL_TCP)

        with pytest.raises(RehearsalFailedError) as raised:
            after_cutover(plan)

        assert raised.value.step == "ii"
        assert raised.value.detail == (
            f"{REHEARSAL_TCP} still answers after (c); the admin endpoint did not move"
        )

    def test_a_socket_running_another_release_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        wanted = "0" * 64

        with pytest.raises(RehearsalFailedError) as raised:
            after_cutover(replace(staged.plan, content_id=wanted))

        assert raised.value.step == "ii"
        running = staged.running(staged.plan.host.socket_admin).describe()
        assert raised.value.detail == (
            f"over the socket Caddy runs {running}, not release {wanted[:12]}"
        )

    def test_an_exec_reload_that_does_not_name_the_socket_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        plan = staged.show_answering(2, Completed(0, STOCK_SHOWN, ""))

        with pytest.raises(RehearsalFailedError) as raised:
            cutover(plan)

        assert raised.value.step == "ii"
        wanted = f"--address {staged.plan.host.socket_admin}"
        assert raised.value.detail == f"ExecReload does not name {wanted}: {STOCK_SHOWN.strip()}"

    def test_an_exec_reload_that_cannot_be_read_ends_the_rehearsal(self, staged: Staged) -> None:
        plan = staged.show_answering(2, Completed(1, "", "Unit caddy-rehearsal not loaded."))

        with pytest.raises(RehearsalFailedError) as raised:
            cutover(plan)

        assert raised.value.step == "ii"
        assert raised.value.detail == (
            f"systemctl show {REHEARSAL_UNIT} -p ExecReload failed: "
            "Unit caddy-rehearsal not loaded."
        )


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

    def test_it_is_the_rejected_fixture_that_is_offered_to_the_instance(
        self, staged: Staged
    ) -> None:
        """A run against the ordinary source would prove nothing about a refused load."""
        staged.caddy.fail_reloads = 1

        rejected_cutover(staged.plan)

        adapted = [argv for argv in staged.droplet.argvs() if argv[:2] == ("caddy", "adapt")]
        assert any(str(staged.plan.fixtures.rejected) in argv for argv in adapted)

    def test_both_steps_say_what_they_read(self, staged: Staged) -> None:
        staged.caddy.fail_reloads = 1

        said = details(rejected_cutover(staged.plan))

        assert said["ii.rejected"].startswith(f"R unmoved on {REHEARSAL_TCP}: ")
        assert said["ii.abandoned"] == "the previous Caddyfile is back; no fragment, no backup"

    def test_the_abandon_leaves_the_host_ready_for_the_real_cutover(self, staged: Staged) -> None:
        staged.caddy.fail_reloads = 1

        rejected_cutover(staged.plan)

        assert staged.plan.plane.caddyfile.read_text(encoding="utf-8").startswith("{\n\tadmin ")
        assert not staged.plan.plane.fragment.exists()
        assert not staged.plan.host.previous_caddyfile.exists()
        assert names(cutover(staged.plan))[-1] == "ii"

    def test_a_fixture_the_instance_accepts_ends_the_rehearsal(self, staged: Staged) -> None:
        """The negative fixture proves nothing unless the load is actually refused."""
        with pytest.raises(RehearsalFailedError) as raised:
            rejected_cutover(staged.plan)

        assert raised.value.step == "ii.rejected"
        assert raised.value.detail == (
            f"{staged.plan.fixtures.rejected} loaded; the fixture must be refused at load"
        )

    def test_an_instance_that_does_not_answer_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.caddy.admin_up = False

        with pytest.raises(RehearsalFailedError) as raised:
            rejected_cutover(staged.plan)

        assert raised.value.step == "ii.rejected"
        assert raised.value.detail == f"nothing answers on {REHEARSAL_TCP}"

    def test_a_refusal_that_moved_r_anyway_ends_the_rehearsal(self, staged: Staged) -> None:
        """R moves only on a successful load; a refusal that moved it is the whole hazard."""
        before = staged.running(REHEARSAL_TCP)

        def moved(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            staged.caddy.load({"apps": {"http": {"servers": {"srv0": {"routes": []}}}}})
            return Completed(1, "", "Error: loading new config")

        with pytest.raises(RehearsalFailedError) as raised:
            rejected_cutover(staged.with_runner(("caddy", "reload"), moved))

        assert raised.value.step == "ii.rejected"
        assert raised.value.detail.endswith(f"not the previous {before.describe()}")

    def test_a_refusal_that_left_nothing_answering_says_so(self, staged: Staged) -> None:
        def gone(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            staged.caddy.admin_up = False
            return Completed(1, "", "Error: loading new config")

        with pytest.raises(RehearsalFailedError) as raised:
            rejected_cutover(staged.with_runner(("caddy", "reload"), gone))

        assert raised.value.detail.startswith(f"{REHEARSAL_TCP} runs nothing, ")

    def test_a_refusal_that_left_a_socket_behind_ends_the_rehearsal(self, staged: Staged) -> None:
        def planted(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            staged.plan.host.socket.parent.mkdir(parents=True, exist_ok=True)
            staged.plan.host.socket.touch()
            return Completed(1, "", "Error: loading new config")

        with pytest.raises(RehearsalFailedError) as raised:
            rejected_cutover(staged.with_runner(("caddy", "reload"), planted))

        assert raised.value.step == "ii.rejected"
        assert raised.value.detail == f"{staged.plan.host.socket} exists after a refusal"


class TestAbandoned:
    def test_a_fragment_that_survived_the_abandon_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.plan.plane.fragment.write_text("", encoding="utf-8")

        with pytest.raises(RehearsalFailedError) as raised:
            abandoned(staged.plan)

        assert raised.value.step == "ii.rejected"
        assert raised.value.detail == (
            f"the fragment {staged.plan.plane.fragment} survived the abandon"
        )

    def test_a_backup_that_survived_the_abandon_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.plan.host.previous_caddyfile.write_text("", encoding="utf-8")

        with pytest.raises(RehearsalFailedError) as raised:
            abandoned(staged.plan)

        assert raised.value.step == "ii.rejected"
        assert raised.value.detail == (
            f"the backup {staged.plan.host.previous_caddyfile} survived the abandon"
        )

    def test_an_instance_that_does_not_answer_afterwards_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        staged.caddy.admin_up = False

        with pytest.raises(RehearsalFailedError) as raised:
            abandoned(staged.plan)

        assert raised.value.step == "ii.rejected"
        assert raised.value.detail == (
            f"{REHEARSAL_TCP} does not run the pre-envelope configuration after the abandon"
        )

    def test_an_instance_left_running_a_release_ends_the_rehearsal(self, staged: Staged) -> None:
        staged.caddy.load(_released(staged))

        with pytest.raises(RehearsalFailedError, match="pre-envelope configuration"):
            abandoned(staged.plan)


class TestPlainReloadRefused:
    def test_the_stock_line_cannot_reach_the_running_socket(self, staged: Staged) -> None:
        cutover(staged.plan)
        kept = staged.plan.plane.caddyfile.read_bytes()

        step = plain_reload_refused(staged.plan)

        assert step == Step(
            name="iii.plain-reload", detail="the stock reload line reached nothing, as it must"
        )
        assert staged.plan.plane.caddyfile.read_bytes() == kept
        assert staged.plan.host.drop_in.read_text(encoding="utf-8") == drop_in_text(
            staged.plan.host, with_exec_reload=True
        )

    def test_a_stock_reload_that_reached_the_socket_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        """Had it succeeded, the rollback's explicit --address would be redundant."""
        cutover(staged.plan)
        plan = staged.with_runner(("systemctl", "reload"), Completed(0, "", ""))

        with pytest.raises(RehearsalFailedError) as raised:
            plain_reload_refused(plan)

        assert raised.value.step == "iii.plain-reload"
        assert raised.value.detail == (
            f"systemctl reload {REHEARSAL_UNIT} of the previous Caddyfile reached the socket; "
            "the rollback's explicit --address would then be redundant"
        )

    def test_it_is_the_rehearsal_unit_that_is_reloaded(self, staged: Staged) -> None:
        cutover(staged.plan)

        plain_reload_refused(staged.plan)

        assert ("systemctl", "reload", REHEARSAL_UNIT) in staged.droplet.argvs()

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

        plain_reload_refused(staged.with_runner(("systemctl", "reload"), reload))

        (drop_in, caddyfile) = seen[0]
        assert drop_in == drop_in_text(staged.plan.host, with_exec_reload=False)
        assert caddyfile == staged.plan.host.previous_caddyfile.read_bytes()

    def test_what_it_writes_is_readable_by_caddy_whatever_the_operators_umask(
        self, staged: Staged, strict_umask: None
    ) -> None:
        cutover(staged.plan)
        seen: list[tuple[int, int]] = []

        def reload(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            seen.append((_mode(staged.plan.plane.caddyfile), _mode(staged.plan.host.drop_in)))
            return Completed(1, "", "failed")

        plain_reload_refused(staged.with_runner(("systemctl", "reload"), reload))

        assert seen == [(0o644, 0o644)]
        assert _mode(staged.plan.plane.caddyfile) == 0o644
        assert _mode(staged.plan.host.drop_in) == 0o644

    def test_a_daemon_reload_that_fails_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        plan = staged.with_runner(("systemctl", "daemon-reload"), Completed(1, "", "no such unit"))

        with pytest.raises(RehearsalFailedError) as raised:
            plain_reload_refused(plan)

        assert raised.value.step == "iii.plain-reload"
        assert raised.value.detail == "systemctl daemon-reload failed: no such unit"

    def test_a_daemon_reload_that_failed_silently_reports_its_exit_code(
        self, staged: Staged
    ) -> None:
        cutover(staged.plan)
        plan = staged.with_runner(("systemctl", "daemon-reload"), Completed(3, "", ""))

        with pytest.raises(RehearsalFailedError) as raised:
            plain_reload_refused(plan)

        assert raised.value.detail == "systemctl daemon-reload failed: 3"

    def test_the_instance_is_left_on_the_socket(self, staged: Staged) -> None:
        cutover(staged.plan)

        plain_reload_refused(staged.plan)

        assert staged.caddy.admin_address == staged.plan.host.socket_admin


class TestRollback:
    def _previous(self, staged: Staged) -> ConfigPair:
        """The pre-envelope pair, read while the backup still exists; then the envelope is live."""
        cutover(staged.plan)
        return adapt(
            staged.plan.plane.runner,
            staged.plan.host.previous_caddyfile,
            staged.plan.plane.fragment,
        )

    def test_the_way_back_reaches_the_socket_and_leaves_tcp_answering(self, staged: Staged) -> None:
        cutover(staged.plan)

        steps = rollback(staged.plan)

        assert names(steps) == ["iii.plain-reload", "iii"]
        assert details(steps)["iii"] == (
            f"rolled back over {staged.plan.host.socket_admin} to {REHEARSAL_TCP}"
        )
        assert staged.caddy.admin_address == REHEARSAL_TCP
        assert not staged.plan.host.socket.exists()
        assert read_marker(staged.plan.plane.releases) is None

    def test_the_previous_configuration_is_read_from_the_backup_with_the_fragment(
        self, staged: Staged
    ) -> None:
        """R is compared with the backup adapted, not with a value remembered from earlier."""
        cutover(staged.plan)

        rollback(staged.plan)

        argv = (
            "caddy",
            "adapt",
            "--config",
            str(staged.plan.host.previous_caddyfile),
            "--adapter",
            "caddyfile",
        )
        assert (argv, {FRAGMENT_ENV: str(staged.plan.plane.fragment)}) in staged.caddy.calls

    def test_the_previous_configuration_is_what_runs_afterwards(self, staged: Staged) -> None:
        before = staged.running(REHEARSAL_TCP)
        cutover(staged.plan)

        rollback(staged.plan)

        assert staged.running(REHEARSAL_TCP) == before
        assert staged.running(REHEARSAL_TCP).release_id is None

    def test_an_instance_that_does_not_answer_afterwards_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        previous = self._previous(staged)
        staged.caddy.admin_up = False

        with pytest.raises(RehearsalFailedError) as raised:
            rolled_back(staged.plan, previous)

        assert raised.value.step == "iii"
        assert raised.value.detail == f"{REHEARSAL_TCP} does not answer afterwards"

    def test_a_configuration_that_still_names_a_release_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        """The ADR's own wording: no `vars` handler afterwards, the symlink roots."""
        previous = self._previous(staged)
        plan = staged.answering(REHEARSAL_TCP)

        with pytest.raises(RehearsalFailedError) as raised:
            rolled_back(plan, previous)

        assert raised.value.step == "iii"
        assert raised.value.detail == f"it still names release {staged.plan.content_id}"

    def test_a_configuration_that_is_not_the_previous_one_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        previous = self._previous(staged)
        rollback(staged.plan)
        other = previous.model_copy(update={"config_hash": "0" * 64})

        with pytest.raises(RehearsalFailedError) as raised:
            rolled_back(staged.plan, other)

        assert raised.value.step == "iii"
        running = staged.running(REHEARSAL_TCP).describe()
        assert raised.value.detail == (
            f"{REHEARSAL_TCP} runs {running}, not the previous {other.describe()}"
        )

    def test_a_socket_that_still_answers_ends_the_rehearsal(self, staged: Staged) -> None:
        previous = self._previous(staged)
        rollback(staged.plan)
        plan = staged.answering(REHEARSAL_TCP, staged.plan.host.socket_admin)

        with pytest.raises(RehearsalFailedError) as raised:
            rolled_back(plan, previous)

        assert raised.value.step == "iii"
        assert raised.value.detail == (
            f"{staged.plan.host.socket_admin} still answers after the way back"
        )

    def test_a_socket_file_left_behind_ends_the_rehearsal(self, staged: Staged) -> None:
        previous = self._previous(staged)
        rollback(staged.plan)
        staged.plan.host.socket.parent.mkdir(parents=True, exist_ok=True)
        staged.plan.host.socket.touch()

        with pytest.raises(RehearsalFailedError) as raised:
            rolled_back(staged.plan, previous)

        assert raised.value.step == "iii"
        assert raised.value.detail == f"{staged.plan.host.socket} survived the rollback"

    def test_an_exec_reload_still_naming_the_socket_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        cutover(staged.plan)
        shown = f"ExecReload=[ argv[]=caddy reload --address {staged.plan.host.socket_admin} ]\n"
        plan = staged.show_answering(2, Completed(0, shown, ""))

        with pytest.raises(RehearsalFailedError) as raised:
            rollback(plan)

        assert raised.value.step == "iii"
        assert raised.value.detail == (
            f"ExecReload still names --address {staged.plan.host.socket_admin}"
        )

    def test_an_exec_reload_that_cannot_be_read_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        plan = staged.show_answering(2, Completed(1, "", "no such unit"))

        with pytest.raises(RehearsalFailedError) as raised:
            rollback(plan)

        assert raised.value.step == "iii"
        assert raised.value.detail == (
            f"systemctl show {REHEARSAL_UNIT} -p ExecReload failed: no such unit"
        )


class TestSteadyState:
    def test_one_reload_through_the_drop_in_reaches_the_socket_and_moves_nothing(
        self, staged: Staged
    ) -> None:
        steps = steady_state(staged.plan)

        assert names(steps)[-1] == "iv"
        assert details(steps)["iv"] == (
            f"the drop-in's reload reached {staged.plan.host.socket_admin}"
        )
        assert staged.caddy.admin_address == staged.plan.host.socket_admin
        assert staged.caddy.reloads >= 2

    def test_it_is_the_rehearsal_unit_that_is_reloaded(self, staged: Staged) -> None:
        cutover(staged.plan)

        steady_reload(staged.plan)

        assert ("systemctl", "reload", REHEARSAL_UNIT) in staged.droplet.argvs()

    def test_r_is_the_same_pair_before_and_after(self, staged: Staged) -> None:
        cutover(staged.plan)
        before = staged.running(staged.plan.host.socket_admin)

        steady_reload(staged.plan)

        assert staged.running(staged.plan.host.socket_admin) == before

    def test_a_reload_that_fails_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        staged.caddy.fail_reloads = 1

        with pytest.raises(RehearsalFailedError) as raised:
            steady_reload(staged.plan)

        assert raised.value.step == "iv"
        assert raised.value.detail.startswith(f"systemctl reload {REHEARSAL_UNIT} failed: Job for")

    def test_a_reload_that_failed_silently_reports_its_exit_code(self, staged: Staged) -> None:
        cutover(staged.plan)
        plan = staged.with_runner(("systemctl", "reload"), Completed(4, "", ""))

        with pytest.raises(RehearsalFailedError) as raised:
            steady_reload(plan)

        assert raised.value.detail == f"systemctl reload {REHEARSAL_UNIT} failed: 4"

    def test_a_socket_that_stopped_answering_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)

        def quiet(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            staged.caddy.admin_up = False
            return Completed(0, "", "")

        with pytest.raises(RehearsalFailedError) as raised:
            steady_reload(staged.with_runner(("systemctl", "reload"), quiet))

        assert raised.value.step == "iv"
        assert raised.value.detail == f"{staged.plan.host.socket_admin} stopped answering"

    def test_a_reload_that_moved_r_ends_the_rehearsal(self, staged: Staged) -> None:
        """A steady-state reload swaps the fragment, never the release that is live."""
        cutover(staged.plan)

        def moved(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            del argv, env
            staged.caddy.load({"apps": {"http": {"servers": {"srv0": {"routes": []}}}}})
            return Completed(0, "", "")

        with pytest.raises(RehearsalFailedError) as raised:
            steady_reload(staged.with_runner(("systemctl", "reload"), moved))

        assert raised.value.step == "iv"
        assert raised.value.detail.startswith("the reload moved R to ")

    def test_tcp_answering_again_ends_the_rehearsal(self, staged: Staged) -> None:
        cutover(staged.plan)
        plan = staged.answering(staged.plan.host.socket_admin, REHEARSAL_TCP)

        with pytest.raises(RehearsalFailedError) as raised:
            steady_reload(plan)

        assert raised.value.step == "iv"
        assert raised.value.detail == f"{REHEARSAL_TCP} answers"


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

        with pytest.raises(RehearsalFailedError) as raised:
            restarts(staged.plan)

        assert raised.value.step == "v.1"
        assert "admin socket precondition unmet" in raised.value.detail

    def test_a_restart_that_fails_ends_the_rehearsal_naming_the_attempt(
        self, staged: Staged
    ) -> None:
        cutover(staged.plan)
        plan = staged.with_runner(("systemctl", "restart"), Completed(1, "", "nope"))

        with pytest.raises(RehearsalFailedError) as raised:
            restarts(plan)

        assert raised.value.step == "v.1"
        assert raised.value.detail == f"systemctl restart {REHEARSAL_UNIT} failed: nope"

    def test_a_restart_that_failed_silently_reports_its_exit_code(self, staged: Staged) -> None:
        cutover(staged.plan)
        plan = staged.with_runner(("systemctl", "restart"), Completed(5, "", ""))

        with pytest.raises(RehearsalFailedError) as raised:
            restarts(plan)

        assert raised.value.detail == f"systemctl restart {REHEARSAL_UNIT} failed: 5"

    def test_a_hand_provisioned_socket_does_not_survive_the_restart(self, staged: Staged) -> None:
        cutover(staged.plan)

        step = hand_provisioned_socket_fails(staged.plan)

        assert step.name == "v.by-hand"
        assert step.detail.startswith("the recreated socket failed the facts: ")
        assert "mode" in step.detail

    def test_the_hand_provisioning_is_a_chmod_and_a_chgrp_of_that_socket(
        self, staged: Staged
    ) -> None:
        """A chgrp is a chown that leaves the owner alone, and -1 is how that is spelled."""
        cutover(staged.plan)
        staged.droplet.ownership.chowns.clear()

        hand_provisioned_socket_fails(staged.plan)

        gid = staged.droplet.ownership.groups["lovspor-release"]
        assert staged.droplet.ownership.chowns == [(staged.plan.host.socket, -1, gid)]

    def test_a_restart_inside_the_fixture_that_fails_names_the_fixture(
        self, staged: Staged
    ) -> None:
        cutover(staged.plan)
        seen: list[int] = []

        def restart(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            seen.append(1)
            return Completed(1, "", "nope") if len(seen) > 2 else staged.caddy.run(argv, env)

        with pytest.raises(RehearsalFailedError) as raised:
            restarts(staged.with_runner(("systemctl", "restart"), restart))

        assert raised.value.step == "v.by-hand"

    def test_the_fixture_leaves_the_instance_as_it_found_it(self, staged: Staged) -> None:
        cutover(staged.plan)
        kept = staged.plan.plane.caddyfile.read_bytes()

        hand_provisioned_socket_fails(staged.plan)

        assert staged.plan.plane.caddyfile.read_bytes() == kept
        assert _mode(staged.plan.host.socket) == SOCKET_MODE

    def test_what_the_fixture_writes_is_readable_by_caddy_whatever_the_umask(
        self, staged: Staged, strict_umask: None
    ) -> None:
        cutover(staged.plan)

        hand_provisioned_socket_fails(staged.plan)

        assert _mode(staged.plan.plane.caddyfile) == 0o644

    def test_a_fixture_that_still_carries_the_suffix_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        """The fixture proves nothing unless the recreated socket really loses the mode."""
        cutover(staged.plan)
        plan = replace(
            staged.plan,
            fixtures=replace(staged.plan.fixtures, unsuffixed=staged.plan.host.caddyfile_source),
        )

        with pytest.raises(RehearsalFailedError) as raised:
            hand_provisioned_socket_fails(plan)

        assert raised.value.step == "v.by-hand"
        assert raised.value.detail == (
            "a socket whose mode and group were set once by hand passed the four facts"
        )

    def test_a_refusal_about_something_else_than_the_mode_or_the_group_ends_the_rehearsal(
        self, staged: Staged
    ) -> None:
        """The fixture is about permissions; a socket that stopped answering is not that."""
        cutover(staged.plan)
        plan = replace(
            staged.plan,
            host=replace(staged.plan.host, admin_client=Answering(staged.caddy)),
            fixtures=replace(staged.plan.fixtures, unsuffixed=staged.plan.host.caddyfile_source),
        )

        with pytest.raises(RehearsalFailedError) as raised:
            hand_provisioned_socket_fails(plan)

        assert raised.value.step == "v.by-hand"
        assert raised.value.detail.startswith("the refusal is not about the mode or the group: ")


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

        report = rehearse(staged.plan)
        described = report.describe()

        assert len(described) == 17
        assert described == tuple(f"{step.name}: {step.detail}" for step in report.steps)
        assert described[0].startswith("i: ")
