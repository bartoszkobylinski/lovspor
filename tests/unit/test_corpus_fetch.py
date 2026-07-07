"""Unit tests for the corpus fetch/update helper.

Exercises real git against a local origin repo (no subprocess mocks) — the
same posture as the sync tests, so clone/pull behaviour is tested for real.
"""

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.corpus_fetch import (
    CorpusFetchError,
    FetchResult,
    default_corpus_path,
    fetch_corpus,
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_origin(path: Path, manifest_body: str = "{}") -> None:
    """Create a real git repo shaped like a lovverk clone (has manifest.json)."""
    path.mkdir(parents=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@example.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "manifest.json").write_text(manifest_body, encoding="utf-8")
    _git(["add", "manifest.json"], cwd=path)
    _git(["commit", "-m", "init corpus"], cwd=path)


def _url(path: Path) -> str:
    # file:// so `git clone --depth 1` is honoured without the local-clone warning.
    return f"file://{path}"


# --- default_corpus_path ---


def test_default_corpus_path_uses_home_cache_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_corpus_path() == tmp_path / ".cache" / "lovverk"


def test_default_corpus_path_honours_xdg_cache_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_corpus_path() == tmp_path / "xdg" / "lovverk"


# --- fetch_corpus ---


def test_fetch_corpus_clones_when_absent(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _make_origin(origin)
    dest = tmp_path / "clone"

    result = fetch_corpus(dest, repo_url=_url(origin))

    assert isinstance(result, FetchResult)
    assert result.action == "cloned"
    assert result.path == dest.resolve()
    assert (dest / "manifest.json").is_file()


def test_fetch_corpus_updates_existing_clone(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _make_origin(origin)
    dest = tmp_path / "clone"
    fetch_corpus(dest, repo_url=_url(origin))

    (origin / "manifest.json").write_text('{"version": 2}', encoding="utf-8")
    _git(["commit", "-am", "update corpus"], cwd=origin)

    result = fetch_corpus(dest, repo_url=_url(origin))

    assert result.action == "updated"
    assert '{"version": 2}' in (dest / "manifest.json").read_text(encoding="utf-8")


def test_fetch_corpus_reports_unchanged_when_already_current(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _make_origin(origin)
    dest = tmp_path / "clone"
    fetch_corpus(dest, repo_url=_url(origin))

    # No upstream change between the two fetches: the pull is a no-op.
    result = fetch_corpus(dest, repo_url=_url(origin))

    assert result.action == "unchanged"


def test_fetch_corpus_refuses_git_repo_that_is_not_lovverk_clone(tmp_path: Path) -> None:
    other_origin = tmp_path / "other-origin"
    _make_origin(other_origin, manifest_body='{"repo": "not lovverk"}')
    dest = tmp_path / "clone"
    _git(["clone", _url(other_origin), str(dest)], cwd=tmp_path)

    with pytest.raises(CorpusFetchError, match="not a lovverk clone"):
        fetch_corpus(dest)

    assert json.loads((dest / "manifest.json").read_text(encoding="utf-8")) == {
        "repo": "not lovverk",
    }


def test_fetch_corpus_refuses_git_repo_without_origin_remote(tmp_path: Path) -> None:
    # A git repo with a manifest but no `origin` remote is not a lovverk clone:
    # reading the remote fails, so fetch must refuse rather than pull or crash.
    dest = tmp_path / "orphan"
    _make_origin(dest, manifest_body='{"repo": "orphan"}')

    with pytest.raises(CorpusFetchError, match="not a lovverk clone"):
        fetch_corpus(dest)

    assert json.loads((dest / "manifest.json").read_text(encoding="utf-8")) == {
        "repo": "orphan",
    }


def test_fetch_corpus_refuses_nonempty_non_clone_dir(tmp_path: Path) -> None:
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "random.txt").write_text("not a corpus", encoding="utf-8")

    with pytest.raises(CorpusFetchError, match="refusing to overwrite"):
        fetch_corpus(dest, repo_url="file:///nonexistent")

    assert (dest / "random.txt").is_file()  # untouched


def test_fetch_corpus_clones_into_existing_empty_dir(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _make_origin(origin)
    dest = tmp_path / "empty"
    dest.mkdir()

    result = fetch_corpus(dest, repo_url=_url(origin))

    assert result.action == "cloned"
    assert (dest / "manifest.json").is_file()


def test_fetch_corpus_raises_corpusfetcherror_on_bad_remote(tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    with pytest.raises(CorpusFetchError):
        fetch_corpus(dest, repo_url=str(tmp_path / "does-not-exist"))


def test_fetch_corpus_uses_git_argv_without_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=["git"], returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    injected_dest = tmp_path / "lovverk;touch injected"
    injected_remote = "https://example.invalid/lovverk.git;touch injected"
    fetch_corpus(injected_dest, repo_url=injected_remote)

    assert calls == [
        {
            "args": (["git", "clone", "--depth", "1", injected_remote, str(injected_dest)],),
            "kwargs": {
                "cwd": tmp_path,
                "capture_output": True,
                "text": True,
                "check": True,
            },
        },
    ]
    assert not (tmp_path / "injected").exists()


def test_fetch_result_is_frozen() -> None:
    result = FetchResult(path=Path("corpus"), action="cloned")
    with pytest.raises(ValidationError):
        result.path = Path("other")
