"""The first migration: preflight, cutover, crash rows, rollback, retire (ADR-0014 Migration).

The host is the *droplet* of ``migrate_fixtures`` — the pre-envelope box
as the migration finds it; two real envelopes are built once. Kills are
checkpoints that stop the procedure at a named step, as the control-plane
tests do.
"""

import grp
import json
import os
import pwd
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.release import migrate
from lovspor.release.caddy import FRAGMENT_ENV, Completed, adapt, config_pair
from lovspor.release.caddy import validate as validate_caddy
from lovspor.release.control import (
    Checkpoint,
    ControlPlane,
    Situation,
    live_release,
    read_triple,
    situation,
)
from lovspor.release.envelope import (
    FRAGMENT_NAME,
    MARKER_NAME,
    RELEASE_VAR,
    Marker,
    read_marker,
    write_marker,
)
from lovspor.release.errors import (
    CommitRefusedError,
    ControlPlaneError,
    MigrationFailedError,
    MigrationRefusedError,
    ReloadFailedError,
    UnobservableError,
    UnreconciledError,
)
from lovspor.release.migrate import (
    MIGRATION_STEPS,
    OFFLINE_ADMIN,
    PRE_ENVELOPE_DROP_IN,
    MigrationHost,
    MigrationReport,
    Preflight,
    RetireReport,
    RollbackReport,
    SystemOwnership,
    abandon_first_migration,
    complete_first_migration,
    detect_admin,
    drop_in_text,
    first_migration,
    offline_rollback,
    preflight,
    retire_pre_envelope,
    retire_targets,
    rollback_first_migration,
    socket_path,
)
from lovspor.release.reconcile import ReconcileReport, reconcile
from tests.unit.caddy_fakes import (
    ADMIN_DOWN,
    DEFAULT_TCP,
    STOCK_EXEC_RELOAD,
    FakeAdmin,
    FakeCaddy,
    toy_adapt,
)
from tests.unit.migrate_fixtures import (
    CADDY_UID,
    NON_ASCII_OLD_CADDYFILE,
    OLD_CADDYFILE,
    Droplet,
    Sabotaged,
    make_droplet,
    new_caddyfile,
)
from tests.unit.release_fixtures import World, build, make_world, observer, rename_document

LATER = "2026-01-02T00:00:00Z"


class Killed(Exception):  # noqa: N818 — a simulated process death, not a lovspor error
    """The process died right after the named step."""


