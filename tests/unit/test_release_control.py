"""The reconciled triple, the transaction and the crash table (ADR-0014 Decision 6).

Two real envelopes, A and B, are built once; every test gets its own
copy of the releases root and its own Caddy host in a box
(``caddy_fakes.FakeCaddy``): a Caddyfile importing the active fragment,
a toy adapter over the real files, the admin endpoint and systemctl.
Kills are checkpoints that stop the transaction at a named step.
"""

import copy
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, Completed, ConfigPair, adapt, config_pair
from lovspor.release.control import (
    TRANSACTION_STEPS,
    CommitReport,
    ControlPlane,
    Situation,
    Triple,
    commit_release,
    live_release,
    read_triple,
    rollback,
    situation,
)
from lovspor.release.envelope import (
    BUILD_PREFIX,
    FRAGMENT_NAME,
    MARKER_NAME,
    Marker,
    read_marker,
)
from lovspor.release.errors import (
    CommitRefusedError,
    ControlPlaneError,
    IncompleteEnvelopeError,
    ReloadFailedError,
    UnobservableError,
    UnreconciledError,
)
from lovspor.release.reconcile import ReconcileReport, prune, reconcile
from tests.unit.caddy_fakes import toy_adapt
from tests.unit.control_host import PLACEHOLDER, Host, make_host, two_envelopes
from tests.unit.release_fixtures import World, make_world

NON_ASCII_PLACEHOLDER = "# Ørsta kommune sin side\n" + PLACEHOLDER
"""The same, with the kind of comment an operator's editor leaves: bytes outside ASCII."""
RELOAD_FAILURE = (
    "systemctl reload caddy failed: Job for caddy.service failed because the control process"
)


class Killed(Exception):  # noqa: N818 — a simulated process death, not a lovspor error
    """The process died right after the named step."""


def _kill_at(step: str):  # type: ignore[no-untyped-def]
    def checkpoint(reached: str) -> None:
        if reached == step:
            raise Killed(step)

    return checkpoint


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


class TestLiveRelease:
    def test_nothing_live_yet_is_reconciled_with_no_release(self, host: Host) -> None:
        assert live_release(host.plane) is None
        triple = read_triple(host.plane)
        assert triple.running.release_id is None
        assert triple.disk == triple.running
        assert triple.marker is None
        assert situation(triple) == Situation.reconciled

    def test_the_id_iff_r_d_and_m_agree_on_id_and_hash(self, live_a: Host) -> None:
        assert live_release(live_a.plane) == live_a.a
        triple = read_triple(live_a.plane)
        assert triple.running == triple.disk == live_a.pair_of(live_a.a)
        assert triple.marker == Marker(active=live_a.a, previous=None)
        assert triple.describe().startswith(f"R=({live_a.a}, ")

    def test_a_hand_reloaded_edit_with_the_same_id_is_unreconciled(self, live_a: Host) -> None:
        """The id alone is not trusted: one handler changed moves the hash."""
        edited: dict[str, Any] = copy.deepcopy(live_a.caddy.running_config())  # type: ignore[arg-type]
        site = edited["apps"]["http"]["servers"]["srv0"]["routes"][0]
        site["handle"][0]["routes"].append({"handle": [{"handler": "file_server", "hide": ["x"]}]})
        live_a.caddy.load(edited)

        with pytest.raises(UnreconciledError, match="host is foreign") as caught:
            live_release(live_a.plane)
        message = str(caught.value)
        assert config_pair(edited).config_hash[:12] in message
        assert live_a.pair_of(live_a.a).config_hash[:12] in message
        assert message.count(live_a.a) >= 2

    def test_release_a_root_beside_release_b_redirects_is_unreconciled(self, live_a: Host) -> None:
        """A mixed configuration adapts to a hash equal to neither release's."""
        mixed = live_a.fragment_of(live_a.a).replace(
            f"{live_a.root_of(live_a.a)}/corpus/redirects",
            f"{live_a.root_of(live_a.b)}/corpus/redirects",
        )
        assert mixed != live_a.fragment_of(live_a.a)
        assert "redir " in (live_a.releases / live_a.b / "corpus" / "redirects.caddy").read_text()
        staged = live_a.plane.fragment.with_name("mixed.caddy")
        staged.write_text(mixed, encoding="utf-8")
        live_a.caddy.load(
            toy_adapt(live_a.plane.caddyfile, {"LOVSPOR_RELEASE_FRAGMENT": str(staged)})
        )

        running = live_a.running()
        assert running.release_id == live_a.a
        assert running.config_hash not in {
            live_a.pair_of(live_a.a).config_hash,
            live_a.pair_of(live_a.b).config_hash,
        }
        with pytest.raises(UnreconciledError):
            live_release(live_a.plane)

    def test_the_admin_endpoint_unreachable_is_its_own_state(self, live_a: Host) -> None:
        live_a.caddy.admin_up = False
        before = live_a.snapshot()

        with pytest.raises(UnobservableError, match="admin_unreachable") as caught:
            live_release(live_a.plane)
        assert caught.value.reason == "admin_unreachable"
        assert f"D=({live_a.a}, " in caught.value.detail
        assert f"M=(active {live_a.a})" in caught.value.detail
        assert live_a.snapshot() == before

    def test_unreachable_before_anything_was_live_prints_d_and_no_marker(self, host: Host) -> None:
        host.caddy.admin_up = False
        disk = adapt(host.caddy, host.plane.caddyfile, host.plane.fragment)

        with pytest.raises(UnobservableError) as caught:
            live_release(host.plane)
        assert caught.value.detail == (
            f"connect: no such file or directory; D={disk.describe()} M=(active none)"
        )

    def test_the_marker_release_replaced_by_hand_with_no_release_is_foreign(
        self, live_a: Host
    ) -> None:
        """R names no release while M does: not the staged row, whatever D says."""
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")
        placeholder = live_a.plane.fragment.with_name("placeholder.caddy")
        placeholder.write_text(PLACEHOLDER, encoding="utf-8")
        live_a.caddy.load(
            toy_adapt(live_a.plane.caddyfile, {"LOVSPOR_RELEASE_FRAGMENT": str(placeholder)})
        )

        triple = read_triple(live_a.plane)

        assert triple.running.release_id is None and triple.disk.release_id == live_a.b
        assert triple.marker == Marker(active=live_a.a, previous=None)
        assert situation(triple) == Situation.foreign


