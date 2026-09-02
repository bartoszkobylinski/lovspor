"""Unit tests for the corpus fetch/update helper.

Exercises real git against a local origin repo (no subprocess mocks) — the
same posture as the sync tests, so clone/pull behaviour is tested for real.
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.corpus_fetch import (
    ATTESTATION_FETCH_REFSPEC,
    CorpusFetchError,
    FetchResult,
    default_corpus_path,
    fetch_corpus,
)
from lovspor.temporal_attestation import (
    ATTESTATION_NOTES_REF,
    TemporalAttestation,
    read_attestation,
    registry_synchronised,
    write_attestation,
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

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    injected_dest = tmp_path / "lovverk;touch injected"
    injected_remote = "https://example.invalid/lovverk.git;touch injected"
    fetch_corpus(injected_dest, repo_url=injected_remote)

    assert [call["args"][0] for call in calls] == [
        ["git", "clone", "--depth", "1", injected_remote, str(injected_dest)],
        ["git", "config", "--get-all", "remote.origin.fetch"],
        ["git", "config", "--add", "remote.origin.fetch", ATTESTATION_FETCH_REFSPEC],
        ["git", "fetch", "origin"],
    ]
    assert all("shell" not in call["kwargs"] for call in calls)
    assert not (tmp_path / "injected").exists()


def test_fetch_result_is_frozen() -> None:
    result = FetchResult(path=Path("corpus"), action="cloned")
    with pytest.raises(ValidationError):
        result.path = Path("other")


# --- full_history (ADR-0003: temporal tools need complete git history) ---


def _second_commit(origin: Path) -> None:
    (origin / "lover").mkdir(exist_ok=True)
    (origin / "lover" / "x.md").write_text("v2\n", encoding="utf-8")
    _git(["add", "-A"], cwd=origin)
    _git(["commit", "-m", "update(lov): x"], cwd=origin)


def _is_shallow_clone(path: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "true"


def _commit_count(path: Path) -> int:
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return len(result.stdout.splitlines())


def test_fetch_corpus_full_history_clones_complete_history(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _make_origin(origin)
    _second_commit(origin)
    dest = tmp_path / "clone"

    result = fetch_corpus(dest, repo_url=_url(origin), full_history=True)

    assert result.action == "cloned"
    assert _is_shallow_clone(dest) is False
    assert _commit_count(dest) == 2


def test_fetch_corpus_default_clone_stays_shallow(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _make_origin(origin)
    _second_commit(origin)
    dest = tmp_path / "clone"

    fetch_corpus(dest, repo_url=_url(origin))

    assert _is_shallow_clone(dest) is True
    assert _commit_count(dest) == 1


def test_fetch_corpus_full_history_deepens_existing_shallow_clone(
    tmp_path: Path,
) -> None:
    """The hosted-deployment upgrade path: an existing --depth 1 checkout is
    deepened in place (fetch --unshallow, additive — no history rewrite),
    after which the whole history is locally available."""
    origin = tmp_path / "origin"
    _make_origin(origin)
    _second_commit(origin)
    dest = tmp_path / "clone"
    fetch_corpus(dest, repo_url=_url(origin))
    assert _is_shallow_clone(dest) is True

    result = fetch_corpus(dest, repo_url=_url(origin), full_history=True)

    assert result.action == "unchanged"  # HEAD did not move; history deepened
    assert _is_shallow_clone(dest) is False
    assert _commit_count(dest) == 2


def test_fetch_corpus_full_history_on_already_full_clone_is_plain_update(
    tmp_path: Path,
) -> None:
    origin = tmp_path / "origin"
    _make_origin(origin)
    dest = tmp_path / "clone"
    fetch_corpus(dest, repo_url=_url(origin), full_history=True)
    _second_commit(origin)

    result = fetch_corpus(dest, repo_url=_url(origin), full_history=True)

    assert result.action == "updated"
    assert _is_shallow_clone(dest) is False
    assert _commit_count(dest) == 2


# --- attestation registry transport (ADR-0012 point 2c) ---


def _origin_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _attest_head(origin: Path) -> str:
    head = _origin_head(origin)
    write_attestation(
        origin,
        TemporalAttestation(
            corpus_commit=head,
            parser_version=1,
            documents_reconciled=1,
            notes_total=0,
            events_total=0,
            attested_at=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
        ),
    )
    return head


def _has_notes_ref(clone: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--quiet", "--verify", ATTESTATION_NOTES_REF],
        cwd=clone,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def test_fetch_corpus_clone_carries_the_attestation_registry(tmp_path: Path) -> None:
    """The review-#230 acceptance test: an attested origin, cloned through
    the actual fetch_corpus API, must serve its proof — never read a real
    remote attestation as a false local absence."""
    origin = tmp_path / "origin"
    _make_origin(origin)
    head = _attest_head(origin)
    dest = tmp_path / "clone"

    fetch_corpus(dest, repo_url=_url(origin))

    assert registry_synchronised(dest)
    assert _has_notes_ref(dest)
    assert read_attestation(dest, head, 1) is not None


def test_fetch_corpus_update_synchronises_a_legacy_clone(tmp_path: Path) -> None:
    """A pre-refspec clone gains the registry on its next supported update."""
    origin = tmp_path / "origin"
    _make_origin(origin)
    dest = tmp_path / "clone"
    _git(["clone", _url(origin), str(dest)], cwd=tmp_path)
    assert not registry_synchronised(dest)

    head = _attest_head(origin)
    fetch_corpus(dest, repo_url=_url(origin))

    assert registry_synchronised(dest)
    assert read_attestation(dest, head, 1) is not None


@pytest.mark.parametrize(
    "misleading_refspec",
    [
        "+refs/heads/attestations:refs/notes/temporal-attestations",
        "+refs/notes/temporal-attestations:refs/heads/attestation-copy",
    ],
)
def test_fetch_corpus_repairs_refspec_that_only_mentions_attestation_ref(
    tmp_path: Path,
    misleading_refspec: str,
) -> None:
    """A legacy clone is synchronised only when both refspec sides cover notes.

    A destination-only or source-only textual mention must not prevent the
    supported fetch path from installing its canonical transport refspec.
    """
    origin = tmp_path / "origin"
    _make_origin(origin)
    head = _attest_head(origin)
    dest = tmp_path / "clone"
    _git(["clone", _url(origin), str(dest)], cwd=tmp_path)
    _git(
        ["config", "--add", "remote.origin.fetch", misleading_refspec],
        cwd=dest,
    )
    assert not registry_synchronised(dest)

    fetch_corpus(dest, repo_url=_url(origin))

    assert registry_synchronised(dest)
    assert read_attestation(dest, head, 1) is not None


def test_fetch_corpus_survives_an_origin_without_the_notes_ref(tmp_path: Path) -> None:
    """Bootstrap safety: the configured refspec must not brick fetch/pull
    against an origin that has no attestation ref yet (the glob form)."""
    origin = tmp_path / "origin"
    _make_origin(origin)
    dest = tmp_path / "clone"

    cloned = fetch_corpus(dest, repo_url=_url(origin))
    updated = fetch_corpus(dest, repo_url=_url(origin))

    assert cloned.action == "cloned"
    assert updated.action == "unchanged"
    assert registry_synchronised(dest)
    assert not _has_notes_ref(dest)


def test_plain_clone_is_not_registry_synchronised(tmp_path: Path) -> None:
    """A clone made outside the supported path must fail closed, not read
    the remote's proof as unattested (ADR-0012 point 2c)."""
    origin = tmp_path / "origin"
    _make_origin(origin)
    _attest_head(origin)
    dest = tmp_path / "clone"
    _git(["clone", _url(origin), str(dest)], cwd=tmp_path)

    assert not registry_synchronised(dest)
