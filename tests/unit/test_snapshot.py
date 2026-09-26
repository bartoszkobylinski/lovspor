"""Tests for lovspor.snapshot — global corpus-state resolution (ADR-0011)."""

import json
import os
import subprocess
import tarfile
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest

import lovspor.snapshot as snapshot_module
from lovspor.errors import AmbiguousSlugError, ParseError
from lovspor.snapshot import (
    CorpusSnapshot,
    CorpusStateRef,
    HistoryBoundaryError,
    StateIntegrityError,
    resolve_corpus_state,
)
from lovspor.timetravel import ShallowHistoryError

# ---------- CorpusStateRef ----------


def test_corpus_state_ref_is_frozen() -> None:
    ref = CorpusStateRef("sha", datetime(2026, 5, 1, tzinfo=UTC))

    with pytest.raises(FrozenInstanceError):
        ref.sha = "other"


# ---------- resolve_corpus_state (log walk, monkeypatched) ----------


def _refs(*pairs: tuple[str, datetime]) -> list[CorpusStateRef]:
    return [CorpusStateRef(sha, dt) for sha, dt in pairs]


def test_resolve_picks_newest_commit_at_or_before_end_of_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cutoff_day = date(2026, 4, 27)
    edge = datetime.combine(cutoff_day, time.max).replace(tzinfo=UTC)
    entries = _refs(
        ("newer", datetime(2026, 4, 28, tzinfo=UTC)),
        ("edge", edge),
        ("older", datetime(2026, 4, 26, tzinfo=UTC)),
    )
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: entries)

    ref = resolve_corpus_state(tmp_path, cutoff_day)

    assert ref.sha == "edge"


def test_resolve_compares_offset_author_dates_as_utc_instants(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries = _refs(
        (
            "displayed-next-day-but-within-cutoff",
            datetime(2026, 5, 2, 0, 30, tzinfo=timezone(timedelta(hours=14))),
        ),
        (
            "displayed-cutoff-day-but-after-cutoff",
            datetime(2026, 5, 1, 23, 30, tzinfo=timezone(-timedelta(hours=12))),
        ),
    )
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: entries)

    ref = resolve_corpus_state(tmp_path, date(2026, 5, 1))

    assert ref.sha == "displayed-next-day-but-within-cutoff"


def test_resolve_pre_history_date_raises_boundary_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries = _refs(("start", datetime(2026, 4, 22, tzinfo=UTC)))
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: entries)
    monkeypatch.setattr("lovspor.snapshot._is_shallow_repository", lambda *_: False)

    with pytest.raises(HistoryBoundaryError) as excinfo:
        resolve_corpus_state(tmp_path, date(2026, 4, 21))

    # The boundary outcome names the corpus start so a caller can relay it.
    assert "2026-04-22" in str(excinfo.value)


def test_resolve_on_shallow_clone_raises_shallow_not_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries = _refs(("clone-edge", datetime(2026, 6, 1, tzinfo=UTC)))
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: entries)
    monkeypatch.setattr("lovspor.snapshot._is_shallow_repository", lambda *_: True)

    with pytest.raises(ShallowHistoryError):
        resolve_corpus_state(tmp_path, date(2026, 5, 1))


def test_resolve_passes_repository_and_full_boundary_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries: list[CorpusStateRef] = []
    monkeypatch.setattr(snapshot_module, "_iter_state_log", lambda path: entries)
    seen: list[tuple[Path, datetime, list[CorpusStateRef]]] = []

    def boundary(path: Path, cutoff: datetime, refs: list[CorpusStateRef]) -> Exception:
        seen.append((path, cutoff, refs))
        return RuntimeError("boundary")

    monkeypatch.setattr(snapshot_module, "_pre_history_error", boundary)
    with pytest.raises(RuntimeError, match="boundary"):
        resolve_corpus_state(tmp_path, date(2026, 5, 1))
    assert seen == [
        (tmp_path, datetime.combine(date(2026, 5, 1), time.max).replace(tzinfo=UTC), entries),
    ]