def _kill_at(step: str) -> Checkpoint:
    def checkpoint(reached: str) -> None:
        if reached == step:
            raise Killed(step)

    return checkpoint


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def envelopes(world: World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    releases = tmp_path_factory.mktemp("source") / "releases"
    a = build(world, releases).release_content_id
    rename_document(world)
    b = build(world, releases, observe=observer(LATER)).release_content_id
    assert a != b
    return releases, a, b


@pytest.fixture
def droplet(
    envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Droplet:
    return make_droplet(envelopes, tmp_path, monkeypatch)


class TestHost:
    def test_the_defaults_are_the_droplets(self) -> None:
        host = MigrationHost()

        assert host.caddyfile == Path("/etc/caddy/Caddyfile")
        assert host.previous_caddyfile == Path("/etc/caddy/Caddyfile.pre-envelope")
        assert host.caddyfile_source == Path("/opt/lovspor/app/deploy/digitalocean/Caddyfile")
        assert host.drop_in == Path("/etc/systemd/system/caddy.service.d/lovspor.conf")
        assert host.runtime_dir == Path("/run/caddy")
        assert (host.tcp_admin, host.socket_admin) == (
            "localhost:2019",
            "unix//run/caddy/admin.sock",
        )
        assert host.socket == Path("/run/caddy/admin.sock")
        assert (host.release_group, host.caddy_user, host.unit) == (
            "lovspor-release",
            "caddy",
            "caddy",
        )
        assert host.site_root == Path("/var/www/lovspor")
        assert host.current_symlink == Path("/var/www/lovspor-current")
        assert host.admin_client("localhost:2019").__class__.__name__ == "HttpxAdminClient"
        assert host.ownership.__class__.__name__ == "SystemOwnership"

    def test_the_socket_admin_must_be_a_unix_address(self) -> None:
        with pytest.raises(ControlPlaneError, match="not a Unix-socket admin address: localhost"):
            MigrationHost(socket_admin="localhost:2019")
        assert socket_path("unix//run/caddy/admin.sock") == Path("/run/caddy/admin.sock")

    def test_the_drop_in_text_is_byte_exact(self, droplet: Droplet) -> None:
        host = droplet.host
        runtime = (
            "# Written by lovspor (ADR-0014 Decision 6): provisioning and the first migration.\n"
            "[Service]\n"
            "EnvironmentFile=/etc/default/caddy-lovspor\n"
            "RuntimeDirectory=caddy\n"
            "RuntimeDirectoryMode=2770\n"
            f"ExecStartPre=+/usr/bin/chgrp lovspor-release {host.runtime_dir}\n"
        )

        assert drop_in_text(host, with_exec_reload=False) == runtime
        assert drop_in_text(host, with_exec_reload=True) == runtime + (
            "ExecReload=\n"
            f"ExecReload=/usr/bin/caddy reload --config {host.caddyfile} --force "
            f"--address {host.socket_admin}\n"
        )
        assert drop_in_text(MigrationHost(), with_exec_reload=True).endswith(
            "ExecStartPre=+/usr/bin/chgrp lovspor-release /run/caddy\n"
            "ExecReload=\n"
            "ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force "
            "--address unix//run/caddy/admin.sock\n"
        )
        assert PRE_ENVELOPE_DROP_IN == "[Service]\nEnvironmentFile=/etc/default/caddy-lovspor\n"


class TestSystemOwnership:
    def test_answers_from_pwd_grp_and_chown(self, tmp_path: Path) -> None:
        uid, gid = os.getuid(), os.getgid()
        try:
            user, group = pwd.getpwuid(uid).pw_name, grp.getgrgid(gid).gr_name
        except KeyError:
            pytest.skip("the process's own uid/gid has no name on this box")
        path = tmp_path / "owned"
        path.touch()

        ownership = SystemOwnership()

        assert ownership.uid_of(user) == uid
        assert ownership.gid_of(group) == gid
        ownership.chown(path, uid, gid)  # to oneself: allowed unprivileged
        assert (path.stat().st_uid, path.stat().st_gid) == (uid, gid)
        with pytest.raises(KeyError):
            ownership.gid_of("lovspor-no-such-group-3f9c")
        with pytest.raises(KeyError):
            ownership.uid_of("lovspor-no-such-user-3f9c")


def _blank_release_var(node: object) -> None:
    """Empty every ``vars lovspor_release`` in an adapted configuration, in place."""
    if isinstance(node, dict):
        if node.get("handler") == "vars" and RELEASE_VAR in node:
            node[RELEASE_VAR] = ""
        for value in node.values():
            _blank_release_var(value)
    elif isinstance(node, list):
        for item in node:
            _blank_release_var(item)


def _adapt_call(config: Path, fragment: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """One ``caddy adapt`` as the runner records it: the file, and the fragment it imports."""
    return (
        ("caddy", "adapt", "--config", str(config), "--adapter", "caddyfile"),
        {FRAGMENT_ENV: str(fragment)},
    )


def _probe(droplet: Droplet, name: str) -> Path:
    """A throwaway file of the preflight, beside the Caddyfile it is asked about."""
    caddyfile = droplet.plane.caddyfile
    return caddyfile.with_name(f"{caddyfile.name}.lovspor-{name}")


class TestPreflight:
    def test_probe_files_and_fragments_are_forwarded_to_adaptation(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[Path, Path | None, str | None]] = []
        real = migrate.adapt_config

        def recording(runner: object, caddyfile: Path, fragment: Path | None = None) -> object:
            seen.append(
                (
                    caddyfile,
                    fragment,
                    fragment.read_text(encoding="utf-8")
                    if fragment is not None and fragment.exists()
                    else None,
                )
            )
            return real(runner, caddyfile, fragment)  # type: ignore[arg-type]

        monkeypatch.setattr(migrate, "adapt_config", recording)
        preflight(droplet.plane, droplet.host)

        assert seen[0][:2] == (
            droplet.plane.caddyfile.with_name(f"{droplet.plane.caddyfile.name}.lovspor-probe"),
            droplet.plane.fragment,
        )
        assert seen[1][:2] == (
            droplet.host.caddyfile_source,
            droplet.plane.caddyfile.with_name(f"{droplet.plane.caddyfile.name}.lovspor-fragment"),
        )
        assert seen[1][2] == ""

    def test_the_pre_envelope_host_passes_with_and_without_a_release(
        self, droplet: Droplet
    ) -> None:
        gid = droplet.ownership.groups["lovspor-release"]
        old = droplet.old_pair()

        bare = preflight(droplet.plane, droplet.host)
        named = preflight(droplet.plane, droplet.host, droplet.a)

        assert bare == Preflight(
            content_id=None,
            running=old.describe(),
            tcp_admin="localhost:2019",
            socket_admin=droplet.host.socket_admin,
            release_group="lovspor-release",
            gid=gid,
        )
        assert named.content_id == droplet.a
        assert bare.describe() == (
            f"no release named; R={old.describe()} on localhost:2019; socket "
            f"{droplet.host.socket_admin} absent; group lovspor-release (gid {gid}); "
            "Caddy accepts |0660"
        )
        assert named.describe().startswith(f"release {droplet.a}; R=(none, ")
        assert droplet.caddy.reloads == 0 and droplet.caddy.daemon_reloads == 0
        assert not droplet.plane.fragment.exists() and not droplet.host.previous_caddyfile.exists()
        assert not list(droplet.plane.caddyfile.parent.glob("*.lovspor-*"))

    def test_every_question_is_adapted_with_the_fragment_it_is_about(
        self, droplet: Droplet
    ) -> None:
        """The preflight's three ``caddy adapt`` runs, each with its own fragment.

        The fragment reaches Caddy through the environment, so the file the
        composed configuration imports is not visible in the argv: an adapt
        run with the wrong one silently answers a different question. The
        two throwaway files are named after what they are for and stand
        beside the Caddyfile, where the same relative imports resolve.
        """
        preflight(droplet.plane, droplet.host)
        bare = list(droplet.caddy.calls)
        droplet.caddy.calls.clear()

        preflight(droplet.plane, droplet.host, droplet.a)

        assert bare == [
            _adapt_call(droplet.plane.caddyfile, droplet.plane.fragment),
            _adapt_call(_probe(droplet, "probe"), droplet.plane.fragment),
            _adapt_call(droplet.host.caddyfile_source, _probe(droplet, "fragment")),
        ]
        assert droplet.caddy.calls == [
            _adapt_call(droplet.plane.caddyfile, droplet.plane.fragment),
            _adapt_call(_probe(droplet, "probe"), droplet.plane.fragment),
            _adapt_call(
                droplet.host.caddyfile_source, droplet.releases / droplet.a / FRAGMENT_NAME
            ),
        ]

    def test_the_release_less_preflight_composes_with_an_empty_fragment(
        self, droplet: Droplet
    ) -> None:
        """No release named means no release imported.

        ``--check`` without an id asks whether the new Caddyfile adapts at
        all and binds admin where it must; a probe fragment with anything
        in it would fold that content into the answer.
        """
        seen: list[tuple[Path, str | None]] = []

        def record(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            fragment = Path(env[FRAGMENT_ENV])
            text = fragment.read_text(encoding="utf-8") if fragment.is_file() else None
            seen.append((fragment, text))
            return droplet.caddy.run(argv, env)

        adapt_prefix = ("caddy", "adapt")
        plane = replace(droplet.plane, runner=Sabotaged(droplet.caddy, adapt_prefix, record))

        preflight(plane, droplet.host)

        assert seen[-1] == (_probe(droplet, "fragment"), "")

    def test_refuses_once_a_marker_exists(self, droplet: Droplet) -> None:
        write_marker(droplet.releases, Marker(active=droplet.a, previous=None))

        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host)
        assert str(caught.value) == (
            f"a marker exists (active {droplet.a}); the first migration has already happened"
        )

    def test_refuses_when_the_planes_caddyfile_is_not_the_hosts(self, droplet: Droplet) -> None:
        other = replace(droplet.plane, caddyfile=droplet.plane.caddyfile.with_name("Other"))

        with pytest.raises(MigrationRefusedError, match="is not the host's"):
            preflight(other, droplet.host)

    def test_refuses_when_the_socket_already_exists(self, droplet: Droplet) -> None:
        droplet.host.runtime_dir.mkdir(parents=True)
        droplet.socket_file.touch()

        with pytest.raises(MigrationRefusedError, match="admin socket .* already exists"):
            preflight(droplet.plane, droplet.host)

    def test_refuses_to_overwrite_an_existing_backup(self, droplet: Droplet) -> None:
        droplet.host.previous_caddyfile.write_text("old", encoding="utf-8")

        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host)
        assert str(caught.value) == (
            f"{droplet.host.previous_caddyfile} already exists; it is the rollback's source "
            "and is not overwritten"
        )

    def test_refuses_without_the_new_caddyfile(self, droplet: Droplet) -> None:
        droplet.host.caddyfile_source.unlink()

        with pytest.raises(MigrationRefusedError, match="new Caddyfile .* does not exist"):
            preflight(droplet.plane, droplet.host)

    def test_tcp_down_is_unobservable_with_the_migrations_own_hint(self, droplet: Droplet) -> None:
        droplet.caddy.admin_up = False

        with pytest.raises(UnobservableError) as caught:
            preflight(droplet.plane, droplet.host)
        assert caught.value.reason == "admin_unreachable"
        assert caught.value.detail.endswith(
            "; the first migration needs Caddy answering on localhost:2019"
        )

    def test_the_socket_answering_instead_of_tcp_is_unobservable(self, droplet: Droplet) -> None:
        """Only TCP is asked: an instance already on the socket is not the pre-envelope host."""
        droplet.host.runtime_dir.mkdir(parents=True)
        droplet.caddy.run(
            (
                "caddy",
                "reload",
                "--config",
                str(droplet.host.caddyfile_source),
                "--adapter",
                "caddyfile",
                "--address",
                DEFAULT_TCP,
            ),
            {FRAGMENT_ENV: str(droplet.releases / droplet.a / FRAGMENT_NAME)},
        )
        droplet.socket_file.unlink()

        with pytest.raises(UnobservableError, match="localhost:2019: connection refused"):
            preflight(droplet.plane, droplet.host)

    def test_refuses_when_caddy_already_runs_a_release(self, droplet: Droplet) -> None:
        fragment = droplet.releases / droplet.a / FRAGMENT_NAME
        served = toy_adapt(droplet.host.caddyfile_source, {FRAGMENT_ENV: str(fragment)})
        served.pop("admin")
        droplet.caddy.load(served)

        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host)
        assert str(caught.value) == f"Caddy already runs release {droplet.a} on localhost:2019"

    def test_refuses_when_the_served_configuration_is_not_the_files(self, droplet: Droplet) -> None:
        edited = toy_adapt(droplet.plane.caddyfile, {})
        edited["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]["routes"].append(
            {"handle": [{"handler": "file_server", "hide": ["x"]}]}
        )
        droplet.caddy.load(edited)

        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host)
        message = str(caught.value)
        assert message.startswith(
            f"the Caddyfile on disk adapts to {droplet.old_pair().describe()}"
        )
        assert f"Caddy runs {config_pair(edited).describe()} on localhost:2019" in message

    def test_refuses_without_the_group(self, droplet: Droplet) -> None:
        del droplet.ownership.groups["lovspor-release"]

        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host)
        assert str(caught.value) == (
            "group lovspor-release does not exist; groupadd --system lovspor-release"
        )

    def test_refuses_a_caddy_without_the_creation_mode_suffix(self, droplet: Droplet) -> None:
        droplet.caddy.knows_mode_suffix = False

        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host)
        assert str(caught.value).startswith(
            "this Caddy does not accept the creation-mode suffix on the admin address "
            f"({droplet.host.socket_admin}|0660): caddy adapt failed"
        )
        assert not list(droplet.plane.caddyfile.parent.glob("*.lovspor-*"))

    def test_refuses_an_incomplete_envelope_and_a_fragment_naming_another(
        self, droplet: Droplet
    ) -> None:
        (droplet.releases / droplet.b / FRAGMENT_NAME).write_text(
            droplet.fragment_of(droplet.a), encoding="utf-8"
        )
        with pytest.raises(MigrationRefusedError, match=f"{droplet.b}/release.caddy does not name"):
            preflight(droplet.plane, droplet.host, droplet.b)

        (droplet.releases / droplet.b / FRAGMENT_NAME).unlink()
        with pytest.raises(MigrationRefusedError, match="not a complete envelope; not staged"):
            preflight(droplet.plane, droplet.host, droplet.b)

    def test_refuses_a_new_caddyfile_that_does_not_adapt(self, droplet: Droplet) -> None:
        droplet.host.caddyfile_source.write_text("{\n\tadmin off\n", encoding="utf-8")

        with pytest.raises(MigrationRefusedError, match="does not adapt: caddy adapt failed"):
            preflight(droplet.plane, droplet.host)

    @pytest.mark.parametrize(
        ("listen", "shown"),
        [
            ("", "the default address"),
            ("unix//elsewhere.sock|0660", "unix//elsewhere.sock|0660"),
            ("{socket}", "{socket}"),
            ("{socket}|0600", "{socket}|0600"),
        ],
    )
    def test_refuses_a_new_caddyfile_binding_admin_elsewhere(
        self, droplet: Droplet, listen: str, shown: str
    ) -> None:
        socket_admin = droplet.host.socket_admin
        text = new_caddyfile(socket_admin, droplet.plane.fragment)
        listen = listen.replace("{socket}", socket_admin)
        text = text.replace(
            f"\tadmin {socket_admin}|0660\n", f"\tadmin {listen}\n" if listen else ""
        )
        if not listen:
            text = text.replace("{\n}\n", "")
        droplet.host.caddyfile_source.write_text(text, encoding="utf-8")

        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host)
        assert str(caught.value) == (
            f"{droplet.host.caddyfile_source} binds admin to "
            f"{shown.replace('{socket}', socket_admin)}, not {socket_admin}|0660"
        )

    def test_refuses_a_new_caddyfile_that_does_not_import_the_fragment(
        self, droplet: Droplet
    ) -> None:
        text = new_caddyfile(droplet.host.socket_admin, droplet.plane.fragment)
        without = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("import ")
        )
        droplet.host.caddyfile_source.write_text(without + "\n", encoding="utf-8")

        preflight(droplet.plane, droplet.host)  # without a release, nothing to name
        with pytest.raises(MigrationRefusedError) as caught:
            preflight(droplet.plane, droplet.host, droplet.a)
        assert str(caught.value) == (
            f"{droplet.host.caddyfile_source} composed with the fragment names no release, "
            f"not {droplet.a}; does it import the fragment?"
        )

    def test_a_new_caddyfile_importing_a_release_literally_names_the_wrong_one(
        self, droplet: Droplet
    ) -> None:
        text = new_caddyfile(
            droplet.host.socket_admin, droplet.releases / droplet.b / FRAGMENT_NAME
        )
        text = text.replace("{$LOVSPOR_RELEASE_FRAGMENT:", "").replace("}\n\theader", "\n\theader")
        droplet.host.caddyfile_source.write_text(text, encoding="utf-8")

        with pytest.raises(MigrationRefusedError, match=f"names {droplet.b}, not {droplet.a}"):
            preflight(droplet.plane, droplet.host, droplet.a)
        with pytest.raises(MigrationRefusedError, match=f"names {droplet.b}, not no release"):
            preflight(droplet.plane, droplet.host)


