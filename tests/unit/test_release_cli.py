"""``lovspor release …`` and ``publish-check`` on an envelope (ADR-0014 Decision 6).

The operator's route: every state the library exposes must be reachable
through the commands the wrapper script and the runbook name, with the
exit codes the unit reports through. The control plane is the Caddy host
in a box; ``discover_checkout`` is environment discovery, monkeypatched to
the throwaway checkout as the site CLI tests do.
"""

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import click
import pytest
import typer
from pytest_httpx import HTTPXMock
from typer.main import get_command
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.release import commands
from lovspor.release.caddy import HttpxAdminClient, SubprocessRunner
from lovspor.release.commands import (
    MigrateFlags,
    Run,
    _migrate_action,
    _migrate_lines,
    _retire,
)
from lovspor.release.control import ControlPlane
from lovspor.release.envelope import FRAGMENT_NAME, Marker, read_fragment, read_marker, write_marker
from lovspor.release.errors import ReleaseError
from lovspor.release.migrate import first_migration
from tests.unit.caddy_fakes import FakeCaddy
from tests.unit.migrate_fixtures import Droplet, make_droplet
from tests.unit.probe_fixtures import (
    MCP_URL,
    READINESS_URL,
    TOKEN,
    FakeMcp,
    absent_discovery,
    install,
    ready,
    tools_listing,
)
from tests.unit.release_fixtures import World, build, make_world, observer, rename_document

runner = CliRunner()
LATER = "2026-01-02T00:00:00Z"
_AN_ID = "a" * 64
"""A well-formed release_content_id; the runs that refuse never look it up."""
PLACEHOLDER = "handle {\n\troot * /var/www/lovspor\n\tfile_server\n}\n"
_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("content_id", "flags", "message"),
    [
        (
            None,
            commands.MigrateFlags(rollback=True, retire=True),
            "--rollback and --retire exclude each other",
        ),
        (
            "a" * 64,
            commands.MigrateFlags(rollback=True),
            "--rollback and --retire take no release_content_id",
        ),
        (
            None,
            commands.MigrateFlags(check=True, rollback=True),
            "--check is the migration's preflight; it excludes --rollback and --retire",
        ),
        (
            None,
            commands.MigrateFlags(offline=True),
            "--offline is the rollback's last resort; it needs --rollback",
        ),
        (None, commands.MigrateFlags(yes=True), "--yes confirms --retire; no other run asks"),
    ],
)
def test_migrate_flag_conflicts_have_stable_operator_messages(
    content_id: str | None, flags: commands.MigrateFlags, message: str
) -> None:
    with pytest.raises(typer.BadParameter) as caught:
        commands._migrate_action(content_id, flags)
    assert str(caught.value) == message


def test_no_migration_flags_selects_migrate() -> None:
    assert commands._migrate_action("a" * 64, commands.MigrateFlags()) == "migrate"


