"""The pre-envelope host in a box, shared by the migration and CLI tests (ADR-0014 Migration).

A *droplet* is the existing box as the first migration finds it: the old
Caddyfile — the repository's at the merge of #268, verbatim — served by
a Caddy whose admin endpoint is TCP ``localhost:2019``, no fragment, no
marker, no runtime directory, the drop-in provisioning wrote; beside it
the new Caddyfile binding admin to a socket under ``tmp_path`` and
importing the fragment. Ownership is a table, the group's gid the
temporary directory's own, so the socket file the fake creates carries
the group the verification asks for.
"""

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, Completed, ConfigPair, config_pair
from lovspor.release.control import ControlPlane
from lovspor.release.envelope import FRAGMENT_NAME, read_fragment
from lovspor.release.migrate import PRE_ENVELOPE_DROP_IN, MigrationHost
from tests.unit.caddy_fakes import FakeCaddy, FakeOwnership, toy_adapt

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


Answer = Completed | Callable[[Sequence[str], Mapping[str, str]], Completed]


class Sabotaged:
    """A runner that answers one command with a canned result — or a hook's — and passes the
    rest through."""

    def __init__(self, inner: FakeCaddy, prefix: tuple[str, ...], answer: Answer) -> None:
        self.inner = inner
        self.prefix = prefix
        self.answer = answer

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        if tuple(argv[: len(self.prefix)]) != self.prefix:
            return self.inner.run(argv, env)
        if isinstance(self.answer, Completed):
            self.inner.calls.append((tuple(argv), dict(env)))
            return self.answer
        return self.answer(argv, env)


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


def make_droplet(
    envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Droplet:
    """The droplet under ``tmp_path``, with copies of the two envelopes."""
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
