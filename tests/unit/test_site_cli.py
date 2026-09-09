"""``build-site`` and ``site-fixture``: the operator's route to ADR-0014 builds.

A new surface is not shipped until an operator can reach it through a
supported interface (CLAUDE.md): the site tree and the CI fixture
document must both be producible by ``lovspor`` commands alone, and the
fixture a command writes must be a document the build command accepts.

``discover_checkout`` is environment discovery, not logic: it is
monkeypatched to a throwaway checkout here, because the developer's own
work tree is dirty exactly while these tests are being written.
"""

import json
from pathlib import Path

import click
import pytest
from typer.main import get_command
from typer.testing import CliRunner

import lovspor.cli
from lovspor.cli import app
from lovspor.publish.emit import emit_site
from lovspor.site.build import require_clean_work_tree
from lovspor.site.capabilities import load_capabilities
from lovspor.site.fixture import FixtureCase, document_bytes, synthetic_document
from tests.unit.site_fixtures import run_git, throwaway_checkout, throwaway_corpus

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