def _migrate(droplet: Droplet, checkpoint: Checkpoint | None = None) -> MigrationReport:
    if checkpoint is None:
        return first_migration(droplet.plane, droplet.host, droplet.a)
    return first_migration(droplet.plane, droplet.host, droplet.a, checkpoint)


class TestFirstMigration:
    def test_passes_the_release_to_preflight(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str | None] = []
        real = migrate.preflight

        def recording(
            plane: ControlPlane, host: MigrationHost, content_id: str | None = None
        ) -> Preflight:
            seen.append(content_id)
            return real(plane, host, content_id)

        monkeypatch.setattr(migrate, "preflight", recording)
        _migrate(droplet)
        assert seen == [droplet.a]

    def test_walks_the_steps_in_the_adrs_order(self, droplet: Droplet) -> None:
        reached: list[str] = []
        old = OLD_CADDYFILE.encode("utf-8")

        report = _migrate(droplet, reached.append)

        assert tuple(reached) == MIGRATION_STEPS
        assert report == MigrationReport(
            active=droplet.a,
            admin=droplet.host.socket_admin,
            running=droplet.pair_of(droplet.a).describe(),
            previous_caddyfile=droplet.host.previous_caddyfile,
        )
        host = droplet.host
        assert host.caddyfile.read_bytes() == host.caddyfile_source.read_bytes()
        assert host.previous_caddyfile.read_bytes() == old
        assert droplet.plane.fragment.read_text(encoding="utf-8") == droplet.fragment_of(droplet.a)
        assert not droplet.plane.next_fragment.exists()
        assert host.drop_in.read_text(encoding="utf-8") == drop_in_text(host, with_exec_reload=True)
        assert stat.S_IMODE(host.runtime_dir.stat().st_mode) == 0o2770
        gid = droplet.ownership.groups["lovspor-release"]
        assert droplet.ownership.chowns == [(host.runtime_dir, CADDY_UID, gid)]
        assert read_marker(droplet.releases) == Marker(active=droplet.a, previous=None)
        assert droplet.caddy.admin_address == host.socket_admin
        assert stat.S_IMODE(droplet.socket_file.stat().st_mode) == 0o660
        with pytest.raises(UnobservableError):
            droplet.caddy.running_config_at(DEFAULT_TCP)
        assert live_release(droplet.plane) == droplet.a
        assert situation(read_triple(droplet.plane)) == Situation.reconciled

    def test_the_runner_saw_validate_the_explicit_reload_and_two_daemon_reloads(
        self, droplet: Droplet
    ) -> None:
        _migrate(droplet)

        argvs = droplet.argvs()
        caddyfile = str(droplet.plane.caddyfile)
        reload = ("caddy", "reload", "--config", caddyfile, "--adapter", "caddyfile", "--address")
        reloads = [argv for argv in argvs if argv[:2] == ("caddy", "reload")]
        assert reloads == [(*reload, "localhost:2019")]
        reload_env = next(
            env for argv, env in droplet.caddy.calls if argv[:2] == ("caddy", "reload")
        )
        assert reload_env == {FRAGMENT_ENV: str(droplet.plane.fragment)}
        validate_at = argvs.index(
            ("caddy", "validate", "--config", caddyfile, "--adapter", "caddyfile")
        )
        reload_at = argvs.index(reloads[0])
        daemon_reloads = [
            i for i, argv in enumerate(argvs) if argv == ("systemctl", "daemon-reload")
        ]
        show_at = argvs.index(("systemctl", "show", "caddy", "-p", "ExecReload"))
        assert daemon_reloads[0] < validate_at < reload_at < daemon_reloads[1] < show_at
        assert len(daemon_reloads) == 2
        assert droplet.caddy.reloads == 1
        assert ("systemctl", "reload", "caddy") not in argvs

    def test_the_exec_reload_pair_arrives_only_after_verification(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("reloaded"))
        loaded = droplet.host.drop_in.read_text(encoding="utf-8")
        assert loaded == drop_in_text(droplet.host, with_exec_reload=False)
        assert "ExecReload" not in loaded
        assert "--address" not in droplet.caddy.exec_reload

    def test_the_marker_arrives_last(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("exec_reload"))

        assert droplet.host.drop_in.read_text(encoding="utf-8") == drop_in_text(
            droplet.host, with_exec_reload=True
        )
        assert f"--address {droplet.host.socket_admin}" in droplet.caddy.exec_reload
        assert read_marker(droplet.releases) is None

    def test_everything_written_is_world_readable_whatever_the_umask(
        self, droplet: Droplet, strict_umask: None
    ) -> None:
        _migrate(droplet)

        for path in (
            droplet.host.caddyfile,
            droplet.host.previous_caddyfile,
            droplet.plane.fragment,
            droplet.host.drop_in,
            droplet.releases / MARKER_NAME,
        ):
            assert stat.S_IMODE(path.stat().st_mode) == 0o644, path

    def test_the_previous_caddyfile_is_kept_byte_for_byte_whatever_the_locale(
        self, droplet: Droplet, c_locale: None
    ) -> None:
        droplet.plane.caddyfile.write_bytes(NON_ASCII_OLD_CADDYFILE.encode("utf-8"))
        droplet.caddy.restart()

        _migrate(droplet)

        assert droplet.host.previous_caddyfile.read_bytes() == NON_ASCII_OLD_CADDYFILE.encode(
            "utf-8"
        )

    def test_a_rejected_load_leaves_tcp_answering_the_old_configuration(
        self, droplet: Droplet
    ) -> None:
        droplet.caddy.fail_reloads = 1
        old = droplet.old_pair()

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet)

        assert caught.value.reached == "validated"
        assert caught.value.detail.startswith("caddy reload --address localhost:2019 failed: ")
        message = str(caught.value)
        assert message.startswith(
            "first migration stopped after validated: caddy reload --address localhost:2019 "
            "failed: Error: sending configuration to instance"
        )
        assert "--complete" in message and "--abandon" in message
        assert droplet.caddy.running_config_at(DEFAULT_TCP) is not None
        assert config_pair(droplet.caddy.running_config()) == old
        assert not droplet.socket_file.exists()
        assert adapt(droplet.caddy, droplet.plane.caddyfile, droplet.plane.fragment).release_id == (
            droplet.a
        )
        assert read_marker(droplet.releases) is None
        assert droplet.host.drop_in.read_text(encoding="utf-8") == drop_in_text(
            droplet.host, with_exec_reload=False
        )
        assert situation(read_triple(droplet.over(DEFAULT_TCP))) == Situation.staged

    def test_a_reload_that_exits_silently_names_the_exit_code(self, droplet: Droplet) -> None:
        plane = replace(
            droplet.plane,
            runner=Sabotaged(droplet.caddy, ("caddy", "reload"), Completed(7, "", "")),
        )

        with pytest.raises(MigrationFailedError, match="failed: exit 7;"):
            first_migration(plane, droplet.host, droplet.a)

    def test_a_validate_refusal_after_the_install_names_the_staged_state(
        self, droplet: Droplet
    ) -> None:
        def corrupt(step: str) -> None:
            if step == "installed":
                droplet.plane.fragment.write_text("import /nonexistent.caddy\n", encoding="utf-8")

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet, corrupt)

        assert caught.value.reached == "installed"
        assert str(caught.value).startswith(
            "first migration stopped after installed: caddy validate refused"
        )
        assert "--complete" in str(caught.value) and "--abandon" in str(caught.value)
        assert droplet.caddy.reloads == 0
        assert droplet.caddy.admin_address == DEFAULT_TCP

    def test_an_installed_configuration_naming_no_release_is_refused_before_the_reload(
        self, droplet: Droplet
    ) -> None:
        def blank(step: str) -> None:
            if step == "installed":
                droplet.plane.fragment.write_text("handle {\n\tfile_server\n}\n", encoding="utf-8")

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet, blank)

        assert caught.value.reached == "installed"
        assert "the configuration on disk names no release" in str(caught.value)
        assert droplet.caddy.reloads == 0

    def test_a_socket_that_does_not_answer_after_the_load_is_named(self, droplet: Droplet) -> None:
        def silence(step: str) -> None:
            if step == "reloaded":
                droplet.caddy.admin_up = False

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet, silence)

        assert caught.value.reached == "reloaded"
        assert str(caught.value) == (
            f"first migration stopped after reloaded: the socket does not answer: {ADMIN_DOWN}; "
            "`systemctl restart caddy` loads the new configuration whole and recreates the socket"
        )
        assert read_marker(droplet.releases) is None

    def test_a_socket_serving_something_else_is_named(self, droplet: Droplet) -> None:
        def swap(step: str) -> None:
            if step == "reloaded":
                served = droplet.caddy.running_config()
                assert isinstance(served, dict)
                served["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0][
                    "routes"
                ].append({"handle": [{"handler": "file_server", "hide": ["x"]}]})
                droplet.caddy.load(served)

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet, swap)

        assert caught.value.reached == "reloaded"
        running = config_pair(droplet.caddy.running_config())
        assert str(caught.value) == (
            f"first migration stopped after reloaded: over the socket Caddy runs "
            f"{running.describe()}, not {droplet.pair_of(droplet.a).describe()}"
        )

    def test_tcp_still_answering_is_named(self, droplet: Droplet) -> None:
        """A TCP client that answers whatever address the instance is on: the listener stayed."""

        def clients(address: str) -> FakeCaddy | FakeAdmin:
            return droplet.caddy if address == DEFAULT_TCP else droplet.caddy.admin_client(address)

        host = replace(droplet.host, admin_client=clients)

        with pytest.raises(MigrationFailedError) as caught:
            first_migration(droplet.plane, host, droplet.a)

        assert caught.value.reached == "reloaded"
        assert str(caught.value) == (
            "first migration stopped after reloaded: localhost:2019 still answers; "
            "the admin endpoint did not move"
        )

    def test_a_socket_with_the_wrong_mode_or_group_is_named(self, droplet: Droplet) -> None:
        def loosen(step: str) -> None:
            if step == "reloaded":
                droplet.socket_file.chmod(0o666)

        with pytest.raises(MigrationFailedError, match="has mode 0666, not 0660") as caught:
            _migrate(droplet, loosen)

        assert caught.value.reached == "reloaded"
        assert str(caught.value) == (
            f"first migration stopped after reloaded: {droplet.socket_file} has mode 0666, not 0660"
        )

    def test_a_socket_in_another_group_is_named(self, droplet: Droplet) -> None:
        gid = droplet.ownership.groups["lovspor-release"]
        droplet.ownership.groups["lovspor-release"] = gid + 1

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet)

        assert caught.value.reached == "reloaded"
        assert str(caught.value) == (
            f"first migration stopped after reloaded: {droplet.socket_file} has gid {gid}, "
            f"not lovspor-release's {gid + 1}"
        )

    def test_a_show_that_does_not_name_the_socket_is_named(self, droplet: Droplet) -> None:
        def misplace(step: str) -> None:
            if step == "verified":
                droplet.caddy.drop_in = droplet.host.drop_in.with_name("elsewhere.conf")

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet, misplace)

        assert caught.value.reached == "verified"
        assert str(caught.value).startswith(
            "first migration stopped after verified: systemctl show caddy -p ExecReload does not "
            f"name --address {droplet.host.socket_admin}: ExecReload={{ path=/usr/bin/caddy"
        )
        assert read_marker(droplet.releases) is None

    def test_a_failing_show_is_named(self, droplet: Droplet) -> None:
        show = ("systemctl", "show")
        plane = replace(
            droplet.plane, runner=Sabotaged(droplet.caddy, show, Completed(1, "", "no"))
        )

        with pytest.raises(MigrationFailedError, match="does not name --address .*: no$"):
            first_migration(plane, droplet.host, droplet.a)

    def test_a_failing_daemon_reload_is_named(self, droplet: Droplet) -> None:
        daemon = ("systemctl", "daemon-reload")
        failing = Sabotaged(droplet.caddy, daemon, Completed(1, "", "Failed to reload daemon"))

        with pytest.raises(
            ControlPlaneError, match="daemon-reload failed: Failed to reload daemon"
        ):
            first_migration(replace(droplet.plane, runner=failing), droplet.host, droplet.a)

    def test_the_preflight_is_run_on_the_release_the_migration_was_given(
        self, droplet: Droplet
    ) -> None:
        """The id is the preflight's subject, not only the stage's.

        A preflight that did not receive it never checks that the envelope
        is complete and never composes the new Caddyfile with *this*
        release's fragment, so an unstaged id would be refused by the stage
        instead — after ``_require_no_marker`` has been passed and with a
        different error for the operator.
        """
        (droplet.releases / droplet.b / FRAGMENT_NAME).unlink()

        with pytest.raises(MigrationRefusedError) as caught:
            first_migration(droplet.plane, droplet.host, droplet.b)

        assert str(caught.value) == f"{droplet.b} is not a complete envelope; not staged"
        assert not droplet.plane.next_fragment.exists()
        assert not droplet.host.previous_caddyfile.exists()

    def test_the_preflight_runs_first_and_a_refusal_moves_nothing(self, droplet: Droplet) -> None:
        droplet.caddy.knows_mode_suffix = False

        with pytest.raises(MigrationRefusedError):
            _migrate(droplet)

        assert droplet.plane.caddyfile.read_text(encoding="utf-8") == OLD_CADDYFILE
        assert not droplet.plane.fragment.exists()
        assert not droplet.plane.next_fragment.exists()
        assert not droplet.host.runtime_dir.exists()
        assert droplet.host.drop_in.read_text(encoding="utf-8") == PRE_ENVELOPE_DROP_IN
        assert droplet.caddy.daemon_reloads == 0