@pytest.mark.parametrize(
    ("shallow", "entries", "expected"),
    [
        (True, [], "no locally available history"),
        (
            True,
            _refs(("old", datetime(2026, 4, 22, tzinfo=UTC))),
            "locally available history begins 2026-04-22",
        ),
        (False, [], "corpus history begins unknown"),
    ],
)
def test_pre_history_error_preserves_actionable_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shallow: bool,
    entries: list[CorpusStateRef],
    expected: str,
) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(
        snapshot_module,
        "_is_shallow_repository",
        lambda path: seen.append(path) or shallow,
    )
    error = snapshot_module._pre_history_error(
        tmp_path, datetime(2026, 4, 21, 23, 59, tzinfo=UTC), entries
    )
    assert seen == [tmp_path]
    assert "2026-04-21" in str(error)
    assert expected in str(error)
    if shallow:
        assert "git fetch --unshallow" in str(error)


def test_iter_state_log_checks_git_and_skips_malformed_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = "__COMMIT__\nmalformed\n__COMMIT__\nsha-2\n2026-05-02T12:00:00+00:00\n"
    seen: dict[str, object] = {}

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout=output)

    monkeypatch.setattr(snapshot_module.subprocess, "run", run)
    assert snapshot_module._iter_state_log(tmp_path) == [
        CorpusStateRef("sha-2", datetime(2026, 5, 2, 12, tzinfo=UTC))
    ]
    assert seen["cwd"] == tmp_path
    assert seen["capture_output"] is True
    assert seen["text"] is True
    assert seen["check"] is True


# ---------- real-git fixture ----------


def _run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )


def _commit_all(repo: Path, message: str, iso_date: str) -> str:
    _run_git(repo, "add", "-A")
    stamp = {"GIT_AUTHOR_DATE": iso_date, "GIT_COMMITTER_DATE": iso_date}
    _run_git(repo, "commit", "-m", message, env=stamp)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _manifest_payload(body_rel: str, *, slug: str = "testloven") -> dict[str, object]:
    return {
        "version": 1,
        "generated_at": "2026-04-22T04:00:00Z",
        "documents": {
            "doc-1": {
                "doc_type": "lov",
                "xml_hash": "hash-1",
                "markdown_path": body_rel,
                "source_dataset": "gjeldende-lover",
                "last_seen": "2026-04-22T04:00:00Z",
                "status": "current",
                "slug": slug,
                "title": "Testloven",
            },
        },
    }


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    return repo


