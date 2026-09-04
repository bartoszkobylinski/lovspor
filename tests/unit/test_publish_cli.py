"""The publish-site command: the operator's route to ADR-0013 builds.

A new surface is not shipped until an operator can reach it through a
supported interface — the command must pin the commit explicitly or
resolve HEAD itself, and refuse a non-corpus directory.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from lovspor.cli import app
from tests.unit.test_publish_emit import corpus  # noqa: F401 — fixture reuse

runner = CliRunner()


class TestPublishSiteCommand:
    def test_builds_the_site_at_an_explicit_ref(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        result = runner.invoke(
            app,
            ["publish-site", "--corpus", str(repo), "--ref", sha, "--out", str(out)],
        )

        assert result.exit_code == 0, result.output
        manifest = json.loads((out / "site-manifest.json").read_text())
        assert manifest["corpus_commit"] == sha
        assert sha[:12] in result.output

    def test_head_is_resolved_to_a_full_sha_when_ref_omitted(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        out = tmp_path / "site"
        result = runner.invoke(
            app,
            ["publish-site", "--corpus", str(repo), "--out", str(out)],
        )

        assert result.exit_code == 0, result.output
        manifest = json.loads((out / "site-manifest.json").read_text())
        assert manifest["corpus_commit"] == sha

    def test_refuses_a_directory_that_is_not_a_git_repo(
        self,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(
            app,
            [
                "publish-site",
                "--corpus",
                str(tmp_path / "empty"),
                "--out",
                str(tmp_path / "site"),
            ],
        )

        assert result.exit_code != 0
