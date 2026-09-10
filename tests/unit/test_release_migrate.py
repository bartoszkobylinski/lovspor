"""The first migration: preflight, cutover, crash rows (ADR-0014 Migration).

A *droplet* is the pre-envelope host in a box: the old Caddyfile — the
repository's at the merge of #268, verbatim — served by a Caddy whose
admin endpoint is TCP ``localhost:2019``, no fragment, no marker, no
runtime directory, the drop-in provisioning wrote; beside it the new
Caddyfile binding admin to a socket under ``tmp_path`` and importing the
fragment. Two real envelopes are built once. Ownership is a table, the
group's gid the temporary directory's own, so the socket file the fake
creates carries the group the verification asks for.
"""

import grp
import os
import pwd
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, Completed, ConfigPair, adapt, config_pair
from lovspor.release.control import ControlPlane, Situation, live_release, read_triple, situation
from lovspor.release.envelope import (
    FRAGMENT_NAME,
    MARKER_NAME,
    Marker,
    read_fragment,
    read_marker,
    write_marker,
)
from lovspor.release.errors import (
    ControlPlaneError,
    MigrationFailedError,
    MigrationRefusedError,
    UnobservableError,
)
from lovspor.release.migrate import (
    MIGRATION_STEPS,
    PRE_ENVELOPE_DROP_IN,
    MigrationHost,
    MigrationReport,
    Preflight,
    SystemOwnership,
    drop_in_text,
    first_migration,
    preflight,
    socket_path,
)
from tests.unit.caddy_fakes import DEFAULT_TCP, FakeAdmin, FakeCaddy, FakeOwnership, toy_adapt
from tests.unit.release_fixtures import World, build, make_world, observer, rename_document

LATER = "2026-01-02T00:00:00Z"
CADDY_UID = 4242
OLD_CADDYFILE = """\
# lovspor hosted MCP — TLS terminated here, proxied to the localhost-bound app.
#
# {$LOVSPOR_DOMAIN} is read from Caddy's environment (see the caddy.service
# drop-in that provision.sh installs, sourcing /etc/default/caddy-lovspor).
# On a public droplet Caddy AUTOMATICALLY obtains and renews a Let's Encrypt
# certificate for this name on first request, once DNS points the name here —
# no certbot, no cron, no manual cert steps.

{$LOVSPOR_DOMAIN} {
	encode zstd gzip

	# MCP app + health probes + OAuth discovery → the localhost-bound server
	# (TLS stops here).
	@app path /mcp /mcp/* /healthz /readyz /.well-known/oauth-protected-resource /.well-known/oauth-protected-resource/*
	handle @app {
		reverse_proxy 127.0.0.1:8000 {
			header_up Host {upstream_hostport}
		}
	}

	# The published corpus (ADR-0013) → the atomically-switched release symlink.
	@corpus path /lov /lov/* /forskrift /forskrift/* /sitemap.xml /sitemaps/* /robots.txt
	handle @corpus {
		import {$LOVSPOR_SITE_ROOT:/var/www/lovspor-current}/redirects*.caddy
		root * {$LOVSPOR_SITE_ROOT:/var/www/lovspor-current}
		file_server
	}

	# Everything else → the static landing page.
	handle {
		root * /var/www/lovspor
		file_server
	}

	header {
		# HSTS: only meaningful once you're confident the domain is HTTPS-only.
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		-Server
	}

	log {
		output file /var/log/caddy/lovspor.log {
			roll_size 10MiB
			roll_keep 5
		}
	}
}
"""  # noqa: E501 — the repository's Caddyfile at the merge of #268, verbatim
NON_ASCII_OLD_CADDYFILE = "# Ørsta kommune sin side\n" + OLD_CADDYFILE


def new_caddyfile(socket_admin: str, fragment: Path) -> str:
    """The new Caddyfile of the first migration, naming this box's socket and fragment."""
    return (
        "{\n"
        f"\tadmin {socket_admin}|0660\n"
        "}\n"
        "{$LOVSPOR_DOMAIN} {\n"
        "\tencode zstd gzip\n"
        "\t@app path /mcp /mcp/* /healthz /readyz\n"
        "\thandle @app {\n\t\treverse_proxy 127.0.0.1:8000\n\t}\n"
        f"\timport {{$LOVSPOR_RELEASE_FRAGMENT:{fragment}}}\n"
        "\theader {\n\t\tX-Content-Type-Options nosniff\n\t}\n"
        "}\n"
    )


class Killed(Exception):  # noqa: N818 — a simulated process death, not a lovspor error
    """The process died right after the named step."""


def _kill_at(step: str):  # type: ignore[no-untyped-def]
    def checkpoint(reached: str) -> None:
        if reached == step:
            raise Killed(step)

    return checkpoint


class Sabotaged:
    """A runner that answers one command with a canned failure and passes the rest through."""

    def __init__(self, inner: FakeCaddy, prefix: tuple[str, ...], answer: Completed) -> None:
        self.inner = inner
        self.prefix = prefix
        self.answer = answer

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        if tuple(argv[: len(self.prefix)]) == self.prefix:
            self.inner.calls.append((tuple(argv), dict(env)))
            return self.answer
        return self.inner.run(argv, env)


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


