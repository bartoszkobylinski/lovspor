"""Integrity checks for the committed post-hoc supplementary report."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.llhb.posthoc import PosthocGuardError

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "benchmarks" / "llhb" / "runner" / "posthoc_supplementary.py"
REPORT_JSON = (
    ROOT
    / "benchmarks"
    / "llhb"
    / "results"
    / "reports"
    / "opus-frozen-pair-posthoc-supplementary-v1.json"
)
REPORT_MD = REPORT_JSON.with_suffix(".md")


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("llhb_posthoc_supplementary", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_markdown_is_deterministically_rendered_from_committed_json() -> None:
    module = _load_script()
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))

    first = module.render_markdown(report)
    second = module.render_markdown(report)

    assert first == second
    assert first == REPORT_MD.read_text(encoding="utf-8")


def test_markdown_distinguishes_proportions_from_instance_volumes() -> None:
    module = _load_script()
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))

    rendered = module.render_markdown(report)

    assert "| citation coverage | 215 | 86.0% | 249 | 99.6% |" in rendered
    assert (
        "| valid citation instances | 128 | 0.51 per answer | 431 | 1.72 per answer |" in rendered
    )
    assert "172.4%" not in rendered


def test_custom_output_path_applies_to_both_report_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI's output override must keep the JSON and Markdown together."""
    module = _load_script()
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    custom_json = tmp_path / "custom-report.json"
    default_markdown = tmp_path / "default-report.md"
    monkeypatch.setattr(module, "build_report", lambda corpus_path, runs_root: report)
    monkeypatch.setattr(module, "OUT_MD", default_markdown)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--corpus-path",
            str(tmp_path / "corpus"),
            "--out",
            str(custom_json),
        ],
    )

    assert module.main() == 0
    assert custom_json.exists()
    assert custom_json.with_suffix(".md").read_text(encoding="utf-8") == module.render_markdown(
        report
    )
    assert not default_markdown.exists()


def test_guard_refusal_happens_before_dataset_or_corpus_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The byte-identity gate must run before any input is aggregated."""
    module = _load_script()

    def refuse(*args: object) -> None:
        raise PosthocGuardError("wrong frozen pair")

    def unexpected(*args: object) -> None:
        pytest.fail("read or scoring started before the frozen-pair guard passed")

    monkeypatch.setattr(module, "verify_frozen_pair", refuse)
    monkeypatch.setattr(module, "load_cases_jsonl", unexpected)

    with pytest.raises(PosthocGuardError, match="wrong frozen pair"):
        module.build_report(tmp_path / "corpus", tmp_path / "runs")


def test_main_refusal_writes_neither_report_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_script()
    output = tmp_path / "supplementary.json"

    def refuse(*args: object) -> None:
        raise PosthocGuardError("wrong frozen pair")

    monkeypatch.setattr(module, "build_report", refuse)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--corpus-path", str(tmp_path / "corpus"), "--out", str(output)],
    )

    assert module.main() == 1
    assert capsys.readouterr().err == "refused: wrong frozen pair\n"
    assert not output.exists()
    assert not output.with_suffix(".md").exists()
