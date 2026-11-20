"""The PR's read-only baseline capture must reproduce a pinned snapshot."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from lovspor.publish.redirects import RedirectMap, caddy_snippet, redirect_map_json
from tests.unit.test_publish_redirects import _doc, _git, _head, _record, _repo, _write_manifest


@pytest.fixture
def script() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/quality/capture_add_only_baseline.py"
    spec = importlib.util.spec_from_file_location("capture_add_only_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Path, str]:
    repo = _repo(tmp_path)
    (repo / "lover/prøveloven.md").write_text(
        _doc("Prøveloven", "lov/2020-01-01-1", ("1",)), encoding="utf-8"
    )
    _write_manifest(
        repo,
        {
            "live": _record("lov", "lover/prøveloven.md", "prøveloven"),
            "retired": _record("forskrift", "forskrifter/utgått.md", "utgått", "removed"),
        },
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pinned")
    sha = _head(repo)
    # A later commit and dirty worktree must not leak into the pinned capture.
    _write_manifest(repo, {})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "later", day=2)
    (repo / "manifest.json").write_text("dirty worktree", encoding="utf-8")
    return repo, sha


def test_capture_uses_pinned_commit_and_leaves_corpus_untouched(
    script: ModuleType, corpus: tuple[Path, str]
) -> None:
    repo, sha = corpus
    before = {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    first = script.capture(repo, sha)
    second = script.capture(repo, sha)
    assert first == second
    assert first["lovverk_commit"] == sha
    assert first["current"] == {
        "live": {"doc_type": "lov", "slug": "prøveloven", "url": "/lov/prøveloven/"}
    }
    assert first["removed"] == {"retired": {"doc_type": "forskrift", "slug": "utgått"}}
    expected = RedirectMap(redirects=(), gone=("/forskrift/utgått/",))
    assert first["redirects"] == {
        "count_301": 0,
        "count_410": 1,
        "redirect_map_json_sha256": hashlib.sha256(redirect_map_json(expected)).hexdigest(),
        "caddy_snippet_sha256": hashlib.sha256(caddy_snippet(expected)).hexdigest(),
    }
    assert {p.relative_to(repo): p.read_bytes() for p in repo.rglob("*") if p.is_file()} == before


def test_render_is_sorted_utf8_json_with_one_final_newline(script: ModuleType) -> None:
    assert script.render({"z": "æøå", "a": {"b": 1}}) == (
        '{\n "a": {\n  "b": 1\n },\n "z": "æøå"\n}\n'
    )


@pytest.mark.parametrize("mode", ["stdout", "match", "different"])
def test_main_emits_or_checks_exact_baseline_without_overwriting(
    script: ModuleType,
    corpus: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    repo, sha = corpus
    expected = script.render(script.capture(repo, sha))
    args = ["capture", "--corpus", str(repo), "--sha", sha]
    baseline = tmp_path / "baseline.json"
    # Semantically identical JSON still differs byte for byte.
    original = expected if mode == "match" else json.dumps(json.loads(expected))
    if mode != "stdout":
        baseline.write_text(original, encoding="utf-8")
        args += ["--check", str(baseline)]
    monkeypatch.setattr(sys, "argv", args)
    assert script.main() == (1 if mode == "different" else 0)
    output = capsys.readouterr()
    assert output.err == ""
    assert (
        output.out
        == {
            "stdout": expected,
            "match": "baseline reproduced\n",
            "different": "baseline DIFFERS from the engine's output\n",
        }[mode]
    )
    if mode != "stdout":
        assert baseline.read_text(encoding="utf-8") == original


def test_capture_counts_and_hashes_rename_redirects(script: ModuleType, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "lover/old.md").write_text(_doc("Test", "lov/2020-01-01-1", ("1",)), encoding="utf-8")
    _write_manifest(repo, {"live": _record("lov", "lover/old.md", "old")})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "old")
    _git(repo, "mv", "lover/old.md", "lover/new.md")
    _write_manifest(repo, {"live": _record("lov", "lover/new.md", "new")})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "rename", day=2)
    expected = RedirectMap(
        redirects=(
            ("/lov/old/", "/lov/new/"),
            ("/lov/old/index.json", "/lov/new/index.json"),
            ("/lov/old/paragraf/1/", "/lov/new/paragraf/1/"),
            ("/lov/old/paragraf/1/index.json", "/lov/new/paragraf/1/index.json"),
        ),
        gone=("/lov/old/",),
    )
    captured = script.capture(repo, _head(repo))
    assert captured["redirects"] == {
        "count_301": 4,
        "count_410": 1,
        "redirect_map_json_sha256": hashlib.sha256(redirect_map_json(expected)).hexdigest(),
        "caddy_snippet_sha256": hashlib.sha256(caddy_snippet(expected)).hexdigest(),
    }