def test_migrate_run_without_an_id_has_a_stable_error() -> None:
    with pytest.raises(typer.BadParameter) as caught:
        commands._migrate_lines(None, None, commands.Run("migrate"))  # type: ignore[arg-type]
    assert str(caught.value) == "migrate needs a release_content_id, --rollback or --retire"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def envelopes(world: World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    releases = tmp_path_factory.mktemp("source") / "releases"
    a = build(world, releases).release_content_id
    rename_document(world)
    b = build(world, releases, observe=observer(LATER)).release_content_id
    return releases, a, b


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(rendered: str) -> str:
    """rich output collapsed to its words: no escapes, no box drawing, one space."""
    text = _ANSI.sub("", rendered)
    text = re.sub(r"[\u2500-\u257f]", " ", text)
    return " ".join(text.split())


class Host(NamedTuple):
    plane: ControlPlane
    caddy: FakeCaddy
    a: str
    b: str

    def make_live(self, content_id: str, previous: str | None = None) -> None:
        fragment = read_fragment(self.plane.releases / content_id)
        self.plane.fragment.write_text(fragment, encoding="utf-8")
        self.caddy.restart()
        write_marker(self.plane.releases, Marker(active=content_id, previous=previous))


@pytest.fixture
def host(envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    """The plane the commands build is replaced by one over the fake; the paths are real."""
    source, a, b = envelopes
    releases = tmp_path / "releases"
    shutil.copytree(source, releases)
    etc = tmp_path / "etc"
    etc.mkdir()
    fragment = etc / "lovspor-release.caddy"
    caddyfile = etc / "Caddyfile"
    caddyfile.write_text(
        f"lovspor.test {{\n\timport {{$LOVSPOR_RELEASE_FRAGMENT:{fragment}}}\n}}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LOVSPOR_RELEASE_FRAGMENT", raising=False)
    fragment.write_text(PLACEHOLDER, encoding="utf-8")
    caddy = FakeCaddy(caddyfile)
    caddy.restart()
    plane = ControlPlane(releases, caddyfile, fragment, runner=caddy, admin=caddy)
    monkeypatch.setattr(commands, "_plane", lambda *_: plane)
    for name, value in (
        ("LOVSPOR_RELEASES_ROOT", releases),
        ("LOVSPOR_CADDYFILE", caddyfile),
        ("LOVSPOR_CADDY_ADMIN", "unix//nowhere.sock"),
    ):
        monkeypatch.setenv(name, str(value))
    return Host(plane, caddy, a, b)


class TestLive:
    def test_prints_none_when_nothing_is_live(self, host: Host) -> None:
        result = runner.invoke(app, ["release", "live"])

        assert result.exit_code == 0, result.output
        assert result.stdout == "none\n"

    def test_prints_the_reconciled_id(self, host: Host) -> None:
        host.make_live(host.a)

        result = runner.invoke(app, ["release", "live"])

        assert result.exit_code == 0, result.output
        assert result.stdout == f"{host.a}\n"

    def test_unreconciled_exits_one_with_the_triple(self, host: Host) -> None:
        host.make_live(host.a)
        host.plane.fragment.write_text(read_fragment(host.plane.releases / host.b))

        result = runner.invoke(app, ["release", "live"])

        assert result.exit_code == 1
        assert "release refused: host is staged_not_reloaded" in result.output
        assert f"R=({host.a}," in result.output and f"D=({host.b}," in result.output

    def test_admin_unreachable_exits_three_naming_the_precondition(self, host: Host) -> None:
        host.make_live(host.a)
        host.caddy.admin_up = False

        result = runner.invoke(app, ["release", "live"])

        assert result.exit_code == 3
        assert "precondition Caddy admin reachable unmet" in result.output
        assert f"D=({host.a}," in result.output


class TestCommit:
    def test_makes_the_release_live(self, host: Host) -> None:
        host.make_live(host.a)

        result = runner.invoke(app, ["release", "commit", host.b])

        assert result.exit_code == 0, result.output
        assert result.stdout == f"live: {host.b} (previous {host.a[:12]})\n"
        assert read_marker(host.plane.releases) == Marker(active=host.b, previous=host.a)
        assert host.caddy.reloads == 1

    def test_the_live_release_is_nothing_to_commit(self, host: Host) -> None:
        host.make_live(host.a)

        result = runner.invoke(app, ["release", "commit", host.a])

        assert result.exit_code == 0, result.output
        assert "already live; nothing to commit" in result.stdout
        assert host.caddy.reloads == 0

    def test_a_reload_failure_exits_one_after_the_revert(self, host: Host) -> None:
        host.make_live(host.a)
        host.caddy.fail_reloads = 1

        result = runner.invoke(app, ["release", "commit", host.b])

        assert result.exit_code == 1
        assert "not switched" in result.output
        assert read_marker(host.plane.releases) == Marker(active=host.a, previous=None)

    def test_a_name_that_is_not_an_id_is_a_usage_error(self, host: Host) -> None:
        result = runner.invoke(app, ["release", "commit", ".build-x"])

        assert result.exit_code == 2


class TestReconcileRollbackPrune:
    def test_reconcile_reports_a_reconciled_host(self, host: Host) -> None:
        host.make_live(host.a)

        result = runner.invoke(app, ["release", "reconcile"])

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("reconciled: R=(")
        assert result.stdout.endswith(f"admin: unix//nowhere.sock\nlive: {host.a}\n")

    def test_reconcile_names_the_options_and_completes_on_the_flag(self, host: Host) -> None:
        host.make_live(host.a)
        host.plane.fragment.write_text(read_fragment(host.plane.releases / host.b))

        reported = runner.invoke(app, ["release", "reconcile"])
        completed = runner.invoke(app, ["release", "reconcile", "--complete"])

        assert reported.exit_code == 1
        assert "--complete" in reported.output and "--abandon" in reported.output
        assert completed.exit_code == 0, completed.output
        assert "action completed" in completed.stdout
        assert read_marker(host.plane.releases) == Marker(active=host.b, previous=host.a)

    def test_reconcile_abandon_restores_the_marker_release(self, host: Host) -> None:
        host.make_live(host.a)
        host.plane.fragment.write_text(read_fragment(host.plane.releases / host.b))

        result = runner.invoke(app, ["release", "reconcile", "--abandon"])

        assert result.exit_code == 0, result.output
        assert "action abandoned" in result.stdout
        assert host.plane.fragment.read_text() == read_fragment(host.plane.releases / host.a)

    def test_both_flags_are_a_usage_error(self, host: Host) -> None:
        result = runner.invoke(app, ["release", "reconcile", "--complete", "--abandon"])

        assert result.exit_code == 2

    def test_rollback_and_prune(self, host: Host) -> None:
        host.make_live(host.a)
        runner.invoke(app, ["release", "commit", host.b])
        stale = host.plane.releases / ".build-stale"
        stale.mkdir()

        rolled = runner.invoke(app, ["release", "rollback"])
        pruned = runner.invoke(app, ["release", "prune"])

        assert rolled.exit_code == 0, rolled.output
        assert rolled.stdout == f"live: {host.a} (previous {host.b})\n"
        assert pruned.exit_code == 0, pruned.output
        # The report sorts the retained ids; which of a/b sorts first depends on
        # their content hashes, which differ per toolchain.
        retained = ", ".join(name[:12] for name in sorted((host.a, host.b)))
        assert pruned.stdout == f"pruned 1: .build-stale; retained {retained}\n"
        assert not stale.exists()

    def test_rollback_without_a_previous_release_exits_one(self, host: Host) -> None:
        host.make_live(host.a)

        result = runner.invoke(app, ["release", "rollback"])

        assert result.exit_code == 1
        assert "no previous release" in result.output


@pytest.fixture
def droplet(
    envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Droplet:
    """The pre-envelope box, with both factories the commands use replaced."""
    found = make_droplet(envelopes, tmp_path, monkeypatch)
    monkeypatch.setattr(commands, "_plane", lambda *_: found.plane)
    monkeypatch.setattr(commands, "_host", lambda *_: found.host)
    monkeypatch.setenv("LOVSPOR_CADDY_ADMIN", found.host.socket_admin)
    return found


class Killed(Exception):  # noqa: N818 — a simulated process death, not a lovspor error
    """The process died right after the named step."""


class TestReconcileWindow:
    def _staged_on_tcp(self, droplet: Droplet) -> None:
        def kill(step: str) -> None:
            if step == "installed":
                raise Killed(step)

        with pytest.raises(Killed):
            first_migration(droplet.plane, droplet.host, droplet.a, kill)

    def test_a_crash_after_the_install_is_named_with_the_two_options(
        self, droplet: Droplet
    ) -> None:
        self._staged_on_tcp(droplet)

        reported = runner.invoke(app, ["release", "reconcile"])

        assert reported.exit_code == 1
        assert "release refused: host is staged_not_reloaded" in reported.output
        assert "on localhost:2019" in reported.output
        assert "--complete" in reported.output and "--abandon" in reported.output

    def test_complete_runs_the_cutover_and_prints_the_socket(self, droplet: Droplet) -> None:
        self._staged_on_tcp(droplet)

        completed = runner.invoke(app, ["release", "reconcile", "--complete"])

        assert completed.exit_code == 0, completed.output
        lines = completed.stdout.splitlines()
        assert lines[0].startswith("reconciled: R=(none, ") and lines[0].endswith(
            "action completed"
        )
        assert lines[1:] == [f"admin: {droplet.host.socket_admin}", f"live: {droplet.a}"]
        assert read_marker(droplet.plane.releases) == Marker(active=droplet.a, previous=None)
        assert droplet.caddy.admin_address == droplet.host.socket_admin

    def test_abandon_restores_the_files_and_prints_tcp(self, droplet: Droplet) -> None:
        self._staged_on_tcp(droplet)

        abandoned = runner.invoke(app, ["release", "reconcile", "--abandon"])

        assert abandoned.exit_code == 0, abandoned.output
        assert abandoned.stdout.endswith("action abandoned\nadmin: localhost:2019\nlive: none\n")
        assert not droplet.plane.fragment.exists()
        assert droplet.caddy.reloads == 0

    def test_the_host_options_are_registered_with_their_environment(self) -> None:
        root = get_command(app)
        assert isinstance(root, click.Group)
        group = root.commands["release"]
        assert isinstance(group, click.Group)
        params = {param.name: param for param in group.commands["reconcile"].params}

        assert isinstance(params["tcp_admin"], click.Option)
        assert params["tcp_admin"].envvar == "LOVSPOR_CADDY_ADMIN_TCP"
        assert params["tcp_admin"].default == "localhost:2019"
        assert params["caddyfile_source"].envvar == "LOVSPOR_CADDYFILE_SOURCE"
        assert params["drop_in"].envvar == "LOVSPOR_CADDY_DROP_IN"
        assert params["runtime_dir"].envvar == "LOVSPOR_CADDY_RUNTIME_DIR"
        assert params["release_group"].envvar == "LOVSPOR_RELEASE_GROUP"
        assert params["release_group"].default == "lovspor-release"

    def test_the_production_host_factory_binds_the_socket_to_the_admin_option(
        self, tmp_path: Path
    ) -> None:
        options = commands.HostOptions(
            tcp_admin="localhost:2029",
            caddyfile_source=tmp_path / "src",
            drop_in=tmp_path / "drop",
            runtime_dir=tmp_path / "run",
            release_group="g",
            site_root=tmp_path / "site",
            current_symlink=tmp_path / "current",
        )

        host = commands._host(tmp_path / "Caddyfile", "unix//run/x/admin.sock", options)

        assert host.caddyfile == tmp_path / "Caddyfile"
        assert host.socket_admin == "unix//run/x/admin.sock"
        assert host.tcp_admin == "localhost:2029"
        assert host.caddyfile_source == tmp_path / "src"
        assert host.drop_in == tmp_path / "drop"
        assert host.runtime_dir == tmp_path / "run"
        assert host.release_group == "g"
        assert (host.site_root, host.current_symlink) == (tmp_path / "site", tmp_path / "current")
        assert commands.HostOptions() == commands.HostOptions(
            "localhost:2019",
            Path("/opt/lovspor/app/deploy/digitalocean/Caddyfile"),
            Path("/etc/systemd/system/caddy.service.d/lovspor.conf"),
            Path("/run/caddy"),
            "lovspor-release",
            Path("/var/www/lovspor"),
            Path("/var/www/lovspor-current"),
        )


class TestMigrate:
    """``lovspor release migrate``: the cutover, its preflight, its rollback, the retire step.

    Four runs, never combined. ``--retire`` in particular is its own
    command and never a phase of the migration (decision F2): it deletes
    the previous Caddyfile's whole world, which is the only way back.
    """

    def _staged(self, droplet: Droplet) -> None:
        """The migration killed after (a): D is the new configuration, R still old on TCP."""

        def kill(step: str) -> None:
            if step == "installed":
                raise Killed(step)

        with pytest.raises(Killed):
            first_migration(droplet.plane, droplet.host, droplet.a, kill)

    def test_cuts_over_and_reports_the_release_the_socket_and_the_backup(
        self, droplet: Droplet
    ) -> None:
        result = runner.invoke(app, ["release", "migrate", droplet.a])

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            f"migrated: {droplet.a} (admin {droplet.host.socket_admin})",
            f"running: {droplet.pair_of(droplet.a).describe()}",
            f"previous Caddyfile: {droplet.host.previous_caddyfile}",
        ]
        assert read_marker(droplet.plane.releases) == Marker(active=droplet.a, previous=None)
        assert droplet.caddy.admin_address == droplet.host.socket_admin

    def test_check_names_the_release_and_moves_nothing(self, droplet: Droplet) -> None:
        result = runner.invoke(app, ["release", "migrate", "--check", droplet.a])

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith(f"preflight: release {droplet.a}; R=(none, ")
        assert result.stdout.endswith(
            f" on localhost:2019; socket {droplet.host.socket_admin} absent; "
            f"group lovspor-release (gid {droplet.ownership.groups['lovspor-release']}); "
            "Caddy accepts |0660\n"
        )
        assert not droplet.plane.fragment.exists()
        assert read_marker(droplet.plane.releases) is None
        assert droplet.caddy.admin_address == "localhost:2019"

    def test_check_without_a_release_names_none(self, droplet: Droplet) -> None:
        result = runner.invoke(app, ["release", "migrate", "--check"])

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("preflight: no release named; R=(none, ")

    def test_a_refused_precondition_exits_one_and_names_it(self, droplet: Droplet) -> None:
        write_marker(droplet.plane.releases, Marker(active=droplet.b, previous=None))

        result = runner.invoke(app, ["release", "migrate", droplet.a])

        assert result.exit_code == 1
        assert (
            f"release refused: a marker exists (active {droplet.b}); "
            "the first migration has already happened"
        ) in result.output.splitlines()
        assert not droplet.plane.fragment.exists()

    def test_tcp_unreachable_exits_three_naming_the_precondition(self, droplet: Droplet) -> None:
        droplet.caddy.admin_up = False

        result = runner.invoke(app, ["release", "migrate", "--check"])

        assert result.exit_code == 3
        assert "precondition Caddy admin reachable unmet" in result.output
        assert "the first migration needs Caddy answering on localhost:2019" in result.output

    def test_rollback_after_the_cutover_returns_the_host_to_tcp(self, droplet: Droplet) -> None:
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0

        result = runner.invoke(app, ["release", "migrate", "--rollback"])

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            f"rolled back from {droplet.host.socket_admin} to localhost:2019; reloaded yes",
            "marker removed yes, ExecReload pair removed yes",
        ]
        assert droplet.caddy.admin_address == "localhost:2019"
        assert read_marker(droplet.plane.releases) is None

    def test_rollback_before_the_cutover_restores_the_files_without_a_reload(
        self, droplet: Droplet
    ) -> None:
        self._staged(droplet)

        result = runner.invoke(app, ["release", "migrate", "--rollback"])

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            "rolled back from localhost:2019 to localhost:2019; reloaded no",
            "marker removed no, ExecReload pair removed no",
        ]
        assert droplet.caddy.reloads == 0
        assert not droplet.plane.fragment.exists()

    def test_offline_rollback_restores_the_files_with_caddy_dead(self, droplet: Droplet) -> None:
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0
        droplet.caddy.admin_up = False

        result = runner.invoke(app, ["release", "migrate", "--rollback", "--offline"])

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            "rolled back from offline to localhost:2019; reloaded no",
            "marker removed yes, ExecReload pair removed yes",
            "restarted caddy",
        ]
        assert read_marker(droplet.plane.releases) is None
        assert droplet.caddy.restarts == 1

    def test_the_dialling_rollback_exits_three_when_nothing_answers(self, droplet: Droplet) -> None:
        """The state `--offline` exists for: the ordinary rollback reads the
        running configuration first and refuses in the one moment the operator
        has no other way back."""
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0
        droplet.caddy.admin_up = False

        result = runner.invoke(app, ["release", "migrate", "--rollback"])

        assert result.exit_code == 3
        assert "precondition Caddy admin reachable unmet" in result.output
        assert droplet.host.previous_caddyfile.is_file()

    def _litter(self, droplet: Droplet) -> Path:
        droplet.host.site_root.mkdir(parents=True)
        flat = droplet.releases / "20260908T120000Z-abcdef123456"
        flat.mkdir()
        droplet.host.current_symlink.symlink_to(flat)
        return flat

    def test_retire_without_yes_lists_every_path_and_removes_none(self, droplet: Droplet) -> None:
        """`--retire` deletes production directories and the rollback's only
        source. The confirmation is a flag, never a prompt: a non-tty run — the
        wrapper under systemd, an ssh one-liner — must fail closed, not read a
        yes off a pipe that is not there."""
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0
        flat = self._litter(droplet)

        result = runner.invoke(app, ["release", "migrate", "--retire"])

        assert result.exit_code == 1
        for path in (
            droplet.host.current_symlink,
            droplet.host.site_root,
            flat,
            droplet.host.previous_drop_in,
            droplet.host.previous_caddyfile,
        ):
            assert f"  {path}" in result.output, path
        assert "re-run with --yes to confirm" in result.output
        assert droplet.host.current_symlink.is_symlink()
        assert droplet.host.site_root.is_dir()
        assert flat.is_dir()
        assert droplet.host.previous_caddyfile.is_file()
        assert read_marker(droplet.plane.releases) is not None

    def test_retire_with_nothing_left_still_asks(self, droplet: Droplet) -> None:
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0
        assert runner.invoke(app, ["release", "migrate", "--retire", "--yes"]).exit_code == 0

        result = runner.invoke(app, ["release", "migrate", "--retire"])

        assert result.exit_code == 1
        assert "(nothing)" in result.output
        assert "re-run with --yes to confirm" in result.output

    def test_retire_removes_the_pre_envelope_layout(self, droplet: Droplet) -> None:
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0
        flat = self._litter(droplet)

        result = runner.invoke(app, ["release", "migrate", "--retire", "--yes"])

        assert result.exit_code == 0, result.output
        assert result.stdout == (
            f"retired 5: {droplet.host.current_symlink}, {droplet.host.site_root}, {flat}, "
            f"{droplet.host.previous_drop_in}, {droplet.host.previous_caddyfile}\n"
        )
        assert not droplet.host.current_symlink.is_symlink()
        assert not droplet.host.site_root.exists()
        assert not flat.exists()
        assert not droplet.host.previous_drop_in.exists()
        assert not droplet.host.previous_caddyfile.exists()

    def test_retire_with_the_trees_gone_still_removes_the_way_back(self, droplet: Droplet) -> None:
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0

        first = runner.invoke(app, ["release", "migrate", "--retire", "--yes"])
        second = runner.invoke(app, ["release", "migrate", "--retire", "--yes"])

        assert first.exit_code == 0, first.output
        assert first.stdout == (
            f"retired 2: {droplet.host.previous_drop_in}, {droplet.host.previous_caddyfile}\n"
        )
        assert second.exit_code == 0, second.output
        assert second.stdout == "retired 0: -\n"

    def test_retire_before_the_marker_exits_one(self, droplet: Droplet) -> None:
        confirmed = runner.invoke(app, ["release", "migrate", "--retire", "--yes"])
        asked = runner.invoke(app, ["release", "migrate", "--retire"])

        for result in (confirmed, asked):
            assert result.exit_code == 1
            assert (
                "release refused: no marker: the first migration has not finished"
            ) in result.output

    @pytest.mark.parametrize(
        ("args", "phrase"),
        [
            (["--rollback", "--retire"], "--rollback and --retire exclude each other"),
            (["--rollback", "ID"], "--rollback and --retire take no release_content_id"),
            (["--retire", "ID"], "--rollback and --retire take no release_content_id"),
            (["--check", "--rollback"], "--check is the migration's preflight"),
            ([".build-x"], "not a release_content_id: .build-x"),
            (["--offline"], "--offline is the rollback's last resort"),
            (["--offline", "--retire"], "--offline is the rollback's last resort"),
            (["--offline", "--check"], "--offline is the rollback's last resort"),
            (["--yes"], "--yes confirms --retire"),
            (["--yes", "--rollback"], "--yes confirms --retire"),
            ([], "migrate needs a release_content_id"),
        ],
    )
    def test_the_runs_exclude_each_other(
        self, droplet: Droplet, args: list[str], phrase: str
    ) -> None:
        given = [droplet.a if arg == "ID" else arg for arg in args]

        result = runner.invoke(app, ["release", "migrate", *given])

        assert result.exit_code == 2
        # rich renders the usage error as a panel wrapped at the runner's
        # width; compare the words, not the rendering.
        assert phrase in _plain(result.output)
        assert read_marker(droplet.plane.releases) is None
        assert not droplet.plane.fragment.exists()

    def test_the_options_are_registered_with_their_environment(self) -> None:
        root = get_command(app)
        assert isinstance(root, click.Group)
        group = root.commands["release"]
        assert isinstance(group, click.Group)
        params = {param.name: param for param in group.commands["migrate"].params}

        assert isinstance(params["content_id"], click.Argument)
        assert not params["content_id"].required
        for flag in ("rollback", "retire", "check", "offline", "yes"):
            assert isinstance(params[flag], click.Option)
            assert params[flag].is_flag, flag
        for name, envvar, default in (
            ("releases", "LOVSPOR_RELEASES_ROOT", Path("/var/www/lovspor-releases")),
            ("caddyfile", "LOVSPOR_CADDYFILE", Path("/etc/caddy/Caddyfile")),
            ("fragment", "LOVSPOR_RELEASE_FRAGMENT", Path("/etc/caddy/lovspor-release.caddy")),
            ("admin", "LOVSPOR_CADDY_ADMIN", "unix//run/caddy/admin.sock"),
            ("tcp_admin", "LOVSPOR_CADDY_ADMIN_TCP", "localhost:2019"),
            (
                "caddyfile_source",
                "LOVSPOR_CADDYFILE_SOURCE",
                Path("/opt/lovspor/app/deploy/digitalocean/Caddyfile"),
            ),
            (
                "drop_in",
                "LOVSPOR_CADDY_DROP_IN",
                Path("/etc/systemd/system/caddy.service.d/lovspor.conf"),
            ),
            ("runtime_dir", "LOVSPOR_CADDY_RUNTIME_DIR", Path("/run/caddy")),
            ("release_group", "LOVSPOR_RELEASE_GROUP", "lovspor-release"),
        ):
            assert params[name].envvar == envvar, name
            assert params[name].default == default, name