class TestTriple:
    def test_describe_prints_the_three_sources_with_none_for_what_is_absent(self) -> None:
        running = ConfigPair(release_id="a" * 64, config_hash="1" * 64)
        disk = ConfigPair(release_id=None, config_hash="2" * 64)
        prefix = f"R=({'a' * 64}, {'1' * 12}) D=(none, {'2' * 12}) "

        unmarked = Triple(running=running, disk=disk, marker=None)
        first = Triple(running=running, disk=disk, marker=Marker(active="a" * 64, previous=None))
        second = Triple(
            running=running,
            disk=disk,
            marker=Marker(active="b" * 64, previous="a" * 64),
        )

        assert unmarked.describe() == prefix + "M=(active none, previous none)"
        assert first.describe() == prefix + f"M=(active {'a' * 64}, previous none)"
        assert second.describe() == prefix + f"M=(active {'b' * 64}, previous {'a' * 64})"
        assert (unmarked.marked, first.marked, second.marked) == (None, "a" * 64, "b" * 64)


class TestCommit:
    def test_makes_b_live_through_the_transaction(self, live_a: Host) -> None:
        reached: list[str] = []

        report = commit_release(live_a.plane, live_a.b, reached.append)

        assert tuple(reached) == TRANSACTION_STEPS
        assert report.active == live_a.b and report.previous == live_a.a
        assert not report.already_live
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.b)
        assert live_a.running() == live_a.pair_of(live_a.b)
        assert read_marker(live_a.releases) == Marker(active=live_a.b, previous=live_a.a)
        assert live_release(live_a.plane) == live_a.b
        assert live_a.caddy.reloads == 1
        assert not live_a.plane.next_fragment.exists()
        assert not live_a.plane.previous_fragment.exists()

    def test_the_first_release_replaces_a_foreign_fragment_and_keeps_a_copy(
        self, host: Host
    ) -> None:
        report = commit_release(host.plane, host.a)

        assert report.previous is None
        assert live_release(host.plane) == host.a
        assert read_marker(host.releases) == Marker(active=host.a, previous=None)
        assert host.plane.previous_fragment.read_text(encoding="utf-8") == PLACEHOLDER

    def test_the_kept_copy_is_read_as_utf_8_whatever_the_process_locale(
        self, host: Host, c_locale: None
    ) -> None:
        host.plane.fragment.write_bytes(NON_ASCII_PLACEHOLDER.encode("utf-8"))
        host.caddy.restart()

        commit_release(host.plane, host.a)

        assert host.plane.previous_fragment.read_bytes() == NON_ASCII_PLACEHOLDER.encode("utf-8")

    def test_the_fragment_and_the_marker_are_world_readable_whatever_the_umask(
        self, live_a: Host, strict_umask: None
    ) -> None:
        """``ExecReload`` runs as ``User=caddy``, which must read what root wrote."""
        commit_release(live_a.plane, live_a.b)

        assert stat.S_IMODE(live_a.plane.fragment.stat().st_mode) == 0o644
        assert stat.S_IMODE((live_a.releases / MARKER_NAME).stat().st_mode) == 0o644

    def test_a_pair_adapted_at_next_is_not_what_caddy_runs_once_the_fragment_is_renamed(
        self, live_a: Host
    ) -> None:
        """#317: Caddy hides the fragment by the path it imported it from, so the same bytes
        adapted at ``.next`` are one ``hide`` entry apart from R after the rename and reload;
        adapted where Caddy reads them, they are R."""
        live_a.plane.next_fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")
        at_next = adapt(live_a.caddy, live_a.plane.caddyfile, live_a.plane.next_fragment)

        live_a.plane.next_fragment.replace(live_a.plane.fragment)
        assert live_a.caddy.run(("systemctl", "reload", "caddy"), {}).returncode == 0

        assert at_next.release_id == live_a.running().release_id == live_a.b
        assert at_next != live_a.running()
        assert (
            adapt(live_a.caddy, live_a.plane.caddyfile, live_a.plane.fragment) == live_a.running()
        )

    def test_the_live_release_is_not_reloaded_again(self, live_a: Host) -> None:
        before = live_a.snapshot()

        report = commit_release(live_a.plane, live_a.a)

        assert report == CommitReport(active=live_a.a, previous=live_a.a, already_live=True)
        assert live_a.caddy.reloads == 0
        assert live_a.snapshot() == before

    def test_the_transaction_validates_and_adapts_next_before_touching_the_fragment(
        self, live_a: Host
    ) -> None:
        commit_release(live_a.plane, live_a.b)

        calls = live_a.caddy.calls
        staged = [
            index
            for index, (_, env) in enumerate(calls)
            if env.get(FRAGMENT_ENV) == str(live_a.plane.next_fragment)
        ]
        assert [calls[index][0][1] for index in staged] == ["validate", "adapt"]
        reload_at = [argv for argv, _ in calls].index(("systemctl", "reload", "caddy"))
        assert reload_at > max(staged)

    def test_an_incomplete_envelope_is_never_staged(self, live_a: Host) -> None:
        (live_a.releases / live_a.b / FRAGMENT_NAME).unlink()
        before = live_a.snapshot()

        with pytest.raises(IncompleteEnvelopeError, match="not a complete envelope"):
            commit_release(live_a.plane, live_a.b)
        assert live_a.snapshot() == before
        assert live_a.caddy.reloads == 0

    def test_a_build_directory_is_never_staged(self, live_a: Host) -> None:
        with pytest.raises(IncompleteEnvelopeError):
            commit_release(live_a.plane, f"{BUILD_PREFIX}x")

    def test_a_fragment_naming_another_release_is_refused(self, live_a: Host) -> None:
        (live_a.releases / live_a.b / FRAGMENT_NAME).write_text(
            live_a.fragment_of(live_a.a), encoding="utf-8"
        )

        with pytest.raises(CommitRefusedError, match="does not name its release"):
            commit_release(live_a.plane, live_a.b)
        assert live_release(live_a.plane) == live_a.a

    def test_a_caddyfile_that_does_not_import_the_fragment_is_refused(self, live_a: Host) -> None:
        text = live_a.plane.caddyfile.read_text(encoding="utf-8")
        literal = f"\timport {live_a.plane.fragment}\n"
        live_a.plane.caddyfile.write_text(
            "\n".join(
                literal.rstrip("\n") if line.lstrip().startswith("import ") else line
                for line in text.splitlines()
            )
            + "\n",
            encoding="utf-8",
        )

        with pytest.raises(CommitRefusedError) as caught:
            commit_release(live_a.plane, live_a.b)
        assert str(caught.value) == (
            f"the composed configuration names {live_a.a}, not {live_a.b}; "
            "does the Caddyfile import the fragment?"
        )
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert live_a.caddy.reloads == 0

    def test_a_caddyfile_without_the_import_composes_no_release(self, host: Host) -> None:
        host.plane.caddyfile.write_text("lovspor.test {\n\tfile_server\n}\n", encoding="utf-8")
        host.caddy.restart()

        with pytest.raises(CommitRefusedError) as caught:
            commit_release(host.plane, host.a)
        assert str(caught.value) == (
            f"the composed configuration names no release, not {host.a}; "
            "does the Caddyfile import the fragment?"
        )
        assert host.caddy.reloads == 0

    def test_a_fragment_caddy_refuses_to_validate_is_refused_before_anything_public(
        self, live_a: Host
    ) -> None:
        fragment = live_a.releases / live_a.b / FRAGMENT_NAME
        fragment.write_text(
            live_a.fragment_of(live_a.b).replace("redirects*.caddy", "redirects.missing"),
            encoding="utf-8",
        )

        with pytest.raises(CommitRefusedError, match="caddy validate refused"):
            commit_release(live_a.plane, live_a.b)
        assert live_a.running() == live_a.pair_of(live_a.a)
        assert live_a.caddy.reloads == 0
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)

    def test_unreconciled_refuses(self, live_a: Host) -> None:
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")

        with pytest.raises(UnreconciledError, match="staged_not_reloaded"):
            commit_release(live_a.plane, live_a.b)

    def test_admin_unreachable_refuses_and_touches_nothing(self, live_a: Host) -> None:
        live_a.caddy.admin_up = False
        before = live_a.snapshot()

        with pytest.raises(UnobservableError, match="admin_unreachable"):
            commit_release(live_a.plane, live_a.b)
        assert live_a.snapshot() == before


