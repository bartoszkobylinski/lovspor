"""``build-site``, ``site-fixture``, ``release-probe``, ``site-drift-check``.

The operator's route to ADR-0014 builds. A new surface is not shipped
until an operator can reach it through a supported interface
(CLAUDE.md): the site tree, the CI fixture document and the release's
own capability document must all be producible by ``lovspor`` commands
alone, and the document a command writes must be a document the build
command accepts. The drift check is the same probe under the timer's
identity, with the exit codes the unit reports through.

``discover_checkout`` is environment discovery, not logic: it is
monkeypatched to a throwaway checkout here, because the developer's own
work tree is dirty exactly while these tests are being written.
"""

import grp
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import click
import httpx
import pytest
from pydantic import BaseModel, ValidationError
from pytest_httpx import HTTPXMock
from typer.main import get_command
from typer.testing import CliRunner

import lovspor.cli
from lovspor.cli import _first_validation_message, app
from lovspor.publish.emit import emit_site
from lovspor.release.caddy import Completed
from lovspor.site.build import SiteInputs, build_site, require_clean_work_tree
from lovspor.site.capabilities import load_capabilities
from lovspor.site.errors import SiteBuildError
from lovspor.site.fixture import (
    FixtureCase,
    document_bytes,
    expected_checkout,
    synthetic_document,
)
from lovspor.tool_surface import describe_tool_surface
from tests.unit.probe_fixtures import (
    MCP_URL,
    READINESS_URL,
    TOKEN,
    FakeMcp,
    absent_discovery,
    attestation,
    install,
    ready,
    tools_listing,
)
from tests.unit.site_fixtures import run_git, throwaway_checkout, throwaway_corpus

SERVED_URL = "https://lovspor.no/deployment-capabilities.json"
TCP_CONFIG_URL = "http://localhost:2019/config/"
"""The pre-envelope admin address the four facts assert nothing listens on."""

runner = CliRunner()


@pytest.fixture(scope="module")
def repos(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, Path, Path]:
    root = tmp_path_factory.mktemp("cli")
    checkout, commit = throwaway_checkout(root / "lovspor")
    corpus, corpus_commit = throwaway_corpus(root / "lovverk")
    corpus_site = root / "corpus-site"
    emit_site(corpus, corpus_commit, corpus_site)
    return checkout, commit, corpus, corpus_site / "site-manifest.json"