class TestTheInstallWindow:
    """(a) writes three files; a crash between two of them must leave a bootable box."""

    def _dying_caddyfile_write(self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch) -> None:
        """The process dies as the new Caddyfile is being installed."""
        real = migrate.atomic_write_bytes

        def dying(path: Path, payload: bytes, *, mode: int | None = None) -> None:
            if path == droplet.plane.caddyfile:
                raise Killed("installing the Caddyfile")
            real(path, payload, mode=mode)

        monkeypatch.setattr(migrate, "atomic_write_bytes", dying)

    def test_the_fragment_lands_before_the_caddyfile_that_imports_it(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The new Caddyfile imports the active fragment by a plain path. Installed
        first, a crash before the rename left `import` naming a file that does not
        exist: `caddy validate` hard-fails, Caddy cannot start, the admin endpoint
        is unreachable on both addresses, and a re-run of `migrate` refuses on the
        backup it already wrote. The fragment goes first, so the composed
        configuration is loadable at every instant of (a)."""
        self._dying_caddyfile_write(droplet, monkeypatch)

        with pytest.raises(Killed):
            _migrate(droplet)

        assert droplet.plane.fragment.read_text(encoding="utf-8") == droplet.fragment_of(droplet.a)
        assert droplet.plane.caddyfile.read_text(encoding="utf-8") == OLD_CADDYFILE
        validate_caddy(droplet.caddy, droplet.plane.caddyfile, droplet.plane.fragment)
        assert adapt(droplet.caddy, droplet.plane.caddyfile, droplet.plane.fragment) == (
            droplet.old_pair()
        )

    def test_the_crash_leaves_a_box_that_still_starts_and_still_answers(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property the order buys: a restart loads the file on disk whole, so
        the operator reaches the admin endpoint and `reconcile` can read the host."""
        self._dying_caddyfile_write(droplet, monkeypatch)
        with pytest.raises(Killed):
            _migrate(droplet)

        droplet.caddy.restart()

        assert detect_admin(droplet.host) == DEFAULT_TCP
        assert situation(read_triple(droplet.over(DEFAULT_TCP))) == Situation.reconciled
        assert reconcile(droplet.plane, host=droplet.host).action == "none"

    def test_the_reverse_order_is_the_window_this_closes(self, droplet: Droplet) -> None:
        """Pinned, so the statements are never swapped back: the new Caddyfile with
        no fragment beside it is exactly what `caddy validate` refuses."""
        with pytest.raises(CommitRefusedError, match="caddy validate refused"):
            validate_caddy(droplet.caddy, droplet.host.caddyfile_source, droplet.plane.fragment)
        assert not droplet.plane.fragment.exists()

    def test_the_backup_is_written_before_either_of_them(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(a) is only reversible once the previous Caddyfile is beside the new one."""
        written: list[Path] = []
        real = migrate.atomic_write_bytes

        def record(path: Path, payload: bytes, *, mode: int | None = None) -> None:
            written.append(path)
            real(path, payload, mode=mode)

        monkeypatch.setattr(migrate, "atomic_write_bytes", record)
        drop_in_modes: list[bool] = []
        real_load = migrate._load_drop_in

        def record_load(plane: ControlPlane, host: MigrationHost, with_exec_reload: bool) -> None:
            drop_in_modes.append(with_exec_reload)
            real_load(plane, host, with_exec_reload)

        monkeypatch.setattr(migrate, "_load_drop_in", record_load)

        _migrate(droplet)

        assert written[:2] == [droplet.host.previous_caddyfile, droplet.plane.caddyfile]
        assert drop_in_modes == [False, True]


class TestCrashRows:
    def test_after_the_install_the_host_is_staged_on_tcp(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))

        triple = read_triple(droplet.over(DEFAULT_TCP))
        assert triple.disk.release_id == droplet.a
        assert triple.running == droplet.old_pair()
        assert triple.marker is None
        assert situation(triple) == Situation.staged
        assert not droplet.socket_file.exists()
        with pytest.raises(UnobservableError):
            read_triple(droplet.plane)

    def test_after_the_reload_the_host_is_reloaded_on_the_socket(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("reloaded"))

        triple = read_triple(droplet.plane)
        assert triple.running == triple.disk == droplet.pair_of(droplet.a)
        assert triple.marker is None
        assert situation(triple) == Situation.reloaded
        with pytest.raises(UnobservableError):
            read_triple(droplet.over(DEFAULT_TCP))

    def test_after_the_marker_the_migration_is_finished(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("marked"))

        assert live_release(droplet.plane) == droplet.a

    def test_a_restart_in_the_staged_state_serves_d_whole_on_the_socket(
        self, droplet: Droplet
    ) -> None:
        """G3's recovery: the runtime lines load before (c), so a restart recreates the socket."""
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))

        droplet.caddy.restart()

        assert droplet.caddy.admin_address == droplet.host.socket_admin
        assert situation(read_triple(droplet.plane)) == Situation.reloaded


def _rollback_reload(droplet: Droplet) -> tuple[str, ...]:
    return (
        "caddy",
        "reload",
        "--config",
        str(droplet.host.previous_caddyfile),
        "--adapter",
        "caddyfile",
        "--address",
        droplet.host.socket_admin,
    )


def _assert_pre_envelope(droplet: Droplet) -> None:
    """The host as the fixture built it: old Caddyfile served on TCP, no envelope anywhere."""
    assert droplet.plane.caddyfile.read_text(encoding="utf-8") == OLD_CADDYFILE
    assert not droplet.host.previous_caddyfile.exists()
    assert not droplet.plane.fragment.exists()
    assert not droplet.plane.next_fragment.exists()
    assert droplet.host.drop_in.read_text(encoding="utf-8") == PRE_ENVELOPE_DROP_IN
    assert droplet.caddy.exec_reload == STOCK_EXEC_RELOAD
    assert droplet.caddy.admin_address == DEFAULT_TCP
    assert config_pair(droplet.caddy.running_config_at(DEFAULT_TCP)) == droplet.old_pair()
    assert not droplet.socket_file.exists()
    assert read_marker(droplet.releases) is None
    assert preflight(droplet.plane, droplet.host, droplet.a).content_id == droplet.a


