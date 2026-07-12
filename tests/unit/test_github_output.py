"""Tests for lovspor.github_output."""

from pathlib import Path

import pytest

from lovspor.github_output import append_step_summary, set_output


def test_set_output_writes_heredoc_block_when_env_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    set_output("schema_drift_fields", "mimeType,checksum")
    content = out.read_text(encoding="utf-8")
    assert content == "schema_drift_fields<<__EOF__\nmimeType,checksum\n__EOF__\n"


def test_set_output_appends_rather_than_truncates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "gh_output"
    out.write_text("existing=1\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    set_output("k", "v")
    assert out.read_text(encoding="utf-8").startswith("existing=1\n")
    assert "k<<__EOF__\nv\n__EOF__\n" in out.read_text(encoding="utf-8")


def test_set_output_is_noop_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    set_output("k", "v")  # must not raise


def test_append_step_summary_writes_when_env_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    append_step_summary("### note")
    assert summary.read_text(encoding="utf-8") == "### note\n"


def test_append_step_summary_is_noop_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    append_step_summary("### note")  # must not raise