class TestMigrateRunSelection:
    """``_migrate_action`` and ``_retire`` read as text, not through rich's panel.

    ``test_the_runs_exclude_each_other`` above asserts a *phrase*, because
    the runner wraps a ``BadParameter`` panel at its own width. The refusal
    the operator acts on is the whole sentence, and the retire listing is a
    path per line, so both are compared whole here — one layer below the
    renderer, where the text is the code's and not the terminal's.
    """

    @pytest.mark.parametrize(
        ("content_id", "flags", "message"),
        [
            (
                None,
                MigrateFlags(rollback=True, retire=True),
                "--rollback and --retire exclude each other",
            ),
            (
                _AN_ID,
                MigrateFlags(rollback=True),
                "--rollback and --retire take no release_content_id",
            ),
            (
                _AN_ID,
                MigrateFlags(retire=True),
                "--rollback and --retire take no release_content_id",
            ),
            (
                None,
                MigrateFlags(rollback=True, check=True),
                "--check is the migration's preflight; it excludes --rollback and --retire",
            ),
            (
                None,
                MigrateFlags(retire=True, check=True),
                "--check is the migration's preflight; it excludes --rollback and --retire",
            ),
            (
                None,
                MigrateFlags(offline=True),
                "--offline is the rollback's last resort; it needs --rollback",
            ),
            (
                None,
                MigrateFlags(retire=True, offline=True),
                "--offline is the rollback's last resort; it needs --rollback",
            ),
            (None, MigrateFlags(yes=True), "--yes confirms --retire; no other run asks"),
            (
                None,
                MigrateFlags(rollback=True, yes=True),
                "--yes confirms --retire; no other run asks",
            ),
            (".build-x", MigrateFlags(), "not a release_content_id: .build-x"),
        ],
    )
    def test_every_excluded_combination_names_itself_whole(
        self, content_id: str | None, flags: MigrateFlags, message: str
    ) -> None:
        with pytest.raises(click.BadParameter) as caught:
            _migrate_action(content_id, flags)

        assert str(caught.value) == message

    @pytest.mark.parametrize(
        ("content_id", "flags", "action"),
        [
            (_AN_ID, MigrateFlags(), "migrate"),
            (None, MigrateFlags(), "migrate"),
            (_AN_ID, MigrateFlags(check=True), "check"),
            (None, MigrateFlags(check=True), "check"),
            (None, MigrateFlags(rollback=True), "rollback"),
            (None, MigrateFlags(rollback=True, offline=True), "offline"),
            (None, MigrateFlags(retire=True), "retire"),
            (None, MigrateFlags(retire=True, yes=True), "retire"),
        ],
    )
    def test_the_accepted_combinations_name_their_run(
        self, content_id: str | None, flags: MigrateFlags, action: str
    ) -> None:
        assert _migrate_action(content_id, flags) == action

    def test_a_migrate_run_without_an_id_says_so_whole(self, droplet: Droplet) -> None:
        with pytest.raises(click.BadParameter) as caught:
            _migrate_lines(droplet.plane, droplet.host, Run("migrate", None, False))

        assert str(caught.value) == "migrate needs a release_content_id, --rollback or --retire"

    def test_the_unconfirmed_retire_lists_one_path_per_line(self, droplet: Droplet) -> None:
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0
        droplet.host.site_root.mkdir(parents=True)

        with pytest.raises(ReleaseError) as caught:
            _retire(droplet.plane, droplet.host, confirmed=False)

        assert str(caught.value) == (
            "--retire permanently removes these paths, the last of them the only way back to "
            f"the pre-envelope site:\n  {droplet.host.site_root}\n"
            f"  {droplet.host.previous_drop_in}\n"
            f"  {droplet.host.previous_caddyfile}\nre-run with --yes to confirm"
        )
        assert droplet.host.site_root.is_dir()
        assert droplet.host.previous_caddyfile.is_file()

    def test_the_unconfirmed_retire_with_nothing_left_names_nothing(self, droplet: Droplet) -> None:
        assert runner.invoke(app, ["release", "migrate", droplet.a]).exit_code == 0
        droplet.host.previous_drop_in.unlink()
        droplet.host.previous_caddyfile.unlink()

        with pytest.raises(ReleaseError) as caught:
            _retire(droplet.plane, droplet.host, confirmed=False)

        assert str(caught.value) == (
            "--retire permanently removes these paths, the last of them the only way back to "
            "the pre-envelope site:\n  (nothing)\nre-run with --yes to confirm"
        )