@pytest.fixture
def checkout(repos: tuple[Path, str, Path, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    root = repos[0]
    monkeypatch.setattr(lovspor.cli, "discover_checkout", lambda: root)
    return root


def _build_args(repos: tuple[Path, str, Path, Path], capabilities: Path, out: Path) -> list[str]:
    _, _, corpus, manifest = repos
    return [
        "build-site",
        "--corpus",
        str(corpus),
        "--corpus-manifest",
        str(manifest),
        "--capabilities",
        str(capabilities),
        "--out",
        str(out),
    ]


class TestBuildSiteCommand:
    def test_builds_the_tree_and_names_the_commit_and_page_count(
        self, repos: tuple[Path, str, Path, Path], checkout: Path, tmp_path: Path
    ) -> None:
        _, commit, corpus, _ = repos
        capabilities = tmp_path / "deployment-capabilities.json"
        capabilities.write_bytes(
            document_bytes(synthetic_document(checkout, corpus, FixtureCase.available))
        )
        out = tmp_path / "site"

        result = runner.invoke(app, _build_args(repos, capabilities, out))

        assert result.exit_code == 0, result.output
        assert commit[:12] in result.output
        assert "23 pages" in result.output
        assert (out / "index.html").is_file()
        assert (out / "en" / "status" / "index.html").is_file()
        facts = json.loads((out / "site-facts.json").read_text(encoding="utf-8"))
        assert facts["lovspor_commit"] == commit

    def test_refuses_a_non_empty_output_directory(
        self, repos: tuple[Path, str, Path, Path], checkout: Path, tmp_path: Path
    ) -> None:
        _, _, corpus, _ = repos
        capabilities = tmp_path / "deployment-capabilities.json"
        capabilities.write_bytes(
            document_bytes(synthetic_document(checkout, corpus, FixtureCase.available))
        )
        out = tmp_path / "site"
        out.mkdir()
        (out / "stale.html").write_text("x", encoding="utf-8")

        result = runner.invoke(app, _build_args(repos, capabilities, out))

        assert result.exit_code == 1
        assert "site build refused:" in result.output
        assert "not empty" in result.output

    def test_a_document_for_another_commit_exits_one(
        self, repos: tuple[Path, str, Path, Path], checkout: Path, tmp_path: Path
    ) -> None:
        _, _, corpus, _ = repos
        document = json.loads(
            document_bytes(synthetic_document(checkout, corpus, FixtureCase.available))
        )
        document["state"]["checkout"]["lovspor_commit"] = "f" * 40
        capabilities = tmp_path / "deployment-capabilities.json"
        capabilities.write_text(json.dumps(document), encoding="utf-8")

        result = runner.invoke(app, _build_args(repos, capabilities, tmp_path / "site"))

        assert result.exit_code == 1
        assert "site build refused:" in result.output
        assert not (tmp_path / "site").exists()

    def test_an_absent_document_exits_one(
        self, repos: tuple[Path, str, Path, Path], checkout: Path, tmp_path: Path
    ) -> None:
        result = runner.invoke(app, _build_args(repos, tmp_path / "absent.json", tmp_path / "s"))

        assert result.exit_code == 1
        assert "site build refused:" in result.output

    def test_a_checkout_that_is_not_a_work_tree_exits_one(
        self, repos: tuple[Path, str, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal, not a partial tree (ADR-0014 Decision 1)."""
        monkeypatch.setattr(lovspor.cli, "discover_checkout", lambda: tmp_path / "wheel")
        result = runner.invoke(app, _build_args(repos, tmp_path / "c.json", tmp_path / "site"))

        assert result.exit_code == 1
        assert "not a git work tree" in result.output
        assert not (tmp_path / "site").exists()

    def test_has_no_checkout_option(self) -> None:
        """The checkout is discovered, never named: an option would allow
        foreign-repository provenance (plan F.2)."""
        command = get_command(app)
        assert isinstance(command, click.Group)
        options = {name for param in command.commands["build-site"].params for name in param.opts}

        assert "--checkout" not in options
        assert {"--corpus", "--corpus-manifest", "--capabilities", "--out"} <= options


class TestSiteFixtureCommand:
    @pytest.mark.parametrize("case", list(FixtureCase))
    def test_writes_a_valid_document_for_this_checkout(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        case: FixtureCase,
    ) -> None:
        _, commit, corpus, _ = repos
        out = tmp_path / "deployment-capabilities.json"

        result = runner.invoke(
            app, ["site-fixture", "--corpus", str(corpus), "--case", case.value, "--out", str(out)]
        )

        assert result.exit_code == 0, result.output
        assert case.value in result.output
        assert commit[:12] in result.output
        document = load_capabilities(out)
        assert document.state.checkout.lovspor_commit == commit
        assert out.read_bytes() == document_bytes(synthetic_document(checkout, corpus, case))

    def test_an_unknown_case_is_a_usage_error(
        self, repos: tuple[Path, str, Path, Path], checkout: Path, tmp_path: Path
    ) -> None:
        _, _, corpus, _ = repos
        result = runner.invoke(
            app,
            ["site-fixture", "--corpus", str(corpus), "--case", "made_up", "--out", str(tmp_path)],
        )

        assert result.exit_code == 2
        assert not (tmp_path / "x").exists()

    def test_a_dirty_checkout_is_refused(
        self, repos: tuple[Path, str, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, corpus, _ = repos
        dirty, _ = throwaway_checkout(tmp_path / "dirty")
        (dirty / "note.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(lovspor.cli, "discover_checkout", lambda: dirty)
        out = tmp_path / "deployment-capabilities.json"

        result = runner.invoke(
            app, ["site-fixture", "--corpus", str(corpus), "--case", "available", "--out", str(out)]
        )

        assert result.exit_code == 1
        assert "site fixture refused:" in result.output
        assert not out.exists()

    def test_the_fixture_command_feeds_the_build_command(
        self, repos: tuple[Path, str, Path, Path], checkout: Path, tmp_path: Path
    ) -> None:
        """Operator's question: can the state be reached with supported
        interfaces only? Fixture in, tree out, no hand-written JSON."""
        _, commit, corpus, _ = repos
        capabilities = tmp_path / "deployment-capabilities.json"
        out = tmp_path / "site"
        fixture = runner.invoke(
            app,
            [
                "site-fixture",
                "--corpus",
                str(corpus),
                "--case",
                "served_surface_differs",
                "--out",
                str(capabilities),
            ],
        )
        assert fixture.exit_code == 0, fixture.output

        build = runner.invoke(app, _build_args(repos, capabilities, out))

        assert build.exit_code == 0, build.output
        assert require_clean_work_tree(checkout) == commit
        status = (out / "status" / "index.html").read_text(encoding="utf-8")
        assert (
            'data-fact="hosted.comparisons.transport_surface_match" data-kind="hosted">false'
            in (status)
        )
        assert (out / "deployment-capabilities.json").read_bytes() == capabilities.read_bytes()
        assert run_git(checkout, "status", "--porcelain") == ""


class TestHelp:
    def test_both_commands_are_registered_beside_publish_site(self) -> None:
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        for command in ("publish-site", "build-site", "site-fixture"):
            assert command in result.output


def _probe_args(corpus: Path, out: Path, *extra: str) -> list[str]:
    return [
        "release-probe",
        "--corpus",
        str(corpus),
        "--out",
        str(out),
        "--readiness-url",
        READINESS_URL,
        "--public-mcp-url",
        MCP_URL,
        *extra,
    ]


def _host(httpx_mock: HTTPXMock, corpus: Path) -> FakeMcp:
    """A host serving exactly the checkout's surface, token mode, no discovery."""
    descriptor = describe_tool_surface(corpus)
    ready(
        httpx_mock,
        payload=attestation(
            tool_surface_sha256=descriptor.schema_sha256, tool_count=descriptor.tool_count
        ),
    )
    absent_discovery(httpx_mock)
    fake = FakeMcp(tools_listing(("x", "y")))
    fake.listing = _served_listing(corpus)
    return install(httpx_mock, fake)


def _served_listing(corpus: Path) -> dict[str, object]:
    """The checkout's real ``tools/list`` result, as the SDK puts it on the wire."""
    import asyncio  # noqa: PLC0415

    from mcp.types import ListToolsResult  # noqa: PLC0415

    from lovspor.mcp import build_server  # noqa: PLC0415

    listed = ListToolsResult(tools=asyncio.run(build_server(corpus).list_tools()))
    return listed.model_dump(by_alias=True, mode="json", exclude_none=True)


@pytest.fixture
def credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The probe credential as systemd ``LoadCredential=site-probe:...`` delivers it."""
    directory = tmp_path / "credentials"
    directory.mkdir()
    (directory / "site-probe").write_text(TOKEN + "\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(directory))
    monkeypatch.delenv("LOVSPOR_PROBE_TOKEN_FILE", raising=False)
    return directory / "site-probe"


class TestReleaseProbeCommand:
    def test_writes_the_observed_document_for_this_checkout(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        credential: Path,
    ) -> None:
        _, commit, corpus, _ = repos
        fake = _host(httpx_mock, corpus)
        out = tmp_path / "deployment-capabilities.json"
        before = datetime.now(UTC).replace(microsecond=0)

        result = runner.invoke(app, _probe_args(corpus, out))

        assert result.exit_code == 0, result.output
        document = load_capabilities(out)
        assert document.state.checkout == expected_checkout(checkout, corpus)
        assert document.state.checkout.lovspor_commit == commit
        assert document.state.hosted_state == "available"
        assert document.observation.process.observer == "release-probe"
        assert document.observation.transport.authenticated.outcome == "ok"
        assert fake.methods()[-1] == "tools/list"
        observed_at = document.observation.process.observed_at
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", observed_at)
        assert datetime.fromisoformat(observed_at) >= before
        assert result.stdout == (
            f"capability document for lovspor commit {commit[:12]} written to {out}: "
            "hosted state available, process observed, transport observed, "
            "authenticated observed, discovery absent\n"
        )
        assert TOKEN not in result.output
        assert out.read_bytes() == document_bytes(document)

    def test_the_credential_is_read_from_the_systemd_credentials_directory(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        credential: Path,
    ) -> None:
        _, _, corpus, _ = repos
        fake = _host(httpx_mock, corpus)
        result = runner.invoke(app, _probe_args(corpus, tmp_path / "c.json"))

        assert result.exit_code == 0, result.output
        bearers = {
            r.headers.get("authorization") for r in fake.requests if "authorization" in r.headers
        }
        assert bearers == {f"Bearer {TOKEN}"}

    def test_an_explicit_token_file_wins_over_the_credentials_directory(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        credential: Path,
    ) -> None:
        _, _, corpus, _ = repos
        explicit = tmp_path / "other-token"
        explicit.write_text("lsp_explicit\n", encoding="utf-8")
        fake = _host(httpx_mock, corpus)
        fake.accepted_token = "lsp_explicit"

        result = runner.invoke(
            app, _probe_args(corpus, tmp_path / "c.json", "--probe-token-file", str(explicit))
        )

        assert result.exit_code == 0, result.output
        document = load_capabilities(tmp_path / "c.json")
        assert document.observation.transport.authenticated.outcome == "ok"

    def test_the_token_file_can_be_named_by_the_environment(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _, corpus, _ = repos
        token_file = tmp_path / "env-token"
        token_file.write_text(TOKEN, encoding="utf-8")
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        monkeypatch.setenv("LOVSPOR_PROBE_TOKEN_FILE", str(token_file))
        _host(httpx_mock, corpus)

        result = runner.invoke(app, _probe_args(corpus, tmp_path / "c.json"))

        assert result.exit_code == 0, result.output
        document = load_capabilities(tmp_path / "c.json")
        assert document.observation.transport.authenticated.outcome == "ok"

    @pytest.mark.parametrize("shape", ["absent", "unreadable", "empty"])
    def test_a_credential_that_cannot_be_loaded_is_recorded_not_fatal(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        shape: str,
    ) -> None:
        """A secret that cannot be loaded is ``probe_credential_missing``
        (ADR:901-903): the document is still written, the release proceeds,
        and one line on stderr — never on stdout, which is the summary — says
        why, naming the path and never the content."""
        _, _, corpus, _ = repos
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        monkeypatch.delenv("LOVSPOR_PROBE_TOKEN_FILE", raising=False)
        args = _probe_args(corpus, tmp_path / "c.json")
        if shape == "unreadable":
            token_file = tmp_path / "missing" / "site-probe"
            args += ["--probe-token-file", str(token_file)]
            line = f"probe credential unreadable: {token_file}: No such file or directory"
        elif shape == "empty":
            token_file = tmp_path / "empty"
            token_file.write_text("\n", encoding="utf-8")
            args += ["--probe-token-file", str(token_file)]
            line = f"probe credential empty: {token_file}"
        else:
            line = "probe credential: none configured (step (b) unobserved)"
        fake = _host(httpx_mock, corpus)

        result = runner.invoke(app, args)

        assert result.exit_code == 0, result.output
        document = load_capabilities(tmp_path / "c.json")
        step = document.observation.transport.authenticated
        assert (step.status, step.reason) == ("unobserved", "probe_credential_missing")
        assert document.state.hosted_state == "unknown"
        assert fake.methods() == ["initialize"]
        # Only the probe's own lines: build_server may warn about OPENAI_API_KEY
        # on a box without it, and that line is the server's, not the probe's.
        probe_lines = [
            entry for entry in result.stderr.splitlines() if entry.startswith("probe credential")
        ]
        assert probe_lines == [line]
        assert "probe credential" not in result.stdout

    def test_the_credential_is_read_as_utf_8_whatever_the_process_locale(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        credential: Path,
        c_locale: None,
    ) -> None:
        """A unit started without LANG runs under the C locale; the file is
        still UTF-8, so a non-ASCII space an editor left behind is stripped
        rather than tripping the locale's codec."""
        _, _, corpus, _ = repos
        credential.write_bytes(f"{TOKEN}\u00a0\n".encode())
        _host(httpx_mock, corpus)

        result = runner.invoke(app, _probe_args(corpus, tmp_path / "c.json"))

        assert result.exit_code == 0, result.output
        document = load_capabilities(tmp_path / "c.json")
        assert document.observation.transport.authenticated.outcome == "ok"

    @pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
    def test_a_malformed_utf_8_credential_is_recorded_not_fatal(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An undecodable credential is still a credential that cannot be loaded."""
        _, _, corpus, _ = repos
        token_file = tmp_path / "malformed-token"
        token_file.write_bytes(b"lsp_secret-\xff")
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        _host(httpx_mock, corpus)
        out = tmp_path / "c.json"

        result = runner.invoke(app, _probe_args(corpus, out, "--probe-token-file", str(token_file)))

        assert result.exit_code == 0, result.output
        document = load_capabilities(out)
        step = document.observation.transport.authenticated
        assert (step.status, step.reason) == ("unobserved", "probe_credential_missing")
        assert str(token_file) in result.stderr
        assert "lsp_secret" not in result.output

    def test_a_dirty_checkout_is_refused_before_any_request(
        self,
        repos: tuple[Path, str, Path, Path],
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, _, corpus, _ = repos
        dirty, _ = throwaway_checkout(tmp_path / "dirty")
        (dirty / "note.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(lovspor.cli, "discover_checkout", lambda: dirty)
        out = tmp_path / "c.json"

        result = runner.invoke(app, _probe_args(corpus, out))

        assert result.exit_code == 1
        assert "release probe refused:" in result.output
        assert not out.exists()
        assert not httpx_mock.get_requests()

    def test_a_target_outside_http_is_refused(
        self, repos: tuple[Path, str, Path, Path], checkout: Path, tmp_path: Path
    ) -> None:
        _, _, corpus, _ = repos
        out = tmp_path / "c.json"

        result = runner.invoke(app, _probe_args(corpus, out, "--public-mcp-url", "lovspor.no/mcp"))

        assert result.exit_code == 1
        assert "release probe refused:" in result.output
        assert not out.exists()

    @pytest.mark.parametrize(
        ("transport", "summary"),
        [
            (
                httpx.Response(421),
                "hosted state unavailable, process unobserved (http_502), transport observed, "
                "authenticated unobserved (not_attempted), discovery absent",
            ),
            (
                httpx.ConnectError("no route"),
                "hosted state unknown, process unobserved (http_502), "
                "transport unobserved (network), authenticated unobserved (not_attempted), "
                "discovery unobserved",
            ),
        ],
        ids=["subject-failed", "nothing-observed"],
    )
    def test_the_document_is_written_in_every_observed_state(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        credential: Path,
        transport: httpx.Response | Exception,
        summary: str,
    ) -> None:
        """Hosted state never gates the release (ADR:932-946); the summary
        names each record's status with its reason where there is one."""
        _, commit, corpus, _ = repos
        httpx_mock.add_response(url=READINESS_URL, status_code=502)
        if isinstance(transport, Exception):
            httpx_mock.add_exception(transport, url=MCP_URL)
        else:
            httpx_mock.add_callback(lambda _request: transport, url=MCP_URL)
            absent_discovery(httpx_mock)
        out = tmp_path / "c.json"

        result = runner.invoke(app, _probe_args(corpus, out))

        assert result.exit_code == 0, result.output
        document = load_capabilities(out)
        assert document.observation.process.reason == "http_502"
        assert result.stdout == (
            f"capability document for lovspor commit {commit[:12]} written to {out}: {summary}\n"
        )

    def test_the_probe_feeds_the_build_command(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        credential: Path,
    ) -> None:
        """Operator's question: probe in, tree out, no hand-written JSON."""
        _, commit, corpus, manifest = repos
        _host(httpx_mock, corpus)
        capabilities = tmp_path / "deployment-capabilities.json"
        probe = runner.invoke(app, _probe_args(corpus, capabilities))
        assert probe.exit_code == 0, probe.output

        report = build_site(
            SiteInputs(
                checkout=checkout,
                corpus=corpus,
                corpus_manifest=manifest,
                capabilities=capabilities,
                out=tmp_path / "site",
            )
        )

        assert report.hosted_state == "available"
        assert report.lovspor_commit == commit
        assert (tmp_path / "site" / "deployment-capabilities.json").read_bytes() == (
            capabilities.read_bytes()
        )


SUDO_CALLS: list[tuple[str, ...]] = []
"""What the drift check tried to run as another identity; one list, cleared per run."""


class _RefusingSudo:
    """The unprivileged call as the unit makes it, refused — without running a real ``sudo``."""

    def run(self, argv: Sequence[str], env: Mapping[str, str]) -> Completed:
        del env
        SUDO_CALLS.append(tuple(argv))
        return Completed(7, "", "curl: (7) Couldn't connect to server")


@pytest.fixture
def admin_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
) -> list[str]:
    """A socket holding the four facts, so a drift run reaches its comparison.

    The four facts are the check's first action, so every drift run has to
    get past them; what they assert is pinned in
    ``test_release_admin_socket.py``. The release group is the temporary
    file's own, so the group fact is about the code and not the machine;
    the one boundary that cannot be exercised here is the call made as
    another identity, which is a real ``sudo``.
    """
    socket = tmp_path / "admin.sock"
    socket.touch()
    socket.chmod(0o660)
    try:
        group = grp.getgrgid(socket.stat().st_gid).gr_name
    except KeyError:  # pragma: no cover — a machine whose tmp gid has no group entry
        pytest.skip("the temporary directory's gid has no group entry")
    SUDO_CALLS.clear()
    monkeypatch.setattr(lovspor.cli, "SubprocessRunner", _RefusingSudo)
    httpx_mock.add_response(url="http://127.0.0.1/config/", json={})
    # The fourth fact: the pre-envelope TCP address must refuse, as a closed port does.
    httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=TCP_CONFIG_URL)
    return ["--admin", f"unix/{socket}", "--release-group", group]


def _drift_args(*extra: str) -> list[str]:
    return [
        "site-drift-check",
        "--served-url",
        SERVED_URL,
        "--readiness-url",
        READINESS_URL,
        "--public-mcp-url",
        MCP_URL,
        *extra,
    ]


class TestSiteDriftCheckCommand:
    @pytest.fixture
    def released(
        self,
        repos: tuple[Path, str, Path, Path],
        checkout: Path,
        tmp_path: Path,
        httpx_mock: HTTPXMock,
        credential: Path,
    ) -> tuple[Path, Path, FakeMcp]:
        """The document the release probe wrote for the host, as the site serves it."""
        _, _, corpus, _ = repos
        fake = _host(httpx_mock, corpus)
        document = tmp_path / "served.json"
        result = runner.invoke(app, _probe_args(corpus, document))
        assert result.exit_code == 0, result.output
        httpx_mock.reset()
        return corpus, document, fake

    def _host_again(self, httpx_mock: HTTPXMock, corpus: Path, fake: FakeMcp) -> FakeMcp:
        descriptor = describe_tool_surface(corpus)
        ready(
            httpx_mock,
            payload=attestation(
                tool_surface_sha256=descriptor.schema_sha256, tool_count=descriptor.tool_count
            ),
        )
        absent_discovery(httpx_mock)
        return install(httpx_mock, fake)

    def test_no_drift_exits_zero_with_one_line(
        self,
        released: tuple[Path, Path, FakeMcp],
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        admin_socket: list[str],
    ) -> None:
        corpus, document, fake = released
        httpx_mock.add_response(url=SERVED_URL, content=document.read_bytes())
        self._host_again(httpx_mock, corpus, fake)

        def no_checkout() -> Path:
            raise SiteBuildError("the drift check must not read a checkout")

        monkeypatch.setattr(lovspor.cli, "discover_checkout", no_checkout)
        result = runner.invoke(app, _drift_args(*admin_socket))

        assert result.exit_code == 0, result.output
        assert result.output.count("\n") == 1
        assert result.output.startswith("site-drift-check: no drift ")
        assert "observer=drift-timer" in result.output
        assert TOKEN not in result.output

    def test_drift_exits_one_naming_the_differing_fields(
        self, released: tuple[Path, Path, FakeMcp], httpx_mock: HTTPXMock, admin_socket: list[str]
    ) -> None:
        _, document, fake = released
        httpx_mock.add_response(url=SERVED_URL, content=document.read_bytes())
        ready(httpx_mock, status=503)
        absent_discovery(httpx_mock)
        install(httpx_mock, fake)

        result = runner.invoke(app, _drift_args(*admin_socket))

        assert result.exit_code == 1
        lines = [line for line in result.output.splitlines() if line]
        assert len(lines) == 1
        line = lines[0]
        assert line.startswith("site-drift-check: drift ")
        fields = re.search(r"fields=(\S+)", line)
        assert fields is not None
        assert "hosted_state" in fields.group(1).split(",")
        assert "process.ready" in fields.group(1).split(",")
        assert "observed_at" not in fields.group(1)
        assert "served_state_sha256=" in line
        assert "observed_state_sha256=" in line

    def test_an_unreadable_served_document_exits_three_with_its_reason(
        self, httpx_mock: HTTPXMock, credential: Path, admin_socket: list[str]
    ) -> None:
        httpx_mock.add_response(url=SERVED_URL, status_code=503)

        result = runner.invoke(app, _drift_args(*admin_socket))

        assert result.exit_code == 3
        assert "site-drift-check: served_document_unavailable" in result.stderr
        assert "reason=http_503" in result.stderr
        assert not httpx_mock.get_requests(url=READINESS_URL)

    def test_a_target_outside_http_is_a_usage_error(
        self, httpx_mock: HTTPXMock, credential: Path
    ) -> None:
        """Exit 2 is the command's documented classification for invalid options."""
        result = runner.invoke(
            app,
            _drift_args("--public-mcp-url", "lovspor.no/mcp"),
        )

        assert result.exit_code == 2
        assert (
            "Invalid value: public_mcp_url: not an http(s) URL: 'lovspor.no/mcp'" in result.stderr
        )
        assert not httpx_mock.get_requests()

    def test_a_served_target_outside_http_is_a_usage_error(
        self, credential: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every operator-supplied URL is validated before any I/O — the served
        document's too, with the same classification as the probe targets."""
        called = False

        def forbidden_drift_check(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(lovspor.cli, "checked_drift", forbidden_drift_check)
        result = runner.invoke(
            app,
            _drift_args("--served-url", "lovspor.no/deployment-capabilities.json"),
        )

        assert result.exit_code == 2
        assert "Invalid value: served_url: not an http(s) URL" in result.stderr
        assert called is False

    def test_the_observer_is_the_drift_timer_by_default(
        self, released: tuple[Path, Path, FakeMcp], httpx_mock: HTTPXMock, admin_socket: list[str]
    ) -> None:
        corpus, document, fake = released
        httpx_mock.add_response(url=SERVED_URL, content=document.read_bytes())
        self._host_again(httpx_mock, corpus, fake)
        command = get_command(app)
        assert isinstance(command, click.Group)
        observer = next(
            param
            for param in command.commands["site-drift-check"].params
            if "--observer" in param.opts
        )

        assert observer.default == "drift-timer"
        assert runner.invoke(app, _drift_args(*admin_socket)).exit_code == 0

    def test_exit_codes_are_documented_in_the_help(self) -> None:
        result = runner.invoke(app, ["site-drift-check", "--help"])

        assert result.exit_code == 0
        for code in ("0", "1", "2", "3", "4"):
            assert re.search(rf"\b{code}\b", result.output)
        assert "drift" in result.output
        assert "served document" in result.output

    def test_the_admin_socket_is_the_first_action_and_its_own_exit(
        self, tmp_path: Path, httpx_mock: HTTPXMock, credential: Path
    ) -> None:
        """Exit 4, named, with nothing observed: a broken permission model is not drift."""
        result = runner.invoke(app, _drift_args("--admin", f"unix/{tmp_path / 'gone.sock'}"))

        assert result.exit_code == 4
        assert "admin socket precondition unmet" in result.stderr
        assert str(tmp_path / "gone.sock") in result.stderr
        assert not httpx_mock.get_requests()

    def test_an_admin_address_that_is_not_a_socket_is_a_usage_error(
        self, httpx_mock: HTTPXMock, credential: Path
    ) -> None:
        result = runner.invoke(app, _drift_args("--admin", "localhost:2019"))

        assert result.exit_code == 2
        assert "Unix-socket" in result.stderr
        assert not httpx_mock.get_requests()

    def test_the_call_as_the_other_identity_is_made_as_the_option_names_it(
        self, released: tuple[Path, Path, FakeMcp], httpx_mock: HTTPXMock, admin_socket: list[str]
    ) -> None:
        """Fact four is attempted, and attempted as the identity the unit was told about."""
        corpus, document, fake = released
        httpx_mock.add_response(url=SERVED_URL, content=document.read_bytes())
        self._host_again(httpx_mock, corpus, fake)

        result = runner.invoke(
            app, _drift_args(*admin_socket, "--unprivileged-user", "someone-else")
        )

        assert result.exit_code == 0, result.output
        assert len(SUDO_CALLS) == 1
        assert SUDO_CALLS[0][:4] == ("sudo", "-u", "someone-else", "curl")

    def test_the_socket_options_carry_the_droplets_defaults(self) -> None:
        command = get_command(app)
        assert isinstance(command, click.Group)
        params = {p.name: p for p in command.commands["site-drift-check"].params}

        assert params["admin"].default == "unix//run/caddy/admin.sock"
        assert params["admin"].envvar == "LOVSPOR_CADDY_ADMIN"
        assert params["release_group"].default == "lovspor-release"
        assert params["unprivileged_user"].default == "lovspor"


class TestFirstValidationMessage:
    """The usage line for a refused option, on the shapes pydantic can report."""

    def test_a_nested_location_is_the_dotted_path_to_the_field(self) -> None:
        class Inner(BaseModel):
            seconds: float

        class Outer(BaseModel):
            timeout: Inner

        with pytest.raises(ValidationError) as caught:
            Outer.model_validate({"timeout": {"seconds": "soon"}})

        assert _first_validation_message(caught.value) == (
            f"timeout.seconds: {caught.value.errors()[0]['msg']}"
        )

    def test_a_model_level_error_is_reported_as_the_option_without_pydantics_prefix(
        self,
    ) -> None:
        error = ValidationError.from_exception_data(
            "ProbeSettings",
            [{"type": "value_error", "loc": (), "input": {}, "ctx": {"error": "boom"}}],
        )

        assert error.errors()[0]["msg"] == "Value error, boom"
        assert _first_validation_message(error) == "option: boom"


class TestProbeHelp:
    def test_both_commands_are_registered(self) -> None:
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "release-probe" in result.output
        assert "site-drift-check" in result.output

    def test_the_probe_has_no_checkout_option_and_the_drift_check_no_corpus(self) -> None:
        command = get_command(app)
        assert isinstance(command, click.Group)
        probe_options = {n for p in command.commands["release-probe"].params for n in p.opts}
        drift_options = {n for p in command.commands["site-drift-check"].params for n in p.opts}

        assert "--checkout" not in probe_options
        assert {"--corpus", "--out", "--probe-token-file", "--observer"} <= probe_options
        assert "--corpus" not in drift_options
        assert {
            "--served-url",
            "--probe-token-file",
            "--observer",
            "--admin",
            "--release-group",
            "--unprivileged-user",
        } <= drift_options