class TestReloadFailure:
    def test_reverts_to_the_previous_fragment_and_reloads_it(self, live_a: Host) -> None:
        live_a.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError) as caught:
            commit_release(live_a.plane, live_a.b)

        assert str(caught.value) == f"release {live_a.b[:12]} not switched: {RELOAD_FAILURE}"
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert live_a.running() == live_a.pair_of(live_a.a)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)
        assert live_release(live_a.plane) == live_a.a
        assert live_a.caddy.reloads == 1

    def test_the_reverted_fragment_is_world_readable_whatever_the_umask(
        self, live_a: Host, strict_umask: None
    ) -> None:
        live_a.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError):
            commit_release(live_a.plane, live_a.b)

        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert stat.S_IMODE(live_a.plane.fragment.stat().st_mode) == 0o644

    def test_a_reload_that_answers_but_serves_something_else_is_reverted(
        self, live_a: Host
    ) -> None:
        foreign = copy.deepcopy(live_a.caddy.running_config())
        original_restart = live_a.caddy.restart
        live_a.caddy.restart = lambda: live_a.caddy.load(foreign)  # type: ignore[method-assign]

        with pytest.raises(ReloadFailedError, match="after the reload Caddy runs"):
            commit_release(live_a.plane, live_a.b)

        live_a.caddy.restart = original_restart  # type: ignore[method-assign]
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)

    def test_the_first_release_reverts_to_the_kept_copy(self, host: Host) -> None:
        host.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError):
            commit_release(host.plane, host.a)

        assert host.plane.fragment.read_text(encoding="utf-8") == PLACEHOLDER
        assert read_marker(host.releases) is None
        assert live_release(host.plane) is None

    def test_the_kept_copy_is_restored_byte_for_byte_whatever_the_process_locale(
        self, host: Host, c_locale: None
    ) -> None:
        host.plane.fragment.write_bytes(NON_ASCII_PLACEHOLDER.encode("utf-8"))
        host.caddy.restart()
        host.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError):
            commit_release(host.plane, host.a)

        assert host.plane.fragment.read_bytes() == NON_ASCII_PLACEHOLDER.encode("utf-8")
        assert live_release(host.plane) is None

    def test_a_revert_that_also_fails_to_reload_is_named(self, live_a: Host) -> None:
        live_a.caddy.fail_reloads = 2

        with pytest.raises(ReloadFailedError, match="revert did not restore"):
            commit_release(live_a.plane, live_a.b)
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert live_release(live_a.plane) == live_a.a