@pytest.fixture
def checkout(world: World, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(commands, "discover_checkout", lambda: world.checkout)
    return world.checkout


def _fake_host(httpx_mock: HTTPXMock) -> FakeMcp:
    ready(httpx_mock)
    absent_discovery(httpx_mock)
    return install(httpx_mock, FakeMcp(tools_listing(("x", "y"))))


def _build_args(world: World, releases: Path, live: str, token: Path | None) -> list[str]:
    args = [
        "release",
        "build",
        "--corpus",
        str(world.corpus),
        "--releases",
        str(releases),
        "--live",
        live,
        "--readiness-url",
        READINESS_URL,
        "--public-mcp-url",
        MCP_URL,
    ]
    if token is not None:
        args += ["--probe-token-file", str(token)]
    return args


class TestBuild:
    def test_builds_and_prints_the_id_alone_on_stdout(
        self, world: World, checkout: Path, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        fake = _fake_host(httpx_mock)
        token = tmp_path / "site-probe"
        token.write_text(TOKEN + "\n", encoding="utf-8")
        releases = tmp_path / "releases"

        result = runner.invoke(app, _build_args(world, releases, "none", token))

        assert result.exit_code == 0, result.output
        content_id = result.stdout.strip()
        assert re.fullmatch(r"[0-9a-f]{64}", content_id)
        assert (releases / content_id / FRAGMENT_NAME).is_file()
        assert f"release {content_id[:12]}: built and finalized" in result.stderr.splitlines()
        assert fake.methods()[-1] == "tools/list"
        assert TOKEN not in result.output
        document = (releases / content_id / "site" / "deployment-capabilities.json").read_text()
        assert '"observer": "release-probe"' in document

    def test_the_same_release_is_reused_and_the_live_one_is_already_live(
        self,
        world: World,
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The wrapper reads the id from stdout either way; stderr says which case it was.

        The command's clock is the one input the id depends on that a second
        run would not repeat, so it is held still."""
        monkeypatch.setattr(commands, "_clock", lambda: datetime(2026, 1, 1, tzinfo=UTC))
        _fake_host(httpx_mock)
        releases = tmp_path / "releases"
        first = runner.invoke(app, _build_args(world, releases, "none", None))
        assert first.exit_code == 0, first.output
        content_id = first.stdout.strip()

        ready(httpx_mock)
        absent_discovery(httpx_mock)
        again = runner.invoke(app, _build_args(world, releases, "none", None))
        write_marker(releases, Marker(active=content_id, previous=None))
        ready(httpx_mock)
        absent_discovery(httpx_mock)
        live = runner.invoke(app, _build_args(world, releases, content_id, None))

        assert again.exit_code == 0, again.output
        assert again.stdout == f"{content_id}\n"
        assert f"release {content_id[:12]}: reused, already on disk" in again.stderr.splitlines()
        assert live.exit_code == 0, live.output
        assert live.stdout == f"{content_id}\n"
        assert f"release {content_id[:12]}: already live" in live.stderr.splitlines()
        assert {path.name for path in releases.iterdir()} == {"ACTIVE", content_id}

    def test_a_missing_credential_is_recorded_not_fatal(
        self, world: World, checkout: Path, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        _fake_host(httpx_mock)
        releases = tmp_path / "releases"

        result = runner.invoke(app, _build_args(world, releases, "none", tmp_path / "absent"))

        assert result.exit_code == 0, result.output
        assert "probe credential unreadable" in result.stderr
        content_id = result.stdout.strip()
        document = (releases / content_id / "site" / "deployment-capabilities.json").read_text()
        assert "probe_credential_missing" in document

    def test_live_must_agree_with_the_marker(
        self, world: World, checkout: Path, tmp_path: Path
    ) -> None:
        releases = tmp_path / "releases"
        releases.mkdir()
        write_marker(releases, Marker(active="a" * 64, previous=None))

        result = runner.invoke(app, _build_args(world, releases, "none", None))

        assert result.exit_code == 1
        assert (
            f"release refused: --live names none, the marker names {'a' * 64}; "
            "establish the live release with `lovspor release live` first"
        ) in result.output.splitlines()
        assert not any(releases.glob(".build-*"))

    def test_live_must_be_an_id_or_none(self, world: World, tmp_path: Path) -> None:
        result = runner.invoke(app, _build_args(world, tmp_path, "latest", None))

        assert result.exit_code == 2
        # rich renders the usage error as an ANSI-styled panel wrapped at the
        # terminal width; compare the words, not the rendering.
        assert "--live must be a release_content_id or 'none'" in _plain(result.output)


class TestPublishCheck:
    def test_checks_an_envelope(self, envelopes: tuple[Path, str, str]) -> None:
        releases, a, _ = envelopes

        result = runner.invoke(app, ["publish-check", str(releases / a)])

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith(f"envelope ok: release {a[:12]}, release ok: corpus")

    def test_still_checks_a_bare_corpus_tree(self, envelopes: tuple[Path, str, str]) -> None:
        releases, a, _ = envelopes

        result = runner.invoke(app, ["publish-check", str(releases / a / "corpus")])

        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("release ok: corpus")

    def test_a_broken_envelope_exits_one_naming_the_defect(
        self, envelopes: tuple[Path, str, str], tmp_path: Path
    ) -> None:
        releases, a, _ = envelopes
        copy = tmp_path / ".build-copy"
        shutil.copytree(releases / a, copy)
        shutil.rmtree(copy / "site")

        result = runner.invoke(app, ["publish-check", str(copy)])

        assert result.exit_code == 1
        assert "release refused: .build-copy: missing site/" in result.output


class TestPlane:
    def test_the_real_plane_wires_the_subprocess_runner_and_the_admin_address(
        self, tmp_path: Path
    ) -> None:
        """The CLI's one production factory: the three paths as given, the real
        Runner, and an admin client bound to exactly the address the wrapper
        passes (the socket in steady state, TCP during the migration). Every
        CLI test replaces it, so it is pinned here without any I/O."""
        releases, caddyfile, fragment = tmp_path / "r", tmp_path / "Caddyfile", tmp_path / "f"

        plane = commands._plane(releases, caddyfile, fragment, "unix//run/caddy/admin.sock")

        assert isinstance(plane, ControlPlane)
        assert (plane.releases, plane.caddyfile, plane.fragment) == (releases, caddyfile, fragment)
        assert isinstance(plane.runner, SubprocessRunner)
        assert isinstance(plane.admin, HttpxAdminClient)
        assert plane.admin.address == "unix//run/caddy/admin.sock"

        tcp = commands._plane(releases, caddyfile, fragment, "localhost:2019")
        assert isinstance(tcp.admin, HttpxAdminClient)
        assert tcp.admin.address == "localhost:2019"


class TestPackage:
    def test_the_release_group_is_registered(self) -> None:
        root = get_command(app)
        assert isinstance(root, click.Group)
        group = root.commands["release"]
        assert isinstance(group, click.Group)

        assert {
            "build",
            "live",
            "commit",
            "reconcile",
            "rollback",
            "prune",
            "migrate",
        } <= set(group.commands)

    def test_only_the_command_layer_reads_a_clock(self) -> None:
        clock = re.compile(
            r"\b(?:now|utcnow|today|time\.time|monotonic|perf_counter|fromtimestamp)\("
        )
        package = _REPO / "src" / "lovspor" / "release"
        offenders: list[str] = [
            path.name
            for path in sorted(package.glob("*.py"))
            if clock.search(path.read_text(encoding="utf-8"))
        ]

        assert offenders == ["commands.py"]

    def test_no_module_of_the_package_runs_a_shell(self) -> None:
        package = _REPO / "src" / "lovspor" / "release"
        for path in package.glob("*.py"):
            assert "shell=True" not in path.read_text(encoding="utf-8"), path.name