class TestDetectAdmin:
    def test_tcp_before_the_cutover_and_the_socket_after(self, droplet: Droplet) -> None:
        assert detect_admin(droplet.host) == DEFAULT_TCP

        _migrate(droplet)

        assert detect_admin(droplet.host) == droplet.host.socket_admin

    def test_neither_answering_names_both_addresses(self, droplet: Droplet) -> None:
        droplet.caddy.admin_up = False

        with pytest.raises(UnobservableError) as caught:
            detect_admin(droplet.host)
        assert caught.value.reason == "admin_unreachable"
        assert caught.value.detail == (
            f"{droplet.host.socket_admin}: connection refused; connect: no such file or directory"
        )


class TestRollback:
    def test_before_the_cutover_is_the_file_restore_with_no_reload(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))
        assert droplet.caddy.daemon_reloads == 1

        report = rollback_first_migration(droplet.plane, droplet.host)

        assert report == RollbackReport(
            admin_before=DEFAULT_TCP,
            reloaded=False,
            marker_removed=False,
            exec_reload_removed=False,
            admin=DEFAULT_TCP,
        )
        assert not any(argv[:2] == ("caddy", "reload") for argv in droplet.argvs())
        assert droplet.caddy.reloads == 0
        assert droplet.caddy.daemon_reloads == 2
        _assert_pre_envelope(droplet)

    def test_abandon_is_the_same_file_restore(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))

        report = abandon_first_migration(droplet.plane, droplet.host)

        assert report.reloaded is False and report.admin == DEFAULT_TCP
        _assert_pre_envelope(droplet)

    def test_abandon_tolerates_a_fragment_that_never_landed(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(a) writes the backup before it renames the fragment into place.

        A crash in that window leaves a box with a way back and no active
        fragment; the restore removes what is there and must not trip over
        what is not, or the one state the rollback exists for has no exit.
        """
        real = Path.replace

        def dying(self: Path, target: Path) -> Path:
            if target == droplet.plane.fragment:
                raise Killed("renaming the fragment")
            return real(self, target)

        monkeypatch.setattr(Path, "replace", dying)
        with pytest.raises(Killed):
            _migrate(droplet)
        assert droplet.host.previous_caddyfile.is_file()
        assert not droplet.plane.fragment.exists()

        report = abandon_first_migration(droplet.plane, droplet.host)

        assert report.reloaded is False and report.marker_removed is False
        _assert_pre_envelope(droplet)

    def test_the_restored_drop_in_is_world_readable_whatever_the_umask(
        self, droplet: Droplet, strict_umask: None
    ) -> None:
        """The drop-in provisioning wrote is world-readable, and the restore puts
        back that file — mode included, under whatever umask the operator's shell set."""
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))

        abandon_first_migration(droplet.plane, droplet.host)

        assert stat.S_IMODE(droplet.host.drop_in.stat().st_mode) == 0o644

    def test_the_drop_in_is_read_as_utf8_whatever_the_locale(
        self, droplet: Droplet, c_locale: None
    ) -> None:
        """Whether the ExecReload pair is there is read off the drop-in, and the
        drop-in is UTF-8: a unit started without ``LANG`` must not decode it
        through the C locale and fail the rollback on a comment."""
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))
        droplet.host.drop_in.write_text(
            drop_in_text(droplet.host, with_exec_reload=True) + "# Ørsta kommune\n",
            encoding="utf-8",
        )

        report = abandon_first_migration(droplet.plane, droplet.host)

        assert report.exec_reload_removed is True
        _assert_pre_envelope(droplet)

    def test_before_the_install_there_is_nothing_to_restore(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("staged"))

        with pytest.raises(ControlPlaneError) as caught:
            rollback_first_migration(droplet.plane, droplet.host)
        assert str(caught.value) == (
            f"{droplet.host.previous_caddyfile} is missing: (a) never ran, the rollback already "
            "ran, or `migrate --retire` removed it with the pre-envelope layout; nothing to restore"
        )
        assert droplet.plane.next_fragment.exists()

    def test_a_missing_backup_is_refused_before_the_admin_is_dialled(
        self, droplet: Droplet
    ) -> None:
        """The state that most needs a refusal is the one where nothing answers."""
        _migrate(droplet)
        droplet.host.previous_caddyfile.unlink()
        droplet.caddy.admin_up = False

        with pytest.raises(ControlPlaneError, match="nothing to restore"):
            rollback_first_migration(droplet.plane, droplet.host)

    @pytest.mark.parametrize(
        ("step", "marker_removed", "pair_removed"),
        [("reloaded", False, False), ("exec_reload", False, True), ("marked", True, True)],
    )
    def test_after_the_cutover_delivers_the_previous_caddyfile_to_the_socket(
        self, droplet: Droplet, step: str, marker_removed: bool, pair_removed: bool
    ) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at(step))
        assert droplet.caddy.admin_address == droplet.host.socket_admin
        calls_before = len(droplet.caddy.calls)

        report = rollback_first_migration(droplet.plane, droplet.host)

        assert report == RollbackReport(
            admin_before=droplet.host.socket_admin,
            reloaded=True,
            marker_removed=marker_removed,
            exec_reload_removed=pair_removed,
            admin=DEFAULT_TCP,
        )
        reloads = [
            (argv, env) for argv, env in droplet.caddy.calls[calls_before:] if argv[1] == "reload"
        ]
        assert reloads == [(_rollback_reload(droplet), {})]
        shown = droplet.caddy.run(("systemctl", "show", "caddy", "-p", "ExecReload"), {})
        assert "--address" not in shown.stdout
        _assert_pre_envelope(droplet)

    def test_after_a_finished_migration_the_migration_can_be_run_again(
        self, droplet: Droplet
    ) -> None:
        _migrate(droplet)

        rollback_first_migration(droplet.plane, droplet.host)
        _assert_pre_envelope(droplet)
        again = _migrate(droplet)

        assert again.active == droplet.a
        assert live_release(droplet.plane) == droplet.a

    def test_refuses_once_a_second_release_has_happened(self, droplet: Droplet) -> None:
        _migrate(droplet)
        write_marker(droplet.releases, Marker(active=droplet.b, previous=droplet.a))
        before = droplet.plane.caddyfile.read_bytes()

        with pytest.raises(ControlPlaneError) as caught:
            rollback_first_migration(droplet.plane, droplet.host)
        assert str(caught.value) == (
            f"the marker names a previous release ({droplet.a[:12]}); that is "
            "`lovspor release rollback`, not the first migration's"
        )
        assert droplet.plane.caddyfile.read_bytes() == before
        assert droplet.caddy.admin_address == droplet.host.socket_admin

    def test_refuses_while_nothing_answers(self, droplet: Droplet) -> None:
        _migrate(droplet)
        droplet.caddy.admin_up = False

        with pytest.raises(UnobservableError):
            rollback_first_migration(droplet.plane, droplet.host)
        assert read_marker(droplet.releases) == Marker(active=droplet.a, previous=None)

    def test_a_refused_reload_back_leaves_the_envelope_served(self, droplet: Droplet) -> None:
        _migrate(droplet)
        droplet.caddy.fail_reloads = 1

        with pytest.raises(ReloadFailedError) as caught:
            rollback_first_migration(droplet.plane, droplet.host)

        assert str(caught.value).startswith(
            f"caddy reload --address {droplet.host.socket_admin} of "
            f"{droplet.host.previous_caddyfile} failed: Error: sending configuration"
        )
        assert str(caught.value).endswith("; the envelope is still served")
        assert live_release(droplet.plane) == droplet.a
        assert droplet.host.previous_caddyfile.is_file()
        assert droplet.host.drop_in.read_text(encoding="utf-8") == drop_in_text(droplet.host, True)

    def test_a_silent_reload_failure_names_the_exit_code(self, droplet: Droplet) -> None:
        _migrate(droplet)
        plane = replace(
            droplet.plane,
            runner=Sabotaged(droplet.caddy, ("caddy", "reload"), Completed(9, "", "")),
        )

        with pytest.raises(ReloadFailedError, match="failed: exit 9;"):
            rollback_first_migration(plane, droplet.host)

    def test_tcp_not_answering_after_the_reload_back_is_named(self, droplet: Droplet) -> None:
        _migrate(droplet)
        plane = replace(
            droplet.plane,
            runner=Sabotaged(droplet.caddy, ("caddy", "reload"), Completed(0, "", "")),
        )

        with pytest.raises(ReloadFailedError) as caught:
            rollback_first_migration(plane, droplet.host)
        assert str(caught.value) == (
            "after the reload localhost:2019 does not answer: localhost:2019: connection refused"
        )
        assert read_marker(droplet.releases) == Marker(active=droplet.a, previous=None)

    def test_a_release_still_served_on_tcp_after_the_reload_back_is_named(
        self, droplet: Droplet
    ) -> None:
        _migrate(droplet)
        served = droplet.caddy.running_config()
        assert isinstance(served, dict)
        served.pop("admin")

        def keep_serving(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            droplet.caddy.load(served)
            droplet.socket_file.unlink()
            droplet.caddy.admin_address = DEFAULT_TCP
            return Completed(0, "", "")

        plane = replace(
            droplet.plane, runner=Sabotaged(droplet.caddy, ("caddy", "reload"), keep_serving)
        )

        with pytest.raises(ReloadFailedError) as caught:
            rollback_first_migration(plane, droplet.host)
        assert str(caught.value) == (
            f"after the reload Caddy still runs release {droplet.a} on localhost:2019"
        )

    def test_a_socket_that_survives_the_reload_back_is_named(self, droplet: Droplet) -> None:
        _migrate(droplet)

        def leave_the_file(argv: Sequence[str], env: Mapping[str, str]) -> Completed:
            done = droplet.caddy.run(argv, env)
            droplet.socket_file.touch()
            return done

        plane = replace(
            droplet.plane, runner=Sabotaged(droplet.caddy, ("caddy", "reload"), leave_the_file)
        )

        with pytest.raises(ReloadFailedError, match="still exists after the reload back to TCP"):
            rollback_first_migration(plane, droplet.host)

    def test_a_show_still_naming_the_socket_is_named(self, droplet: Droplet) -> None:
        _migrate(droplet)
        stale = Completed(
            0, f"ExecReload={{ argv[]=x --address {droplet.host.socket_admin} }}\n", ""
        )
        plane = replace(
            droplet.plane, runner=Sabotaged(droplet.caddy, ("systemctl", "show"), stale)
        )

        with pytest.raises(ControlPlaneError) as caught:
            rollback_first_migration(plane, droplet.host)
        assert str(caught.value).startswith(
            f"systemctl show caddy -p ExecReload still names --address {droplet.host.socket_admin} "
            "after the drop-in was restored: ExecReload={ argv[]=x"
        )

    def test_a_failing_show_is_named_too(self, droplet: Droplet) -> None:
        _migrate(droplet)
        plane = replace(
            droplet.plane,
            runner=Sabotaged(droplet.caddy, ("systemctl", "show"), Completed(1, "", "boom")),
        )

        with pytest.raises(ControlPlaneError, match="restored: boom$"):
            rollback_first_migration(plane, droplet.host)

    def _dying_restore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The process dies between the marker's removal and the file restore — once.

        Only the first call dies, so the second run is the real recovery
        rather than a test that undid the fixture's environment with it.
        """
        real = migrate._restore_files
        died: list[str] = []

        def dying(plane: ControlPlane, host: MigrationHost) -> None:
            if not died:
                died.append("restoring the files")
                raise Killed(died[0])
            real(plane, host)

        monkeypatch.setattr(migrate, "_restore_files", dying)

    def test_a_rollback_that_dies_before_the_restore_leaves_the_backup(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_restore_files` consumes `Caddyfile.pre-envelope`. Removing the marker
        after it meant a crash between the two left the marker with the backup
        already gone: every later rollback hit the `nothing to restore` refusal,
        and `reconcile` — which ignores the host once a marker exists — dialled
        the socket that reload had just closed. The marker goes first, so the
        crash window keeps the one file the recovery needs."""
        _migrate(droplet)
        self._dying_restore(monkeypatch)

        with pytest.raises(Killed):
            rollback_first_migration(droplet.plane, droplet.host)

        assert read_marker(droplet.releases) is None
        assert droplet.host.previous_caddyfile.is_file()
        assert droplet.caddy.admin_address == DEFAULT_TCP

    def test_the_second_run_finishes_what_the_crash_left(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _migrate(droplet)
        self._dying_restore(monkeypatch)
        with pytest.raises(Killed):
            rollback_first_migration(droplet.plane, droplet.host)

        report = rollback_first_migration(droplet.plane, droplet.host)

        assert report.marker_removed is False and report.reloaded is False
        _assert_pre_envelope(droplet)

    def test_reconcile_reads_the_crashed_rollback_on_the_address_that_answers(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The marker's absence is what routes `reconcile` through the host, and
        the host is what reaches TCP after the socket closed."""
        _migrate(droplet)
        self._dying_restore(monkeypatch)
        with pytest.raises(Killed):
            rollback_first_migration(droplet.plane, droplet.host)

        report = reconcile(droplet.plane, "abandon", droplet.host)

        assert report.action == "abandoned" and report.admin == DEFAULT_TCP
        _assert_pre_envelope(droplet)

    def test_abandon_removes_the_marker_before_the_files_too(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-cutover half has the same window and the same ordering."""
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))
        write_marker(droplet.releases, Marker(active=droplet.a, previous=None))
        self._dying_restore(monkeypatch)

        with pytest.raises(Killed):
            abandon_first_migration(droplet.plane, droplet.host)

        assert read_marker(droplet.releases) is None
        assert droplet.host.previous_caddyfile.is_file()

    def test_a_missing_backup_takes_the_marker_with_nothing(self, droplet: Droplet) -> None:
        """The refusal comes before the marker moves: an unrestorable host keeps
        its marker, so nothing reads it as a finished rollback."""
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))
        write_marker(droplet.releases, Marker(active=droplet.a, previous=None))
        droplet.host.previous_caddyfile.unlink()

        with pytest.raises(ControlPlaneError, match="nothing to restore"):
            abandon_first_migration(droplet.plane, droplet.host)

        assert read_marker(droplet.releases) == Marker(active=droplet.a, previous=None)