class Witness:
    """The fake's runner, noting which release the active fragment holds whenever a command
    reads it — ``caddy adapt`` named at that path, or a unit reload through the default."""

    def __init__(self, host: Host) -> None:
        self.host = host
        self.seen: list[tuple[str, str]] = []

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        active = self.host.plane.fragment
        if env.get(FRAGMENT_ENV, str(active)) == str(active):
            text = active.read_text(encoding="utf-8")
            names = {
                self.host.fragment_of(self.host.a): "A",
                self.host.fragment_of(self.host.b): "B",
            }
            self.seen.append((argv[1], names.get(text, "other")))
        return self.host.caddy.run(argv, env)

    def plane(self) -> ControlPlane:
        return ControlPlane(
            self.host.releases,
            self.host.plane.caddyfile,
            self.host.plane.fragment,
            self,
            self.host.caddy,
        )


class TestWhereRIsComparedFrom:
    """#317: Caddy hides the fragment by the path it imported it from, so the pair R is compared
    with is adapted at the active fragment's path once the fragment the reload reads is there;
    ``.next`` is asked only for its content. The first entry is the triple's own D."""

    def test_the_commit_adapts_the_candidate_where_the_reload_reads_it(self, live_a: Host) -> None:
        witness = Witness(live_a)

        commit_release(witness.plane(), live_a.b)

        assert witness.seen == [("adapt", "A"), ("adapt", "B"), ("reload", "B")]

    def test_the_revert_adapts_the_old_fragment_once_it_is_back(self, live_a: Host) -> None:
        witness = Witness(live_a)
        live_a.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError, match="not switched"):
            commit_release(witness.plane(), live_a.b)

        assert witness.seen == [
            ("adapt", "A"),
            ("adapt", "B"),
            ("reload", "B"),
            ("adapt", "A"),
            ("reload", "A"),
        ]

    def test_abandon_adapts_the_markers_fragment_once_it_is_back(self, live_a: Host) -> None:
        with pytest.raises(Killed):
            commit_release(live_a.plane, live_a.b, _kill_at("committed"))
        witness = Witness(live_a)

        assert reconcile(witness.plane(), "abandon").action == "abandoned"

        assert witness.seen == [("adapt", "B"), ("adapt", "A"), ("reload", "A")]


