"""Raw captures and crosswalk outputs never land in a Git checkout or in Lovverk (D2)."""

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk import capture as cap
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.paths import new_cache, real_directory, writable_target
from lovspor.sources.lovdata import LovdataArchive, LovdataClient

LAW = "LOV-2022-06-17-63"
COMMIT = "a" * 40


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _clone(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    return root


def _worktree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    return root


def _lovverk(root: Path) -> Path:
    (root / "lover").mkdir(parents=True)
    (root / "manifest.json").write_text("{}")
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A pacer log outside every tree under test, so it never counts as a created file."""
    return tmp_path / "pacer"


def _refused_capture(cache: Path, outside: Path, match: str) -> None:
    outside.mkdir(exist_ok=True)
    sources = cap.Sources(pacer=cap.Pacer(outside / "progress.log", gap=0))
    with pytest.raises(CrosswalkError, match=match):
        cap.capture(cache, [LAW], COMMIT, sources)


@pytest.mark.parametrize("make", [_clone, _worktree])
def test_capture_refuses_a_cache_inside_a_git_checkout(
    tmp_path: Path, outside: Path, httpx_mock: HTTPXMock, make: object
) -> None:
    repo = make(tmp_path / "repo")  # type: ignore[operator]
    before = _tree(repo)
    _refused_capture(repo / "data" / "cache" / "run", outside, "Git checkout")
    assert _tree(repo) == before
    assert not httpx_mock.get_requests()


def test_capture_refuses_a_cache_inside_lovverk(
    tmp_path: Path, outside: Path, httpx_mock: HTTPXMock
) -> None:
    corpus = _lovverk(tmp_path / "lovverk")
    before = _tree(corpus)
    _refused_capture(corpus / "crosswalk", outside, "Lovverk")
    assert _tree(corpus) == before


def test_capture_refuses_the_configured_lovverk_corpus(
    tmp_path: Path, outside: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    monkeypatch.setenv("LOVVERK_CORPUS_PATH", str(corpus))
    _refused_capture(corpus / "run", outside, "Lovverk")
    assert _tree(corpus) == []


def test_capture_refuses_a_symlink_that_escapes_into_a_checkout(
    tmp_path: Path, outside: Path, httpx_mock: HTTPXMock
) -> None:
    repo = _clone(tmp_path / "repo")
    (repo / "data").mkdir()
    link = tmp_path / "innocent"
    link.symlink_to(repo / "data")
    _refused_capture(link / "run", outside, "Git checkout")
    assert _tree(repo) == [".git", "data"]


def test_capture_refuses_a_symlinked_cache_directory(
    tmp_path: Path, outside: Path, httpx_mock: HTTPXMock
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "cache"
    link.symlink_to(real)
    _refused_capture(link, outside, "symlink")
    assert _tree(real) == []


def test_capture_refuses_a_non_empty_directory(
    tmp_path: Path, outside: Path, httpx_mock: HTTPXMock
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "stray.bin").write_bytes(b"x")
    _refused_capture(cache, outside, "not empty")
    assert _tree(cache) == ["stray.bin"]


def test_capture_accepts_an_empty_directory_outside_any_checkout(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    assert writable_target(cache, "capture cache") == cache.resolve()


@pytest.mark.parametrize("marker", ["manifest.json", "lover"])
def test_one_lovverk_marker_alone_is_not_the_corpus(tmp_path: Path, marker: str) -> None:
    """Lovverk is the manifest and the lover/ tree together; either alone is ordinary."""
    root = tmp_path / "project"
    root.mkdir()
    if marker == "lover":
        (root / marker).mkdir()
    else:
        (root / marker).write_text("{}")
    target = root / "out.jsonl"
    assert writable_target(target, "crosswalk output") == target.resolve()


def test_writable_target_refuses_before_creating_anything(tmp_path: Path) -> None:
    repo = _worktree(tmp_path / "wt")
    with pytest.raises(CrosswalkError) as caught:
        writable_target(repo / "a" / "b" / "out.jsonl", "crosswalk output")
    message = str(caught.value)
    assert message.startswith("refusing to write the crosswalk output inside the Git checkout ")
    assert _tree(repo) == [".git"]


def test_a_symlinked_staging_directory_is_refused(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    cache = tmp_path / "cache"
    (cache / "lovdata").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (cache / "lovdata" / ".staging").symlink_to(elsewhere)
    entry = LovdataArchive.model_validate(
        {
            "filename": "lovtidend-avd1-2026.tar.bz2",
            "description": "d",
            "sizeBytes": 10,
            "lastModified": "2026-10-01T00:00:00Z",
        }
    )
    with LovdataClient() as client, pytest.raises(CrosswalkError, match="staging"):
        cap.capture_archive(client, entry, cache)
    assert _tree(elsewhere) == []
    assert not httpx_mock.get_requests()


def test_staging_reached_through_a_symlinked_parent_is_refused(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (cache / "lovdata").symlink_to(elsewhere)
    with pytest.raises(CrosswalkError) as caught:
        real_directory(cache / "lovdata" / ".staging", cache)
    assert str(caught.value) == (
        f"refusing an unsafe staging directory: {cache / 'lovdata' / '.staging'}"
    )


def test_staging_inside_the_cache_is_created_and_returned(tmp_path: Path) -> None:
    staging = tmp_path / "cache" / "lovdata" / ".staging"
    assert real_directory(staging, tmp_path / "cache") == staging
    assert staging.is_dir()


def test_staging_inside_a_checkout_is_refused_before_it_is_created(tmp_path: Path) -> None:
    repo = _clone(tmp_path / "repo")
    with pytest.raises(CrosswalkError, match="staging directory inside the Git checkout"):
        real_directory(repo / "cache" / ".staging", repo / "cache")
    assert _tree(repo) == [".git"]


def test_refusal_messages_name_the_owner(tmp_path: Path) -> None:
    corpus = _lovverk(tmp_path / "lovverk")
    with pytest.raises(CrosswalkError) as caught:
        new_cache(corpus / "x")
    assert str(caught.value) == (
        f"refusing to write the capture cache inside the Lovverk corpus {corpus.resolve()}: "
        f"{corpus / 'x'}"
    )
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f").write_bytes(b"")
    with pytest.raises(CrosswalkError) as caught:
        new_cache(tmp_path / "full")
    assert str(caught.value) == (
        f"capture cache is not empty; use a new directory: {tmp_path / 'full'}"
    )
    assert new_cache(tmp_path / "fresh") == (tmp_path / "fresh").resolve()
    assert not (tmp_path / "fresh").exists()