class TestOfflineRollback:
    """The last resort: Caddy answers on neither address, so nothing is dialled."""

    def test_restores_the_files_and_restarts_the_unit(self, droplet: Droplet) -> None:
        """`rollback_first_migration` dials `detect_admin` first, so it raises
        `UnobservableError` in exactly the state an operator most needs a way
        back — a box that will not load the configuration on disk. This path
        skips the dial: files back, unit restarted, endpoint on TCP again."""
        _migrate(droplet)
        droplet.caddy.admin_up = False
        with pytest.raises(UnobservableError):
            detect_admin(droplet.host)
        calls_before = len(droplet.caddy.calls)

        report = offline_rollback(droplet.plane, droplet.host)

        assert report == RollbackReport(
            admin_before=OFFLINE_ADMIN,
            reloaded=False,
            marker_removed=True,
            exec_reload_removed=True,
            admin=DEFAULT_TCP,
            restarted="caddy",
        )
        assert droplet.caddy.restarts == 1
        after = [argv for argv, _ in droplet.caddy.calls[calls_before:]]
        assert ("systemctl", "restart", "caddy") in after
        assert not any(argv[:2] == ("caddy", "reload") for argv in after)
        _assert_pre_envelope(droplet)

    def test_before_the_cutover_it_is_the_same_restore(self, droplet: Droplet) -> None:
        """The crash window of (a) is where a box refuses to start at all."""
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))
        droplet.caddy.admin_up = False

        report = offline_rollback(droplet.plane, droplet.host)

        assert report.marker_removed is False and report.restarted == "caddy"
        _assert_pre_envelope(droplet)

    def test_refuses_without_the_backup(self, droplet: Droplet) -> None:
        with pytest.raises(ControlPlaneError, match="nothing to restore"):
            offline_rollback(droplet.plane, droplet.host)
        assert not droplet.plane.fragment.exists()

    def test_a_failing_restart_is_named_after_the_files_are_back(self, droplet: Droplet) -> None:
        _migrate(droplet)
        plane = replace(
            droplet.plane,
            runner=Sabotaged(
                droplet.caddy, ("systemctl", "restart"), Completed(1, "", "Job for caddy failed")
            ),
        )

        with pytest.raises(ControlPlaneError) as caught:
            offline_rollback(plane, droplet.host)
        assert str(caught.value) == "systemctl restart caddy failed: Job for caddy failed"
        assert droplet.plane.caddyfile.read_text(encoding="utf-8") == OLD_CADDYFILE
        assert read_marker(droplet.releases) is None

    def test_a_silent_restart_failure_names_the_exit_code(self, droplet: Droplet) -> None:
        _migrate(droplet)
        plane = replace(
            droplet.plane,
            runner=Sabotaged(droplet.caddy, ("systemctl", "restart"), Completed(4, "", "")),
        )

        with pytest.raises(ControlPlaneError, match="restart caddy failed: exit 4$"):
            offline_rollback(plane, droplet.host)