class Droplet(NamedTuple):
    plane: ControlPlane
    host: MigrationHost
    caddy: FakeCaddy
    ownership: FakeOwnership
    a: str
    b: str

    @property
    def releases(self) -> Path:
        return self.plane.releases

    @property
    def socket_file(self) -> Path:
        return self.host.socket

    def over(self, address: str) -> ControlPlane:
        """The plane with its admin client bound to ``address``."""
        return replace(self.plane, admin=self.caddy.admin_client(address))

    def fragment_of(self, content_id: str) -> str:
        return read_fragment(self.releases / content_id)

    def pair_of(self, content_id: str) -> ConfigPair:
        """What the new Caddyfile composes with this release's fragment."""
        fragment = self.releases / content_id / FRAGMENT_NAME
        return config_pair(toy_adapt(self.host.caddyfile_source, {FRAGMENT_ENV: str(fragment)}))

    def old_pair(self) -> ConfigPair:
        return config_pair(toy_adapt(Path(str(self.plane.caddyfile) + ".old"), {}))

    def argvs(self) -> list[tuple[str, ...]]:
        return [argv for argv, _ in self.caddy.calls]


@pytest.fixture
def droplet(
    envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Droplet:
    source, a, b = envelopes
    releases = tmp_path / "releases"
    shutil.copytree(source, releases)
    etc = tmp_path / "etc" / "caddy"
    etc.mkdir(parents=True)
    caddyfile = etc / "Caddyfile"
    caddyfile.write_text(OLD_CADDYFILE, encoding="utf-8")
    Path(str(caddyfile) + ".old").write_text(OLD_CADDYFILE, encoding="utf-8")
    fragment = etc / "lovspor-release.caddy"
    runtime_dir = tmp_path / "run" / "caddy"
    socket_admin = f"unix/{runtime_dir / 'admin.sock'}"
    drop_in = tmp_path / "systemd" / "caddy.service.d" / "lovspor.conf"
    drop_in.parent.mkdir(parents=True)
    drop_in.write_text(PRE_ENVELOPE_DROP_IN, encoding="utf-8")
    new = tmp_path / "app" / "Caddyfile"
    new.parent.mkdir()
    new.write_text(new_caddyfile(socket_admin, fragment), encoding="utf-8")
    monkeypatch.setenv("LOVSPOR_DOMAIN", "lovspor.test")
    monkeypatch.delenv("LOVSPOR_RELEASE_FRAGMENT", raising=False)
    caddy = FakeCaddy(caddyfile, drop_in)
    caddy.restart()
    ownership = FakeOwnership({"caddy": CADDY_UID}, {"lovspor-release": tmp_path.stat().st_gid})
    host = MigrationHost(
        caddyfile=caddyfile,
        caddyfile_source=new,
        drop_in=drop_in,
        runtime_dir=runtime_dir,
        socket_admin=socket_admin,
        site_root=tmp_path / "www" / "lovspor",
        current_symlink=tmp_path / "www" / "lovspor-current",
        admin_client=caddy.admin_client,
        ownership=ownership,
    )
    plane = ControlPlane(releases, caddyfile, fragment, caddy, caddy.admin_client(socket_admin))
    return Droplet(plane, host, caddy, ownership, a, b)


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


class TestPreflight:
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

        with pytest.raises(MigrationRefusedError, match="rollback's source"):
            preflight(droplet.plane, droplet.host)

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


def _migrate(droplet: Droplet, checkpoint=None) -> MigrationReport:  # type: ignore[no-untyped-def]
    if checkpoint is None:
        return first_migration(droplet.plane, droplet.host, droplet.a)
    return first_migration(droplet.plane, droplet.host, droplet.a, checkpoint)


class TestFirstMigration:
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
        assert "the socket does not answer" in str(caught.value)
        assert "systemctl restart caddy" in str(caught.value)
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

        assert "over the socket Caddy runs (" in str(caught.value)
        assert f", not {droplet.pair_of(droplet.a).describe()}" in str(caught.value)

    def test_tcp_still_answering_is_named(self, droplet: Droplet) -> None:
        """A TCP client that answers whatever address the instance is on: the listener stayed."""

        def clients(address: str) -> FakeCaddy | FakeAdmin:
            return droplet.caddy if address == DEFAULT_TCP else droplet.caddy.admin_client(address)

        host = replace(droplet.host, admin_client=clients)

        with pytest.raises(MigrationFailedError) as caught:
            first_migration(droplet.plane, host, droplet.a)

        assert str(caught.value).endswith(
            "localhost:2019 still answers; the admin endpoint did not move"
        )

    def test_a_socket_with_the_wrong_mode_or_group_is_named(self, droplet: Droplet) -> None:
        def loosen(step: str) -> None:
            if step == "reloaded":
                droplet.socket_file.chmod(0o666)

        with pytest.raises(MigrationFailedError, match="has mode 0666, not 0660"):
            _migrate(droplet, loosen)

    def test_a_socket_in_another_group_is_named(self, droplet: Droplet) -> None:
        gid = droplet.ownership.groups["lovspor-release"]
        droplet.ownership.groups["lovspor-release"] = gid + 1

        with pytest.raises(MigrationFailedError) as caught:
            _migrate(droplet)

        assert str(caught.value).endswith(f"has gid {gid}, not lovspor-release's {gid + 1}")

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
