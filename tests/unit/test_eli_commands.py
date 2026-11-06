"""``lovspor ops eli-audit`` on a real throwaway git corpus (#819)."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.eli_commands import eli_audit_report
from lovspor.publish.inventory import PublishError
from tests.unit.cli_output import said
from tests.unit.test_publish_emit import corpus  # noqa: F401 — fixture reuse

runner = CliRunner()


def test_the_report_audits_every_published_document_at_the_pinned_commit(
    corpus: tuple[Path, str],  # noqa: F811
) -> None:
    repo, sha = corpus
    report = json.loads(eli_audit_report(repo, sha, sample_size=5))

    assert report["corpus_commit"] == sha
    assert report["total"] == 4
    assert report["by_reason"] == {"mapped": 4}
    assert report["padding_sensitive"] == 4
    assert [s["uri"] for s in report["samples"]["mapped"]] == [
        "https://lovdata.no/eli/forskrift/2021/02/02/2",
        "https://lovdata.no/eli/lov/1933/04/04/4",
        "https://lovdata.no/eli/lov/2020/01/01/1",
        "https://lovdata.no/eli/lov/2022/03/03/3",
    ]
    assert "not resolved" in report["note"]


def test_a_symbolic_ref_is_recorded_as_its_full_sha(
    corpus: tuple[Path, str],  # noqa: F811
) -> None:
    repo, sha = corpus
    assert json.loads(eli_audit_report(repo, "HEAD", sample_size=0))["corpus_commit"] == sha


def test_the_report_is_byte_identical_across_runs(
    corpus: tuple[Path, str],  # noqa: F811
) -> None:
    repo, sha = corpus
    assert eli_audit_report(repo, sha, 2) == eli_audit_report(repo, sha, 2)


def test_an_older_commit_is_audited_despite_newer_and_dirty_corpus_content(
    corpus: tuple[Path, str],  # noqa: F811
) -> None:
    """eli_commands promises one pinned commit, read through git only."""
    repo, sha = corpus
    older = subprocess.run(
        ["git", "rev-parse", f"{sha}^"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # Neither the manifest nor the documents on disk are usable as audit input.
    (repo / "manifest.json").write_text("not JSON", encoding="utf-8")
    (repo / "lover/testloven.md").unlink()

    report = json.loads(eli_audit_report(repo, older, sample_size=3))

    assert report["corpus_commit"] == older
    assert report["total"] == report["distinct"] == 1
    assert report["by_reason"] == {"mapped": 1}
    assert [item["ref_id"] for item in report["samples"]["mapped"]] == ["lov/2020-01-01-1"]
    assert (repo / "manifest.json").read_text(encoding="utf-8") == "not JSON"
    assert not (repo / "lover/testloven.md").exists()


def test_the_audit_writes_nothing_into_the_corpus(
    corpus: tuple[Path, str],  # noqa: F811
) -> None:
    repo, sha = corpus
    before = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    eli_audit_report(repo, sha, 3)
    after = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert before == after


@pytest.mark.parametrize("ref", ["no-such-ref", "HEAD:manifest.json"])
def test_an_unreadable_ref_is_refused(corpus: tuple[Path, str], ref: str) -> None:  # noqa: F811
    repo, _ = corpus
    with pytest.raises(PublishError, match="not a readable corpus commit"):
        eli_audit_report(repo, ref, 1)


def test_a_path_that_is_not_a_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PublishError, match="not a readable corpus commit"):
        eli_audit_report(tmp_path / "missing", "HEAD", 1)


class TestTheCommand:
    def test_zero_samples_keeps_counts_but_omits_examples(
        self,
        corpus: tuple[Path, str],  # noqa: F811
    ) -> None:
        repo, sha = corpus
        result = runner.invoke(
            app, ["ops", "eli-audit", "--corpus", str(repo), "--ref", sha, "--samples", "0"]
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.stdout)
        assert report["total"] == report["distinct"] == 4
        assert report["by_reason"] == {"mapped": 4}
        assert report["samples"] == {"mapped": []}

    def test_negative_samples_is_a_usage_error_before_reading_the_corpus(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "missing-corpus"
        result = runner.invoke(app, ["ops", "eli-audit", "--corpus", str(repo), "--samples", "-1"])
        assert result.exit_code == 2
        assert said("--samples", result.output)
        assert said("-1 is not in the range x>=0", result.output)
        assert not repo.exists()

    def test_prints_the_json_report(self, corpus: tuple[Path, str]) -> None:  # noqa: F811
        repo, sha = corpus
        result = runner.invoke(app, ["ops", "eli-audit", "--corpus", str(repo), "--ref", sha])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["by_reason"] == {"mapped": 4}

    def test_a_bad_ref_exits_one_with_one_line(self, corpus: tuple[Path, str]) -> None:  # noqa: F811
        repo, _ = corpus
        result = runner.invoke(app, ["ops", "eli-audit", "--corpus", str(repo), "--ref", "nope"])
        assert result.exit_code == 1
        assert said("eli audit refused", result.output)
