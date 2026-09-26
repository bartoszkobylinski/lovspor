"""A control-plane host in a box, shared by the control and contract tests (ADR-0014 Decision 6).

Two real envelopes, A and B, are built once per module; every test gets
its own copy of the releases root and its own ``caddy_fakes.FakeCaddy``:
a Caddyfile importing the active fragment, a toy adapter over the real
files, the admin endpoint and systemctl.
"""

import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from lovspor.release.caddy import FRAGMENT_ENV, ConfigPair, config_pair
from lovspor.release.control import ControlPlane
from lovspor.release.envelope import (
    FRAGMENT_NAME,
    Marker,
    fragment_paths,
    read_fragment,
    write_marker,
)
from tests.unit.caddy_fakes import FakeCaddy, toy_adapt
from tests.unit.release_fixtures import (
    World,
    build,
    files,
    observer,
    rename_document,
)

LATER = "2026-01-02T00:00:00Z"
PLACEHOLDER = "handle {\n\troot * /var/www/lovspor\n\tfile_server\n}\n"
"""An active fragment from before any envelope release: no ``vars``, no release."""


def two_envelopes(world: World, releases: Path) -> tuple[Path, str, str]:
    """Two finalized envelopes under one releases root: A, then B after a corpus rename."""
    a = build(world, releases).release_content_id
    rename_document(world)
    b = build(world, releases, observe=observer(LATER)).release_content_id
    assert a != b
    return releases, a, b


class Host(NamedTuple):
    plane: ControlPlane
    caddy: FakeCaddy
    a: str
    b: str

    @property
    def releases(self) -> Path:
        return self.plane.releases

    def fragment_of(self, content_id: str) -> str:
        return read_fragment(self.releases / content_id)

    def root_of(self, content_id: str) -> str:
        """The immutable directory the release's own fragment names."""
        return fragment_paths(self.fragment_of(content_id))[1].removesuffix("/corpus")

    def pair_of(self, content_id: str) -> ConfigPair:
        """What the composed configuration adapts to with this release's fragment, installed at
        the active fragment's path — the path Caddy hides it by (#317)."""
        fragment = self.releases / content_id / FRAGMENT_NAME
        env = {FRAGMENT_ENV: str(fragment)}
        installed = {fragment: self.plane.fragment}
        return config_pair(toy_adapt(self.plane.caddyfile, env, imported_as=installed))

    def running(self) -> ConfigPair:
        return config_pair(self.caddy.running_config())

    def make_live(self, content_id: str, previous: str | None = None) -> None:
        """The end state of a completed transaction, written directly."""
        self.plane.fragment.write_text(self.fragment_of(content_id), encoding="utf-8")
        self.caddy.restart()
        write_marker(self.releases, Marker(active=content_id, previous=previous))

    def snapshot(self) -> dict[str, bytes]:
        etc = files(self.plane.fragment.parent)
        return {**files(self.releases), **{f"etc/{name}": data for name, data in etc.items()}}


def _caddyfile(fragment: Path) -> str:
    return (
        "{$LOVSPOR_DOMAIN:lovspor.test} {\n"
        "\tencode zstd gzip\n"
        "\t@app path /mcp /mcp/* /healthz /readyz\n"
        "\thandle @app {\n\t\treverse_proxy 127.0.0.1:8000\n\t}\n"
        f"\timport {{$LOVSPOR_RELEASE_FRAGMENT:{fragment}}}\n"
        "\theader {\n\t\tX-Content-Type-Options nosniff\n\t}\n"
        "}\n"
    )


def make_host(
    envelopes: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Host:
    """A fresh host serving the placeholder: nothing live, no marker."""
    source, a, b = envelopes
    releases = tmp_path / "releases"
    shutil.copytree(source, releases)
    etc = tmp_path / "etc" / "caddy"
    etc.mkdir(parents=True)
    fragment = etc / "lovspor-release.caddy"
    caddyfile = etc / "Caddyfile"
    caddyfile.write_text(_caddyfile(fragment), encoding="utf-8")
    monkeypatch.delenv("LOVSPOR_RELEASE_FRAGMENT", raising=False)
    fragment.write_text(PLACEHOLDER, encoding="utf-8")
    caddy = FakeCaddy(caddyfile)
    caddy.restart()
    plane = ControlPlane(releases, caddyfile, fragment, runner=caddy, admin=caddy)
    return Host(plane, caddy, a, b)