@pytest.fixture
def corpus_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A two-commit corpus: v1 body on 2026-05-01, v2 body on 2026-05-10."""
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "lover").mkdir()
    (repo / "lover" / "testloven.md").write_text("---\nx: 1\n---\n# T\n\nv1 body\n")
    (repo / "manifest.json").write_text(json.dumps(_manifest_payload("lover/testloven.md")))
    sha1 = _commit_all(repo, "sync 1", "2026-05-01T12:00:00Z")
    (repo / "lover" / "testloven.md").write_text("---\nx: 1\n---\n# T\n\nv2 body\n")
    sha2 = _commit_all(repo, "sync 2", "2026-05-10T12:00:00Z")
    return repo, sha1, sha2


def test_resolve_against_real_history(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, sha2 = corpus_repo

    assert resolve_corpus_state(repo, date(2026, 5, 5)).sha == sha1
    assert resolve_corpus_state(repo, date(2026, 5, 10)).sha == sha2


def test_snapshot_reads_blob_as_of_its_commit(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, sha2 = corpus_repo

    assert "v1 body" in (CorpusSnapshot(repo, sha1).read_text("lover/testloven.md") or "")
    assert "v2 body" in (CorpusSnapshot(repo, sha2).read_text("lover/testloven.md") or "")


def test_snapshot_missing_path_reads_none(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, _ = corpus_repo

    assert CorpusSnapshot(repo, sha1).read_text("lover/absent.md") is None


def test_snapshot_manifest_and_slug_index(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, _ = corpus_repo
    snapshot = CorpusSnapshot(repo, sha1)

    assert "doc-1" in snapshot.manifest.documents
    doc_id, record = snapshot.slug_index["testloven"]
    assert doc_id == "doc-1"
    assert record.markdown_path == "lover/testloven.md"


def test_snapshot_without_committed_manifest_fails_loudly(tmp_path: Path) -> None:
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("state before manifest support\n")
    sha = _commit_all(repo, "pre-manifest state", "2026-05-01T12:00:00Z")

    with pytest.raises(ParseError, match=r"carries no manifest\.json"):
        _ = CorpusSnapshot(repo, sha).manifest


def test_snapshot_with_malformed_committed_manifest_fails_loudly(tmp_path: Path) -> None:
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "manifest.json").write_text("{not-json}\n")
    sha = _commit_all(repo, "broken manifest", "2026-05-01T12:00:00Z")

    with pytest.raises(json.JSONDecodeError):
        _ = CorpusSnapshot(repo, sha).manifest


def test_slug_index_records_duplicates_as_ambiguous_and_skips_non_current(
    tmp_path: Path,
) -> None:
    """Issue #243: the snapshot index and the live reader's share one
    contract — a slug two current records claim resolves to neither."""
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    payload = _manifest_payload("lover/a.md")
    payload["documents"]["doc-2"] = {
        **payload["documents"]["doc-1"],  # type: ignore[dict-item]
        "doc_type": "forskrift",
        "markdown_path": "forskrifter/b.md",
        "source_dataset": "gjeldende-sentrale-forskrifter",
    }
    payload["documents"]["doc-3"] = {
        **payload["documents"]["doc-1"],  # type: ignore[dict-item]
        "status": "removed",
        "slug": "borteloven",
    }
    payload["documents"]["doc-4"] = {
        **payload["documents"]["doc-1"],  # type: ignore[dict-item]
        "markdown_path": "lover/c.md",
        "slug": "unik",
    }
    (repo / "manifest.json").write_text(json.dumps(payload))
    sha = _commit_all(repo, "sync", "2026-05-01T12:00:00Z")

    index = CorpusSnapshot(repo, sha).slug_index

    assert set(index) == {"unik"}
    assert index.resolve("unik") == index["unik"]
    assert index.resolve("borteloven") is None
    assert [doc_id for doc_id, _record in index.ambiguous["testloven"]] == ["doc-1", "doc-2"]
    with pytest.raises(AmbiguousSlugError) as excinfo:
        index.resolve("testloven")
    assert str(excinfo.value).startswith(
        "slug 'testloven' names 2 current documents: doc-1 (lov), doc-2 (forskrift); ",
    )


# ---------- operational failure vs historical absence ----------


def test_snapshot_invalid_commit_fails_loudly(corpus_repo: tuple[Path, str, str]) -> None:
    # ADR-0011 point 6 outcome 2: an unresolvable state is an operational
    # failure — it must never read as "the path was absent at that date".
    repo, _, _ = corpus_repo

    with pytest.raises(subprocess.CalledProcessError):
        CorpusSnapshot(repo, "0" * 40).read_text("lover/testloven.md")


def test_missing_path_in_valid_commit_still_reads_none(
    corpus_repo: tuple[Path, str, str],
) -> None:
    # The commit re-check on a probe miss must not turn genuine absence
    # into an error: valid commit + absent path stays a historical None.
    repo, sha1, _ = corpus_repo

    assert CorpusSnapshot(repo, sha1).read_text("lover/aldri-fantes.md") is None


# ---------- ordering: author date vs ancestry order ----------


def test_resolution_follows_ancestry_order_like_timetravel(tmp_path: Path) -> None:
    """A commit authored EARLIER but committed LATER (ancestry tip) wins for
    a cutoff both satisfy — the same convention as timetravel's follow-log
    walk, so global and per-document resolution never pick opposite sides
    of an author/ancestry divergence."""
    repo = tmp_path / "corpus"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "a.md").write_text("first\n")
    sha_first = _commit_all(repo, "first", "2026-05-01T12:00:00Z")
    (repo / "a.md").write_text("second\n")
    sha_second = _commit_all(repo, "second, authored before its parent", "2026-04-20T12:00:00Z")

    # Cutoff satisfied by both: the ancestry tip (sha_second) wins even
    # though sha_first carries the newer author date.
    assert resolve_corpus_state(repo, date(2026, 5, 5)).sha == sha_second
    # Cutoff satisfied only by the earlier-authored tip: same answer.
    assert resolve_corpus_state(repo, date(2026, 4, 25)).sha == sha_second
    assert sha_first != sha_second


def test_shallow_no_history_message_is_verbatim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("lovspor.snapshot._iter_state_log", lambda *_: [])
    monkeypatch.setattr("lovspor.snapshot._is_shallow_repository", lambda *_: True)

    with pytest.raises(ShallowHistoryError) as excinfo:
        resolve_corpus_state(tmp_path, date(2026, 5, 1))

    assert "checkout; no locally available history. Deepen" in str(excinfo.value)


# ---------- streamed archive reads (issue #223) ----------


def test_iter_texts_reads_each_wanted_file_at_its_commit(
    corpus_repo: tuple[Path, str, str],
) -> None:
    repo, sha1, sha2 = corpus_repo

    old = dict(CorpusSnapshot(repo, sha1).iter_texts({"lover/testloven.md"}))
    new = dict(CorpusSnapshot(repo, sha2).iter_texts({"lover/testloven.md"}))

    assert old == {"lover/testloven.md": "---\nx: 1\n---\n# T\n\nv1 body\n"}
    assert new == {"lover/testloven.md": "---\nx: 1\n---\n# T\n\nv2 body\n"}


def test_iter_texts_yields_only_wanted_files_present_in_the_tree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "lover").mkdir()
    (repo / "lover" / "a.md").write_text("A\n")
    (repo / "lover" / "b.md").write_text("B\n")
    (repo / "manifest.json").write_text("{}")
    sha = _commit_all(repo, "two docs", "2026-05-01T12:00:00Z")

    wanted = {"lover/b.md", "lover/absent.md", "lover/absent.txt"}
    texts = dict(CorpusSnapshot(repo, sha).iter_texts(wanted))

    assert texts == {"lover/b.md": "B\n"}


def test_iter_texts_reads_paths_of_every_suffix_and_none(tmp_path: Path) -> None:
    # The archive is narrowed by suffix; a wanted path of another suffix,
    # or of none at all, must still be read rather than silently dropped.
    repo = _init_repo(tmp_path)
    (repo / "lover").mkdir()
    (repo / "lover" / "a.md").write_text("A\n")
    (repo / "lover" / "b.txt").write_text("B\n")
    (repo / "lover" / "README").write_text("C\n")
    (repo / "manifest.json").write_text("{}")
    sha = _commit_all(repo, "mixed", "2026-05-01T12:00:00Z")
    snapshot = CorpusSnapshot(repo, sha)

    by_suffix = dict(snapshot.iter_texts({"lover/a.md", "lover/b.txt"}))
    with_bare = dict(snapshot.iter_texts({"lover/a.md", "lover/README"}))

    assert by_suffix == {"lover/a.md": "A\n", "lover/b.txt": "B\n"}
    assert with_bare == {"lover/a.md": "A\n", "lover/README": "C\n"}


def test_iter_texts_with_no_match_for_the_narrowed_archive_reads_nothing(
    corpus_repo: tuple[Path, str, str],
) -> None:
    # No .txt file exists: git refuses an unmatched pathspec, so the archive
    # must not name one — absence stays absence, not an operational error.
    repo, sha1, _ = corpus_repo

    assert list(CorpusSnapshot(repo, sha1).iter_texts({"lover/absent.txt"})) == []


def test_iter_texts_with_nothing_wanted_runs_no_git(tmp_path: Path) -> None:
    assert list(CorpusSnapshot(tmp_path / "not-a-repo", "0" * 40).iter_texts(set())) == []


def test_iter_texts_invalid_commit_fails_loudly(corpus_repo: tuple[Path, str, str]) -> None:
    repo, _, _ = corpus_repo

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        list(CorpusSnapshot(repo, "0" * 40).iter_texts({"lover/testloven.md"}))

    assert excinfo.value.returncode != 0
    assert excinfo.value.stderr


def test_stream_archive_preserves_git_returncode_and_complete_stderr() -> None:
    archive_bytes = BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        info = tarfile.TarInfo("lover/wanted.md")
        info.size = 1
        archive.addfile(info, BytesIO(b"x"))
    archive_bytes.seek(0)

    class FailedGitProcess:
        stdout = archive_bytes
        args = ["git", "archive", "broken"]  # noqa: RUF012

        @staticmethod
        def wait() -> int:
            return 23

    stderr = BytesIO(b"fatal: complete diagnostic")

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        list(
            snapshot_module._stream_archive(
                FailedGitProcess(),  # type: ignore[arg-type]
                frozenset({"lover/wanted.md"}),
                stderr,
            ),
        )

    assert excinfo.value.returncode == 23
    assert excinfo.value.stderr == b"fatal: complete diagnostic"


def test_iter_texts_stopped_early_reaps_git(corpus_repo: tuple[Path, str, str]) -> None:
    repo, sha1, _ = corpus_repo
    stream = CorpusSnapshot(repo, sha1).iter_texts({"lover/testloven.md"})

    assert next(stream)[0] == "lover/testloven.md"
    stream.close()


def test_archive_pathspecs_narrow_to_wanted_suffixes() -> None:
    wanted = frozenset({"lover/a.md", "forskrifter/b.md", "lover/c.txt"})

    assert snapshot_module._archive_pathspecs(wanted) == [
        ":(glob)**/*.md",
        ":(glob)**/*.txt",
    ]


@pytest.mark.parametrize("odd", ["lover/README", "lover/x.m*d", "lover/.md"])
def test_archive_pathspecs_fall_back_to_the_whole_tree(odd: str) -> None:
    # No suffix, or one a glob would misread: read everything, never guess.
    assert snapshot_module._archive_pathspecs(frozenset({"lover/a.md", odd})) == []


def _tar_stream(*members: tuple[str, bytes]) -> tarfile.TarFile:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))
    buffer.seek(0)
    return tarfile.open(fileobj=buffer, mode="r|")


def test_read_wanted_refuses_a_member_escaping_the_archive_root() -> None:
    # git cannot commit such a name; the data filter is the guarantee that
    # a damaged or forged stream still cannot name a path outside the tree.
    tar = _tar_stream(("../evil.md", b"x"))

    with pytest.raises(StateIntegrityError, match="evil.md"):
        list(snapshot_module._read_wanted(tar, frozenset({"../evil.md"})))


def test_read_wanted_skips_unwanted_before_reading_later_wanted() -> None:
    tar = _tar_stream(("lover/skip.md", b"s"), ("lover/keep.md", b"k"))

    assert list(snapshot_module._read_wanted(tar, frozenset({"lover/keep.md"}))) == [
        ("lover/keep.md", "k"),
    ]


def test_read_wanted_never_follows_a_wanted_symlink() -> None:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        link = tarfile.TarInfo("lover/wanted.md")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside.md"
        archive.addfile(link)
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r|") as archive:
        assert list(snapshot_module._read_wanted(archive, frozenset({link.name}))) == []


def test_read_wanted_fails_loudly_on_malformed_utf8() -> None:
    tar = _tar_stream(("lover/wanted.md", b"valid prefix\n\xff"))

    with pytest.raises(UnicodeDecodeError):
        list(snapshot_module._read_wanted(tar, frozenset({"lover/wanted.md"})))