class TestRetire:
    def _litter(self, droplet: Droplet) -> dict[str, Path]:
        www = droplet.host.site_root.parent
        www.mkdir(parents=True)
        droplet.host.site_root.mkdir()
        (droplet.host.site_root / "index.html").write_text("<html>", encoding="utf-8")
        flat = droplet.releases / "20260908T120000Z-abcdef123456"
        flat.mkdir()
        (flat / "lov").mkdir()
        droplet.host.current_symlink.symlink_to(flat)
        build = droplet.releases / ".build-running"
        build.mkdir()
        (droplet.releases / "20260908T120000Z-not-a-release").mkdir()
        return {"flat": flat, "build": build}

    def test_removes_the_symlink_the_site_root_and_the_flat_releases(
        self, droplet: Droplet
    ) -> None:
        _migrate(droplet)
        litter = self._litter(droplet)

        report = retire_pre_envelope(droplet.plane, droplet.host)

        assert report == RetireReport(
            removed=(
                str(droplet.host.current_symlink),
                str(droplet.host.site_root),
                str(litter["flat"]),
                str(droplet.host.previous_caddyfile),
            )
        )
        assert not droplet.host.previous_caddyfile.exists()
        assert not droplet.host.current_symlink.is_symlink()
        assert not droplet.host.site_root.exists()
        assert not litter["flat"].exists()
        assert litter["build"].is_dir()
        assert (droplet.releases / "20260908T120000Z-not-a-release").is_dir()
        assert (droplet.releases / droplet.a).is_dir() and (droplet.releases / droplet.b).is_dir()
        assert (droplet.releases / MARKER_NAME).is_file()
        assert live_release(droplet.plane) == droplet.a

    def test_the_way_back_goes_last_and_the_rollback_then_refuses(self, droplet: Droplet) -> None:
        """`--retire` deletes every tree the previous Caddyfile roots at. Leaving
        that file behind armed a rollback that restores a configuration serving
        nothing: `root *` tolerates a missing directory and `redirects*.caddy`
        tolerates zero matches, so the reload returns 0, verification passes, the
        backup is consumed and the marker removed — and lovspor.no 404s
        everywhere while the command exits 0."""
        _migrate(droplet)
        self._litter(droplet)
        backup = droplet.host.previous_caddyfile
        assert backup.is_file()

        report = retire_pre_envelope(droplet.plane, droplet.host)

        assert report.removed[-1] == str(backup)
        assert not backup.exists()
        with pytest.raises(ControlPlaneError) as caught:
            rollback_first_migration(droplet.plane, droplet.host)
        assert str(caught.value) == (
            f"{backup} is missing: (a) never ran, the rollback already ran, or "
            "`migrate --retire` removed it with the pre-envelope layout; nothing to restore"
        )
        assert live_release(droplet.plane) == droplet.a
        assert droplet.caddy.admin_address == droplet.host.socket_admin

    def test_a_refused_removal_leaves_the_way_back_armed(self, droplet: Droplet) -> None:
        """The backup is unlinked only after every removal succeeded, so a refusal
        part-way leaves a host the rollback still returns to the old site."""
        _migrate(droplet)
        self._litter(droplet)
        droplet.host.current_symlink.unlink()
        droplet.host.current_symlink.mkdir()

        with pytest.raises(ControlPlaneError, match="is not a symlink; not removed"):
            retire_pre_envelope(droplet.plane, droplet.host)

        assert droplet.host.previous_caddyfile.is_file()
        assert rollback_first_migration(droplet.plane, droplet.host).reloaded is True
        _assert_pre_envelope(droplet)

    def test_with_the_trees_already_gone_only_the_way_back_is_left(self, droplet: Droplet) -> None:
        _migrate(droplet)
        backup = str(droplet.host.previous_caddyfile)

        first = retire_pre_envelope(droplet.plane, droplet.host)

        assert first == RetireReport(removed=(backup,))
        assert retire_pre_envelope(droplet.plane, droplet.host) == RetireReport(removed=())

    def test_refuses_without_a_marker(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("exec_reload"))
        self._litter(droplet)

        with pytest.raises(ControlPlaneError) as caught:
            retire_pre_envelope(droplet.plane, droplet.host)
        assert str(caught.value) == (
            "no marker: the first migration has not finished; nothing retired"
        )
        assert droplet.host.current_symlink.is_symlink()

    def test_refuses_while_unreconciled_or_unobservable(self, droplet: Droplet) -> None:
        _migrate(droplet)
        self._litter(droplet)
        droplet.plane.fragment.write_text(droplet.fragment_of(droplet.b), encoding="utf-8")
        with pytest.raises(UnreconciledError, match="staged_not_reloaded"):
            retire_pre_envelope(droplet.plane, droplet.host)

        droplet.caddy.admin_up = False
        with pytest.raises(UnobservableError):
            retire_pre_envelope(droplet.plane, droplet.host)
        assert droplet.host.current_symlink.is_symlink() and droplet.host.site_root.is_dir()

    def test_a_current_that_is_a_real_directory_is_refused_before_anything_goes(
        self, droplet: Droplet
    ) -> None:
        _migrate(droplet)
        litter = self._litter(droplet)
        droplet.host.current_symlink.unlink()
        droplet.host.current_symlink.mkdir()

        with pytest.raises(ControlPlaneError) as caught:
            retire_pre_envelope(droplet.plane, droplet.host)
        assert str(caught.value) == f"{droplet.host.current_symlink} is not a symlink; not removed"
        assert droplet.host.site_root.is_dir() and litter["flat"].is_dir()
        assert droplet.host.previous_caddyfile.is_file()

    def test_a_site_root_that_is_a_symlink_or_a_file_is_refused(self, droplet: Droplet) -> None:
        _migrate(droplet)
        litter = self._litter(droplet)
        shutil.rmtree(droplet.host.site_root)
        droplet.host.site_root.symlink_to(litter["flat"])
        with pytest.raises(ControlPlaneError, match="is not a directory; not removed"):
            retire_pre_envelope(droplet.plane, droplet.host)
        assert litter["flat"].is_dir()

        droplet.host.site_root.unlink()
        droplet.host.site_root.write_text("x", encoding="utf-8")
        with pytest.raises(ControlPlaneError, match="is not a directory; not removed"):
            retire_pre_envelope(droplet.plane, droplet.host)

    def test_refuses_when_the_releases_root_is_inside_the_site_root(self, droplet: Droplet) -> None:
        """`LOVSPOR_RELEASES_ROOT=/var/www/lovspor/releases` — a plausible reading
        of "keep the releases under the site root" — would take every envelope and
        the marker out with the pre-envelope tree. The refusal comes before the
        marker check, which on such a host reads the wrong directory anyway."""
        _migrate(droplet)
        self._litter(droplet)
        inside = replace(droplet.plane, releases=droplet.host.site_root / "releases")

        with pytest.raises(ControlPlaneError) as caught:
            retire_pre_envelope(inside, droplet.host)
        assert str(caught.value) == (
            f"the releases root {inside.releases} is inside {droplet.host.site_root}, which "
            "--retire removes; nothing retired"
        )
        with pytest.raises(ControlPlaneError, match="which --retire removes"):
            retire_targets(inside, droplet.host)
        assert droplet.host.site_root.is_dir()
        assert droplet.host.current_symlink.is_symlink()
        assert droplet.host.previous_caddyfile.is_file()

    def test_refuses_when_the_releases_root_is_the_site_root_itself(self, droplet: Droplet) -> None:
        _migrate(droplet)
        self._litter(droplet)
        same = replace(droplet.plane, releases=droplet.host.site_root)

        with pytest.raises(ControlPlaneError, match="which --retire removes"):
            retire_pre_envelope(same, droplet.host)
        assert droplet.host.site_root.is_dir()

    def test_refuses_when_the_releases_root_is_under_the_current_symlink(
        self, droplet: Droplet
    ) -> None:
        """Unlinking the symlink would orphan every envelope beneath it."""
        _migrate(droplet)
        self._litter(droplet)
        under = replace(droplet.plane, releases=droplet.host.current_symlink / "releases")

        with pytest.raises(ControlPlaneError) as caught:
            retire_pre_envelope(under, droplet.host)
        assert str(caught.value) == (
            f"the releases root {under.releases} is inside {droplet.host.current_symlink}, which "
            "--retire removes; nothing retired"
        )
        assert droplet.host.current_symlink.is_symlink()

    def test_the_droplets_own_releases_root_is_outside_both(self, droplet: Droplet) -> None:
        _migrate(droplet)

        assert retire_targets(droplet.plane, droplet.host) == (
            str(droplet.host.previous_caddyfile),
        )

    def test_a_flat_named_symlink_in_the_releases_root_is_left_alone(
        self, droplet: Droplet
    ) -> None:
        _migrate(droplet)
        link = droplet.releases / "20260101T000000Z-0123456789ab"
        link.symlink_to(droplet.releases / droplet.b)

        report = retire_pre_envelope(droplet.plane, droplet.host)

        assert report.removed == (str(droplet.host.previous_caddyfile),)
        assert link.is_symlink() and (droplet.releases / droplet.b).is_dir()


PLACEHOLDER = "handle {\n\troot * /var/www/lovspor\n\tfile_server\n}\n"
"""A fresh box's active fragment: no ``vars``, no release."""