class TestRollback:
    def test_the_previous_release_through_the_same_transaction(self, live_a: Host) -> None:
        commit_release(live_a.plane, live_a.b)
        reached: list[str] = []

        report = rollback(live_a.plane, reached.append)

        assert tuple(reached) == TRANSACTION_STEPS
        assert report.active == live_a.a and report.previous == live_a.b
        assert live_release(live_a.plane) == live_a.a
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=live_a.b)
        assert live_a.running() == live_a.pair_of(live_a.a)

    def test_rolling_forward_is_the_same_command(self, live_a: Host) -> None:
        commit_release(live_a.plane, live_a.b)
        rollback(live_a.plane)

        rollback(live_a.plane)

        assert live_release(live_a.plane) == live_a.b
        assert read_marker(live_a.releases) == Marker(active=live_a.b, previous=live_a.a)

    def test_without_a_previous_release_there_is_nothing_to_roll_back_to(
        self, live_a: Host
    ) -> None:
        with pytest.raises(ControlPlaneError) as caught:
            rollback(live_a.plane)
        assert str(caught.value) == "no previous release in the marker; nothing to roll back to"

    def test_refuses_while_unreconciled_or_unobservable(self, live_a: Host) -> None:
        commit_release(live_a.plane, live_a.b)
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.a), encoding="utf-8")
        with pytest.raises(UnreconciledError):
            rollback(live_a.plane)

        live_a.caddy.admin_up = False
        with pytest.raises(UnobservableError):
            rollback(live_a.plane)


class TestTheCrashTable:
    def _kill(self, host: Host, step: str) -> None:
        with pytest.raises(Killed):
            commit_release(host.plane, host.b, _kill_at(step))

    @pytest.mark.parametrize("step", ["staged", "validated"])
    def test_a_crash_during_staging_leaves_the_host_reconciled(
        self, live_a: Host, step: str
    ) -> None:
        self._kill(live_a, step)

        assert live_a.plane.next_fragment.read_text(encoding="utf-8") == live_a.fragment_of(
            live_a.b
        )
        assert live_release(live_a.plane) == live_a.a
        assert reconcile(live_a.plane) == ReconcileReport(
            situation=Situation.reconciled,
            live=live_a.a,
            action="none",
            triple=read_triple(live_a.plane).describe(),
        )

        commit_release(live_a.plane, live_a.b)

        assert live_release(live_a.plane) == live_a.b
        assert not live_a.plane.next_fragment.exists()

    def test_a_crash_after_the_fragment_rename_is_d_new_r_old_m_old(self, live_a: Host) -> None:
        self._kill(live_a, "committed")

        triple = read_triple(live_a.plane)
        assert triple.disk.release_id == live_a.b
        assert triple.running == live_a.pair_of(live_a.a)
        assert triple.marker == Marker(active=live_a.a, previous=None)
        assert situation(triple) == Situation.staged
        for refused in (live_release, rollback, prune):
            with pytest.raises(UnreconciledError, match="staged_not_reloaded"):
                refused(live_a.plane)  # type: ignore[operator]
        with pytest.raises(UnreconciledError):
            commit_release(live_a.plane, live_a.b)
        with pytest.raises(UnreconciledError) as caught:
            reconcile(live_a.plane)
        assert str(caught.value) == (
            f"host is staged_not_reloaded: {triple.describe()}; resolve with --complete "
            "(D becomes the truth) or --abandon (M's release is restored)"
        )
        assert live_a.caddy.reloads == 0

    def test_complete_after_the_fragment_rename_reloads_then_marks(self, live_a: Host) -> None:
        self._kill(live_a, "committed")

        report = reconcile(live_a.plane, "complete")

        assert report.situation == Situation.reconciled and report.action == "completed"
        assert report.live == live_a.b
        assert live_a.running() == live_a.pair_of(live_a.b)
        assert read_marker(live_a.releases) == Marker(active=live_a.b, previous=live_a.a)
        assert live_release(live_a.plane) == live_a.b

    def test_abandon_after_the_fragment_rename_restores_the_previous_fragment(
        self, live_a: Host
    ) -> None:
        self._kill(live_a, "committed")

        report = reconcile(live_a.plane, "abandon")

        assert report.situation == Situation.reconciled and report.action == "abandoned"
        assert report.live == live_a.a
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert live_a.running() == live_a.pair_of(live_a.a)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)
        assert live_release(live_a.plane) == live_a.a

    def test_the_abandoned_fragment_is_world_readable_whatever_the_umask(
        self, live_a: Host, strict_umask: None
    ) -> None:
        self._kill(live_a, "committed")

        reconcile(live_a.plane, "abandon")

        assert stat.S_IMODE(live_a.plane.fragment.stat().st_mode) == 0o644

    def test_a_resolution_whose_reload_fails_is_named_and_leaves_the_marker(
        self, live_a: Host
    ) -> None:
        self._kill(live_a, "committed")
        live_a.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError) as completing:
            reconcile(live_a.plane, "complete")
        assert str(completing.value) == f"complete failed: {RELOAD_FAILURE}"
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.b)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)

        live_a.caddy.fail_reloads = 1
        with pytest.raises(ReloadFailedError) as abandoning:
            reconcile(live_a.plane, "abandon")
        assert str(abandoning.value) == f"abandon failed: {RELOAD_FAILURE}"
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)

    def test_abandon_refuses_when_reload_succeeds_but_caddy_serves_something_else(
        self, live_a: Host
    ) -> None:
        self._kill(live_a, "committed")
        foreign: dict[str, Any] = copy.deepcopy(live_a.caddy.running_config())  # type: ignore[assignment]
        site = foreign["apps"]["http"]["servers"]["srv0"]["routes"][0]
        site["handle"][0]["routes"].append({"handle": [{"handler": "file_server"}]})
        original_restart = live_a.caddy.restart
        live_a.caddy.restart = lambda: live_a.caddy.load(foreign)  # type: ignore[method-assign]

        with pytest.raises(ReloadFailedError, match="after the reload Caddy runs"):
            reconcile(live_a.plane, "abandon")

        live_a.caddy.restart = original_restart  # type: ignore[method-assign]
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)

    def test_a_crash_after_the_reload_is_completed_by_the_marker_alone(self, live_a: Host) -> None:
        self._kill(live_a, "reloaded")

        triple = read_triple(live_a.plane)
        assert triple.running == triple.disk == live_a.pair_of(live_a.b)
        assert triple.marker == Marker(active=live_a.a, previous=None)
        assert situation(triple) == Situation.reloaded
        with pytest.raises(UnreconciledError, match="reloaded_marker_missing"):
            live_release(live_a.plane)

        report = reconcile(live_a.plane)

        assert report == ReconcileReport(
            situation=Situation.reconciled,
            live=live_a.b,
            action="marker_written",
            triple=triple.describe(),
        )
        assert read_marker(live_a.releases) == Marker(active=live_a.b, previous=live_a.a)
        assert live_a.caddy.reloads == 1
        assert live_release(live_a.plane) == live_a.b

    def test_a_crash_after_the_marker_is_a_finished_transaction(self, live_a: Host) -> None:
        self._kill(live_a, "marked")

        assert live_release(live_a.plane) == live_a.b
        assert read_marker(live_a.releases) == Marker(active=live_a.b, previous=live_a.a)

    @pytest.mark.parametrize("step", ["committed", "reloaded"])
    def test_a_restart_in_a_killed_state_serves_d_whole(self, live_a: Host, step: str) -> None:
        self._kill(live_a, step)

        live_a.caddy.restart()

        assert live_a.running() == live_a.pair_of(live_a.b)
        assert situation(read_triple(live_a.plane)) == Situation.reloaded
        assert reconcile(live_a.plane).action == "marker_written"
        assert live_release(live_a.plane) == live_a.b


