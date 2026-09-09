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
from pytest_httpx import HTTPXMock
from typer.main import get_command
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.release import commands
from lovspor.release.control import ControlPlane
from lovspor.release.envelope import FRAGMENT_NAME, Marker, read_fragment, read_marker, write_marker
from tests.unit.caddy_fakes import FakeCaddy
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
PLACEHOLDER = "handle {\n\troot * /var/www/lovspor\n\tfile_server\n}\n"
_REPO = Path(__file__).resolve().parents[2]


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
        assert result.stdout.endswith(f"live: {host.a}\n")

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
        assert "--live must be a release_content_id or 'none'" in result.output


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


class TestPackage:
    def test_the_release_group_is_registered(self) -> None:
        root = get_command(app)
        assert isinstance(root, click.Group)
        group = root.commands["release"]
        assert isinstance(group, click.Group)

        assert {"build", "live", "commit", "reconcile", "rollback", "prune"} <= set(group.commands)

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