class TestReconcileWindow:
    """No marker: ``reconcile`` given the host reads whichever address answers (ADR-0014)."""

    def _staged_on_tcp(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("installed"))

    def test_the_pre_envelope_host_is_reconciled_with_nothing_live(self, droplet: Droplet) -> None:
        report = reconcile(droplet.plane, host=droplet.host)

        assert report == ReconcileReport(
            situation=Situation.reconciled,
            live=None,
            action="none",
            triple=read_triple(droplet.over(DEFAULT_TCP)).describe(),
            admin=DEFAULT_TCP,
        )

    def test_staged_on_tcp_names_the_migrations_two_resolutions(self, droplet: Droplet) -> None:
        self._staged_on_tcp(droplet)
        triple = read_triple(droplet.over(DEFAULT_TCP))

        with pytest.raises(UnreconciledError) as caught:
            reconcile(droplet.plane, host=droplet.host)
        assert str(caught.value) == (
            f"host is staged_not_reloaded: {triple.describe()} on localhost:2019; resolve with "
            "--complete (the cutover: validate, caddy reload --address localhost:2019, verify over "
            f"{droplet.host.socket_admin}, the ExecReload pair, the marker) or --abandon (the "
            "previous Caddyfile restored; no reload)"
        )
        assert droplet.caddy.reloads == 0 and droplet.caddy.admin_address == DEFAULT_TCP

    def test_complete_on_tcp_runs_the_cutover_through_to_the_marker(self, droplet: Droplet) -> None:
        self._staged_on_tcp(droplet)
        triple = read_triple(droplet.over(DEFAULT_TCP))

        report = reconcile(droplet.plane, "complete", droplet.host)

        assert report == ReconcileReport(
            situation=Situation.reconciled,
            live=droplet.a,
            action="completed",
            triple=triple.describe(),
            admin=droplet.host.socket_admin,
        )
        reloads = [argv for argv in droplet.argvs() if argv[:2] == ("caddy", "reload")]
        assert [argv[-1] for argv in reloads] == [DEFAULT_TCP]
        assert droplet.host.drop_in.read_text(encoding="utf-8") == drop_in_text(droplet.host, True)
        assert f"--address {droplet.host.socket_admin}" in droplet.caddy.exec_reload
        assert read_marker(droplet.releases) == Marker(active=droplet.a, previous=None)
        assert live_release(droplet.plane) == droplet.a
        assert reconcile(droplet.plane, host=droplet.host).action == "none"

    def test_reconcile_forwards_the_answering_tcp_address_to_completion(
        self, droplet: Droplet, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._staged_on_tcp(droplet)
        seen: list[str] = []
        real = migrate.complete_first_migration

        def recording(plane: ControlPlane, host: MigrationHost, answered: str) -> MigrationReport:
            seen.append(answered)
            return real(plane, host, answered)

        monkeypatch.setattr("lovspor.release.reconcile.complete_first_migration", recording)
        reconcile(droplet.plane, "complete", droplet.host)
        assert seen == [DEFAULT_TCP]

    def test_complete_repeats_the_idempotent_half_of_the_install(self, droplet: Droplet) -> None:
        """A crash inside (a): the runtime directory and the drop-in lines are redone."""
        self._staged_on_tcp(droplet)
        droplet.host.runtime_dir.rmdir()
        droplet.host.drop_in.write_text(PRE_ENVELOPE_DROP_IN, encoding="utf-8")
        droplet.caddy.run(("systemctl", "daemon-reload"), {})
        droplet.ownership.chowns.clear()

        report = reconcile(droplet.plane, "complete", droplet.host)

        assert report.action == "completed" and report.live == droplet.a
        assert stat.S_IMODE(droplet.host.runtime_dir.stat().st_mode) == 0o2770
        gid = droplet.ownership.groups["lovspor-release"]
        assert droplet.ownership.chowns == [(droplet.host.runtime_dir, CADDY_UID, gid)]
        assert live_release(droplet.plane) == droplet.a

    def test_completing_repeats_the_install_without_the_pair(self, droplet: Droplet) -> None:
        """The ExecReload pair is (e)'s, and (e) is after the verification.

        Completing on TCP redoes (a)'s idempotent half first, so it writes
        the drop-in a second time — the runtime lines only. Written with the
        pair there instead, a cutover that then fails would leave a unit
        whose reload line dials a socket the box does not have.
        """
        self._staged_on_tcp(droplet)
        droplet.caddy.fail_reloads = 1

        with pytest.raises(MigrationFailedError) as caught:
            complete_first_migration(droplet.plane, droplet.host, DEFAULT_TCP)

        assert caught.value.reached == "validated"
        assert droplet.host.drop_in.read_text(encoding="utf-8") == drop_in_text(
            droplet.host, with_exec_reload=False
        )
        assert droplet.caddy.exec_reload == STOCK_EXEC_RELOAD

    def test_abandon_on_tcp_is_the_file_restore_with_no_reload(self, droplet: Droplet) -> None:
        self._staged_on_tcp(droplet)
        triple = read_triple(droplet.over(DEFAULT_TCP))
        reloads_before = droplet.caddy.reloads

        report = reconcile(droplet.plane, "abandon", droplet.host)

        assert report == ReconcileReport(
            situation=Situation.reconciled,
            live=None,
            action="abandoned",
            triple=triple.describe(),
            admin=DEFAULT_TCP,
        )
        assert droplet.caddy.reloads == reloads_before
        assert not any(argv[:2] == ("caddy", "reload") for argv in droplet.argvs())
        _assert_pre_envelope(droplet)
        assert reconcile(droplet.plane, host=droplet.host).live is None

    def test_an_empty_release_var_is_named_back_and_never_marked(self, droplet: Droplet) -> None:
        """R = D carrying an empty ``lovspor_release`` is the *reloaded* row, whose
        resolution is to write the marker without asking. The running configuration
        is read off Caddy's admin API, so its release var is untrusted input: the
        marker refuses a name that is not a release id — never steering a later
        prune or rollback — and the refusal quotes what was read, empty and all."""
        config = toy_adapt(
            droplet.host.caddyfile_source,
            {FRAGMENT_ENV: str(droplet.releases / droplet.a / FRAGMENT_NAME)},
        )
        _blank_release_var(config)
        droplet.caddy.load(config)
        canned = Completed(0, json.dumps(config), "")
        plane = replace(
            droplet.over(DEFAULT_TCP),
            runner=Sabotaged(droplet.caddy, ("caddy", "adapt"), canned),
        )

        with pytest.raises(ValidationError, match="not a release id: ''"):
            reconcile(plane)

        assert read_marker(droplet.releases) is None

    def test_a_failed_cutover_leaves_the_staged_row_for_reconcile(self, droplet: Droplet) -> None:
        droplet.caddy.fail_reloads = 1
        with pytest.raises(MigrationFailedError):
            _migrate(droplet)

        with pytest.raises(UnreconciledError, match="staged_not_reloaded"):
            reconcile(droplet.plane, host=droplet.host)
        report = reconcile(droplet.plane, "complete", droplet.host)

        assert report.action == "completed" and live_release(droplet.plane) == droplet.a

    def test_reloaded_on_the_socket_is_completed_without_a_choice(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("reloaded"))
        triple = read_triple(droplet.plane)
        assert "ExecReload" not in droplet.host.drop_in.read_text(encoding="utf-8")

        report = reconcile(droplet.plane, host=droplet.host)

        assert report == ReconcileReport(
            situation=Situation.reconciled,
            live=droplet.a,
            action="completed",
            triple=triple.describe(),
            admin=droplet.host.socket_admin,
        )
        assert droplet.host.drop_in.read_text(encoding="utf-8") == drop_in_text(droplet.host, True)
        assert read_marker(droplet.releases) == Marker(active=droplet.a, previous=None)
        assert droplet.caddy.reloads == 1
        assert live_release(droplet.plane) == droplet.a

    @pytest.mark.parametrize("action", ["report", "complete", "abandon"])
    def test_reloaded_on_the_socket_ignores_the_flag(self, droplet: Droplet, action: str) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("exec_reload"))

        report = reconcile(droplet.plane, action, droplet.host)  # type: ignore[arg-type]

        assert report.action == "completed" and report.live == droplet.a

    def test_complete_from_the_socket_verifies_before_marking(self, droplet: Droplet) -> None:
        with pytest.raises(Killed):
            _migrate(droplet, _kill_at("reloaded"))
        droplet.socket_file.chmod(0o600)

        with pytest.raises(MigrationFailedError, match="has mode 0600, not 0660"):
            complete_first_migration(droplet.plane, droplet.host, droplet.host.socket_admin)
        assert read_marker(droplet.releases) is None

    def test_foreign_on_tcp_is_refused_whatever_the_flag(self, droplet: Droplet) -> None:
        edited = toy_adapt(droplet.plane.caddyfile, {})
        edited["apps"]["http"]["servers"]["srv0"]["routes"][0]["handle"][0]["routes"].append(
            {"handle": [{"handler": "file_server", "hide": ["x"]}]}
        )
        droplet.caddy.load(edited)
        triple = read_triple(droplet.over(DEFAULT_TCP))

        for action in ("report", "complete", "abandon"):
            with pytest.raises(UnreconciledError) as caught:
                reconcile(droplet.plane, action, droplet.host)  # type: ignore[arg-type]
            assert str(caught.value) == (
                f"host is foreign on localhost:2019: {triple.describe()}; the first migration's "
                "precondition — the pre-envelope configuration serving — is unmet; nothing is "
                "resolved automatically"
            )
        assert droplet.caddy.reloads == 0
        assert droplet.plane.caddyfile.read_text(encoding="utf-8") == OLD_CADDYFILE

    def test_nothing_answering_is_unobservable_naming_both_addresses(
        self, droplet: Droplet
    ) -> None:
        droplet.caddy.admin_up = False

        with pytest.raises(UnobservableError) as caught:
            reconcile(droplet.plane, host=droplet.host)
        assert droplet.host.socket_admin in caught.value.detail
        assert "connect: no such file or directory" in caught.value.detail

    def _provisioned_box(self, droplet: Droplet) -> None:
        """Caddy on the socket serving a placeholder fragment, no marker: a fresh box."""
        _migrate(droplet)
        (droplet.releases / MARKER_NAME).unlink()
        droplet.plane.fragment.write_text(PLACEHOLDER, encoding="utf-8")
        droplet.caddy.restart()
        assert droplet.caddy.admin_address == droplet.host.socket_admin

    def test_a_provisioned_box_with_nothing_live_is_reconciled_on_the_socket(
        self, droplet: Droplet
    ) -> None:
        self._provisioned_box(droplet)

        report = reconcile(droplet.plane, host=droplet.host)

        assert report.situation == Situation.reconciled and report.live is None
        assert report.action == "none" and report.admin == droplet.host.socket_admin

    def test_a_provisioned_box_staged_on_the_socket_is_decision_sixs_row(
        self, droplet: Droplet
    ) -> None:
        """Not the migration's window: the drop-in's reload line reaches the socket."""
        self._provisioned_box(droplet)
        droplet.plane.fragment.write_text(droplet.fragment_of(droplet.a), encoding="utf-8")
        triple = read_triple(droplet.plane)
        assert situation(triple) == Situation.staged

        with pytest.raises(UnreconciledError, match="D becomes the truth"):
            reconcile(droplet.plane, host=droplet.host)
        with pytest.raises(ControlPlaneError, match="no previous fragment to restore"):
            reconcile(droplet.plane, "abandon", droplet.host)
        report = reconcile(droplet.plane, "complete", droplet.host)

        assert report == ReconcileReport(
            situation=Situation.reconciled,
            live=droplet.a,
            action="completed",
            triple=triple.describe(),
            admin=droplet.host.socket_admin,
        )
        assert ("systemctl", "reload", "caddy") in droplet.argvs()
        assert read_marker(droplet.releases) == Marker(active=droplet.a, previous=None)

    def test_with_a_marker_the_host_changes_nothing(self, droplet: Droplet) -> None:
        _migrate(droplet)
        droplet.plane.fragment.write_text(droplet.fragment_of(droplet.b), encoding="utf-8")

        with pytest.raises(UnreconciledError) as with_host:
            reconcile(droplet.plane, host=droplet.host)
        with pytest.raises(UnreconciledError) as without:
            reconcile(droplet.plane)
        assert str(with_host.value) == str(without.value)
        assert "D becomes the truth" in str(without.value)

        report = reconcile(droplet.plane, "complete", droplet.host)
        assert report.admin is None and report.live == droplet.b
        assert read_marker(droplet.releases) == Marker(active=droplet.b, previous=droplet.a)