class TestTheStagedRowByReleaseId:
    """#317, owner decision of 2026-09-15: the crash after the rename is recognised by release id.

    R cannot be compared by hash with M's fragment adapted where the release keeps it: Caddy
    hides the fragment by the path it imported it from, and D's new fragment already holds the
    active fragment's path. The row is R naming M's release — none while no marker exists —
    beside a D naming another release.
    """

    def _crash_after_the_rename(self, host: Host) -> Triple:
        with pytest.raises(Killed):
            commit_release(host.plane, host.b, _kill_at("committed"))
        return read_triple(host.plane)

    def test_the_crash_after_the_rename_reads_staged_where_no_hash_could_say_so(
        self, live_a: Host
    ) -> None:
        triple = self._crash_after_the_rename(live_a)
        kept = live_a.releases / live_a.a / FRAGMENT_NAME

        assert (triple.running.release_id, triple.disk.release_id) == (live_a.a, live_a.b)
        assert triple.running == live_a.pair_of(live_a.a)
        assert triple.running != adapt(live_a.caddy, live_a.plane.caddyfile, kept)
        assert situation(triple) == Situation.staged

    def test_every_other_command_refuses_it_by_name_and_moves_nothing(self, live_a: Host) -> None:
        self._crash_after_the_rename(live_a)
        before = live_a.snapshot()

        for command in (live_release, rollback, prune, lambda p: commit_release(p, live_a.b)):
            with pytest.raises(UnreconciledError, match="^host is staged_not_reloaded: "):
                command(live_a.plane)  # type: ignore[operator]

        assert live_a.snapshot() == before
        assert live_a.caddy.reloads == 0

    @pytest.mark.parametrize(("action", "resolved"), [("complete", "b"), ("abandon", "a")])
    def test_complete_and_abandon_resolve_it_end_to_end(
        self, live_a: Host, action: str, resolved: str
    ) -> None:
        triple = self._crash_after_the_rename(live_a)
        assert situation(triple) == Situation.staged
        live = getattr(live_a, resolved)

        report = reconcile(live_a.plane, action)  # type: ignore[arg-type]

        assert (report.situation, report.live, report.triple) == (
            Situation.reconciled,
            live,
            triple.describe(),
        )
        assert live_release(live_a.plane) == live
        assert live_a.running() == live_a.pair_of(live)
        previous = live_a.a if action == "complete" else None
        assert read_marker(live_a.releases) == Marker(active=live, previous=previous)

    def test_r_naming_a_release_other_than_the_markers_is_foreign(self, live_a: Host) -> None:
        fragment = live_a.releases / live_a.b / FRAGMENT_NAME
        installed = {fragment: live_a.plane.fragment}
        env = {FRAGMENT_ENV: str(fragment)}
        live_a.caddy.load(toy_adapt(live_a.plane.caddyfile, env, imported_as=installed))

        triple = read_triple(live_a.plane)

        assert (triple.running.release_id, triple.disk.release_id) == (live_a.b, live_a.a)
        assert triple.marked == live_a.a
        assert situation(triple) == Situation.foreign

    @pytest.mark.parametrize("disk", ["no release", "the same release"])
    def test_r_naming_the_markers_release_beside_a_d_naming_no_other_is_foreign(
        self, live_a: Host, disk: str
    ) -> None:
        text = PLACEHOLDER
        if disk == "the same release":
            text = live_a.fragment_of(live_a.a).replace(
                f"{live_a.root_of(live_a.a)}/corpus/redirects",
                f"{live_a.root_of(live_a.b)}/corpus/redirects",
            )
        live_a.plane.fragment.write_text(text, encoding="utf-8")

        triple = read_triple(live_a.plane)

        assert triple.running == live_a.pair_of(live_a.a) != triple.disk
        assert triple.disk.release_id == (None if disk == "no release" else live_a.a)
        assert situation(triple) == Situation.foreign

    @pytest.mark.parametrize(
        ("running", "disk", "marked", "row"),
        [
            ("a", "b", "a", Situation.staged),
            (None, "b", None, Situation.staged),
            ("a", "a", "a", Situation.foreign),
            ("a", None, "a", Situation.foreign),
            ("b", "a", "a", Situation.foreign),
            ("b", "c", "a", Situation.foreign),
            (None, "b", "a", Situation.foreign),
            ("a", "b", None, Situation.foreign),
        ],
    )
    def test_r_and_d_apart_are_staged_by_release_id_alone(
        self, running: str | None, disk: str | None, marked: str | None, row: Situation
    ) -> None:
        """R and D always differ here; only the three release ids decide the row."""
        ids = {name: name * 64 for name in "abc"}
        triple = Triple(
            running=ConfigPair(release_id=ids.get(running or ""), config_hash="1" * 64),
            disk=ConfigPair(release_id=ids.get(disk or ""), config_hash="2" * 64),
            marker=Marker(active=ids[marked], previous=None) if marked else None,
        )

        assert situation(triple) == row


class TestForeign:
    def _hand_reload(self, host: Host) -> None:
        edited: dict[str, Any] = copy.deepcopy(host.caddy.running_config())  # type: ignore[arg-type]
        site = edited["apps"]["http"]["servers"]["srv0"]["routes"][0]
        site["handle"][0]["routes"].append({"handle": [{"handler": "file_server", "hide": ["x"]}]})
        host.caddy.load(edited)

    def test_is_reported_and_acts_only_on_a_flag(self, live_a: Host) -> None:
        self._hand_reload(live_a)
        before = live_a.snapshot()

        with pytest.raises(UnreconciledError, match="host is foreign"):
            reconcile(live_a.plane)
        assert live_a.snapshot() == before
        assert live_a.caddy.reloads == 0

    def test_complete_makes_the_disk_the_truth(self, live_a: Host) -> None:
        self._hand_reload(live_a)

        report = reconcile(live_a.plane, "complete")

        assert report.action == "completed" and report.live == live_a.a
        assert live_a.running() == live_a.pair_of(live_a.a)
        assert live_release(live_a.plane) == live_a.a

    def test_abandon_makes_the_marker_the_truth(self, live_a: Host) -> None:
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")
        self._hand_reload(live_a)

        report = reconcile(live_a.plane, "abandon")

        assert report.action == "abandoned" and report.live == live_a.a
        assert live_a.plane.fragment.read_text(encoding="utf-8") == live_a.fragment_of(live_a.a)
        assert live_release(live_a.plane) == live_a.a

    def test_complete_needs_a_release_on_disk(self, live_a: Host) -> None:
        live_a.plane.fragment.write_text(PLACEHOLDER, encoding="utf-8")
        self._hand_reload(live_a)

        with pytest.raises(CommitRefusedError) as caught:
            reconcile(live_a.plane, "complete")
        assert str(caught.value) == "the configuration on disk names no release; cannot complete"

    def test_abandon_needs_the_markers_release_fragment(self, live_a: Host) -> None:
        """R names M's release beside a D naming another: the staged row by release id (#317),
        whether or not M's fragment is still kept; abandon refuses naming it."""
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")
        (live_a.releases / live_a.a / FRAGMENT_NAME).unlink()

        assert situation(read_triple(live_a.plane)) == Situation.staged
        with pytest.raises(IncompleteEnvelopeError, match="release.caddy is unreadable"):
            reconcile(live_a.plane, "abandon")

    def test_abandon_with_nothing_ever_live_needs_the_kept_copy(self, host: Host) -> None:
        host.plane.fragment.write_text(host.fragment_of(host.b), encoding="utf-8")

        assert situation(read_triple(host.plane)) == Situation.staged
        with pytest.raises(ControlPlaneError) as caught:
            reconcile(host.plane, "abandon")
        assert str(caught.value) == "no previous fragment to restore: nothing was live before"

    def test_a_release_var_that_is_not_an_id_is_refused_by_name_not_by_traceback(
        self, live_a: Host
    ) -> None:
        """A hand-edited ``vars`` line whose value is no longer a release id.

        R and D agree on it, which before issue #271 put the host on the
        *reloaded* row and handed the marker a name pydantic refuses --
        a raw ``ValidationError`` past the command layer's refusal families,
        reaching the operator as a traceback in the middle of a half-done
        cutover. The var names no release, so the row is *foreign*: every
        command refuses in one line and the marker is left alone.
        """
        edited = live_a.fragment_of(live_a.a).replace(
            f"vars lovspor_release {live_a.a}\n", 'vars lovspor_release ""\n'
        )
        live_a.plane.fragment.write_text(edited, encoding="utf-8")
        live_a.caddy.restart()
        before = live_a.snapshot()

        triple = read_triple(live_a.plane)

        assert triple.running == triple.disk
        assert triple.running.release_id is None
        assert situation(triple) == Situation.foreign
        for refused in (live_release, rollback, prune):
            with pytest.raises(UnreconciledError, match="host is foreign"):
                refused(live_a.plane)  # type: ignore[operator]
        with pytest.raises(UnreconciledError, match="host is foreign"):
            reconcile(live_a.plane)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)
        assert live_a.snapshot() == before

    def test_admin_unreachable_refuses_reconcile_with_d_and_m_printed(self, live_a: Host) -> None:
        live_a.caddy.admin_up = False

        with pytest.raises(UnobservableError) as caught:
            reconcile(live_a.plane, "complete")
        assert "D=(" in caught.value.detail and "M=(active" in caught.value.detail


class TestPrune:
    def _litter(self, host: Host) -> tuple[Path, Path]:
        stale = host.releases / f"{BUILD_PREFIX}stale"
        running = host.releases / f"{BUILD_PREFIX}running"
        for directory in (stale, running):
            directory.mkdir()
            (directory / "corpus").mkdir()
        (host.releases / "20260908T120000Z-abcdef123456").mkdir()
        (host.releases / ("9" * 64)).mkdir()
        return stale, running

    def test_removes_everything_but_r_d_m_and_previous(self, live_a: Host) -> None:
        commit_release(live_a.plane, live_a.b)
        stale, running = self._litter(live_a)

        report = prune(live_a.plane, keep_build=running)

        assert report.retained == tuple(sorted((live_a.a, live_a.b)))
        assert set(report.removed) == {stale.name, "9" * 64}
        assert (live_a.releases / live_a.a).is_dir() and (live_a.releases / live_a.b).is_dir()
        assert running.is_dir() and not stale.exists()
        assert (live_a.releases / "20260908T120000Z-abcdef123456").is_dir()
        assert (live_a.releases / MARKER_NAME).is_file()

    def test_a_staged_release_that_never_went_live_is_not_the_rollback_target(
        self, live_a: Host
    ) -> None:
        """B was finalized but never committed: it is named by nothing, so it goes."""
        report = prune(live_a.plane)

        assert report.removed == (live_a.b,)
        assert read_marker(live_a.releases) == Marker(active=live_a.a, previous=None)
        with pytest.raises(ControlPlaneError, match="no previous release"):
            rollback(live_a.plane)

    def test_without_a_running_build_every_build_directory_goes(self, live_a: Host) -> None:
        stale, running = self._litter(live_a)

        prune(live_a.plane)

        assert not stale.exists() and not running.exists()

    def test_a_directory_whose_name_is_an_id_plus_a_newline_is_not_pruned(
        self, live_a: Host
    ) -> None:
        """`prune` deletes what it can name, and it cannot name this.

        A POSIX directory name may contain a newline, and `$` used to match
        just before one, so `<id>\n` read as an id-named release and was
        deleted — a foreign directory removed by a command whose contract is
        to remove only the release directories no record names.
        """
        foreign = live_a.releases / (live_a.b + "\n")
        foreign.mkdir()

        report = prune(live_a.plane)

        assert foreign.name not in report.removed
        assert foreign.is_dir()

    def test_refuses_while_unreconciled_and_removes_nothing(self, live_a: Host) -> None:
        self._litter(live_a)
        live_a.plane.fragment.write_text(live_a.fragment_of(live_a.b), encoding="utf-8")
        before = live_a.snapshot()

        with pytest.raises(UnreconciledError, match="not pruning"):
            prune(live_a.plane)
        assert live_a.snapshot() == before

    def test_refuses_while_unobservable(self, live_a: Host) -> None:
        live_a.caddy.admin_up = False
        with pytest.raises(UnobservableError):
            prune(live_a.plane)

    def test_nothing_live_keeps_nothing_special(self, host: Host) -> None:
        report = prune(host.plane)

        assert set(report.removed) == {host.a, host.b}
        assert report.retained == ()
        assert live_release(host.plane) is None
