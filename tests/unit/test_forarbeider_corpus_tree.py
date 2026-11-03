"""Reading a checkout's forarbeider tree, and the synthetic sandbox (#738, PR3B-2, M3).

The tree is read without following anything, must be exactly what its manifest
lists, and the only writable checkout is a new sandbox this package made: a
real clone, a foreign repository or an edited sandbox is refused.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lovspor.forarbeider import corpus_tree
from lovspor.forarbeider.corpus_models import EMPTY_MANIFEST, manifest_text
from lovspor.forarbeider.corpus_render import render_index
from lovspor.forarbeider.corpus_tree import (
    BASELINE,
    MARKER,
    checked_checkout,
    create_sandbox,
    read_dataset,
    require_sandbox,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from tests.unit.forarbeider_corpus_support import read_beside_fifo

_GIT = shutil.which("git")


def _git(*arguments: str, cwd: Path) -> str:
    assert _GIT is not None
    done = subprocess.run([_GIT, *arguments], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


def _refused(reason: RefusalReason, action: object, *arguments: object) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        action(*arguments)  # type: ignore[operator]
    assert caught.value.reason is reason, caught.value
    return caught.value


def _empty_dataset(checkout: Path) -> None:
    (checkout / "forarbeider").mkdir()
    (checkout / "forarbeider" / "manifest.json").write_text(manifest_text(EMPTY_MANIFEST))
    (checkout / "forarbeider" / "INDEX.md").write_text(render_index(EMPTY_MANIFEST))


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    return create_sandbox(tmp_path / "sandbox", ())


@pytest.mark.skipif(_GIT is None, reason="git is not installed")
def test_a_new_sandbox_is_an_empty_git_checkout_without_a_remote(sandbox: Path) -> None:
    assert _git("rev-parse", "--is-inside-work-tree", cwd=sandbox).strip() == "true"
    assert _git("symbolic-ref", "HEAD", cwd=sandbox).strip() == "refs/heads/main"
    assert _git("remote", cwd=sandbox) == ""
    untracked = sorted(_git("status", "--porcelain", "-uall", cwd=sandbox).splitlines())
    assert untracked == sorted(f"?? {name}" for name in BASELINE)
    assert oct(os.lstat(sandbox).st_mode & 0o777) == "0o700"


def test_a_new_sandbox_holds_the_baseline_and_no_dataset(sandbox: Path) -> None:
    for name, text in BASELINE.items():
        assert (sandbox / name).read_text() == text
    assert not (sandbox / "forarbeider").exists()
    assert read_dataset(sandbox) == (EMPTY_MANIFEST, None)
    assert require_sandbox(sandbox, ()) == sandbox


def test_a_sandbox_is_never_made_where_something_is(tmp_path: Path) -> None:
    (tmp_path / "taken").mkdir()
    (tmp_path / "file").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "taken")

    for name in ("taken", "file", "link"):
        error = _refused(RefusalReason.UNSAFE_OUTPUT_PATH, create_sandbox, tmp_path / name, ())
        assert error.detail == "sandbox"
    _refused(RefusalReason.UNSAFE_OUTPUT_PATH, create_sandbox, Path("relative"), ())


def test_a_sandbox_is_never_made_inside_a_repository_or_a_forbidden_tree(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "corpus").mkdir()
    (tmp_path / "real").mkdir()
    (tmp_path / "linked").symlink_to(tmp_path / "real")

    for target, forbidden in [
        (tmp_path / "repo" / "sandbox", ()),
        (tmp_path / "corpus" / "sandbox", (tmp_path / "corpus",)),
        (tmp_path / "linked" / "sandbox", ()),
    ]:
        error = _refused(RefusalReason.UNSAFE_OUTPUT_PATH, create_sandbox, target, forbidden)
        assert str(tmp_path) not in error.detail
        assert not target.exists()


@pytest.mark.skipif(_GIT is None, reason="git is not installed")
def test_a_repository_git_made_is_not_a_sandbox(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    _git("init", "-q", cwd=clone)

    _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, clone, ())


@pytest.mark.skipif(_GIT is None, reason="git is not installed")
def test_a_sandbox_given_a_remote_is_no_longer_one(sandbox: Path) -> None:
    _git("remote", "add", "origin", "https://example.invalid/lovverk.git", cwd=sandbox)

    _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, sandbox, ())


def test_a_marker_edited_or_removed_is_not_a_sandbox(sandbox: Path) -> None:
    (sandbox / MARKER).write_text("lovverk\n")
    _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, sandbox, ())

    (sandbox / MARKER).unlink()
    _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, sandbox, ())


def test_the_default_corpus_or_a_tree_under_it_is_never_a_sandbox(sandbox: Path) -> None:
    _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, sandbox, (sandbox,))
    _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, sandbox, (sandbox.parent,))


def test_a_sandbox_others_can_write_is_refused(sandbox: Path) -> None:
    sandbox.chmod(0o770)
    try:
        _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, sandbox, ())
    finally:
        sandbox.chmod(0o700)


def test_a_symlinked_sandbox_or_git_directory_is_refused(sandbox: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    alias.symlink_to(sandbox)
    _refused(RefusalReason.UNSAFE_INPUT_PATH, require_sandbox, alias, ())

    moved = tmp_path / "moved-git"
    (sandbox / ".git").rename(moved)
    (sandbox / ".git").symlink_to(moved)
    _refused(RefusalReason.CORPUS_NOT_SANDBOX, require_sandbox, sandbox, ())


@pytest.mark.parametrize(
    "change",
    [
        lambda root: (root / "manifest.json").write_text('{"documents": ["x"]}\n'),
        lambda root: (root / "lover" / "9000001.md").write_text("x"),
        lambda root: (root / "lokale-forskrifter" / "README.md").unlink(),
        lambda root: (root / "notes.txt").write_text("x"),
        lambda root: (root / "lover" / "README.md").chmod(0o000),
    ],
    ids=["root-manifest", "lover-file", "local-removed", "stray", "unreadable"],
)
def test_a_baseline_edited_outside_the_dataset_is_refused(sandbox: Path, change: object) -> None:
    change(sandbox)  # type: ignore[operator]
    try:
        with pytest.raises(ForarbeiderRefusedError) as caught:
            require_sandbox(sandbox, ())
    finally:
        (sandbox / "lover" / "README.md").chmod(0o600)
    assert caught.value.reason in {
        RefusalReason.CORPUS_TREE_MISMATCH,
        RefusalReason.INPUT_UNREADABLE,
    }
    assert caught.value.detail == "sandbox"


def test_a_symlink_in_the_baseline_is_refused(sandbox: Path, tmp_path: Path) -> None:
    (sandbox / "lover" / "README.md").unlink()
    (tmp_path / "outside.md").write_text(BASELINE["lover/README.md"])
    (sandbox / "lover" / "README.md").symlink_to(tmp_path / "outside.md")

    _refused(RefusalReason.UNSAFE_INPUT_PATH, require_sandbox, sandbox, ())


def test_an_empty_dataset_reads_as_the_empty_manifest(sandbox: Path) -> None:
    _empty_dataset(sandbox)

    dataset = read_dataset(sandbox)

    assert dataset.manifest == EMPTY_MANIFEST
    assert dataset.manifest_sha256 is not None


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (lambda root: (root / "manifest.json").unlink(), "forarbeider/manifest.json"),
        (lambda root: (root / "INDEX.md").write_text("# Forarbeider\n"), "forarbeider/INDEX.md"),
        (lambda root: (root / "INDEX.md").unlink(), "forarbeider/INDEX.md"),
        (
            lambda root: (
                (root / "2099-2100").mkdir() or (root / "2099-2100" / "x.md").write_text("x")
            ),
            "forarbeider",
        ),
    ],
    ids=["no-manifest", "index-edited", "no-index", "stray"],
)
def test_a_dataset_not_exactly_as_listed_is_refused(
    sandbox: Path, change: object, detail: str
) -> None:
    _empty_dataset(sandbox)
    change(sandbox / "forarbeider")  # type: ignore[operator]

    error = _refused(RefusalReason.CORPUS_TREE_MISMATCH, read_dataset, sandbox)

    assert error.detail == detail


def test_a_symlink_inside_the_dataset_is_refused_not_followed(
    sandbox: Path, tmp_path: Path
) -> None:
    _empty_dataset(sandbox)
    outside = tmp_path / "outside.json"
    outside.write_text(manifest_text(EMPTY_MANIFEST))
    (sandbox / "forarbeider" / "manifest.json").unlink()
    (sandbox / "forarbeider" / "manifest.json").symlink_to(outside)

    error = _refused(RefusalReason.UNSAFE_INPUT_PATH, read_dataset, sandbox)

    assert error.detail == "forarbeider"


def test_a_symlinked_dataset_directory_is_refused(sandbox: Path, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (sandbox / "forarbeider").symlink_to(tmp_path / "elsewhere")

    _refused(RefusalReason.UNSAFE_INPUT_PATH, read_dataset, sandbox)


def test_a_fifo_inside_the_dataset_is_refused_without_hanging(sandbox: Path) -> None:
    _empty_dataset(sandbox)
    os.mkfifo(sandbox / "forarbeider" / "INDEX.md.fifo")

    blocked, outcome = read_beside_fifo(sandbox, sandbox / "forarbeider" / "INDEX.md.fifo")

    assert not blocked, "the reader blocked on a FIFO"
    assert outcome == [RefusalReason.UNSAFE_INPUT_PATH]


def test_an_oversized_dataset_file_is_refused(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _empty_dataset(sandbox)
    monkeypatch.setattr(corpus_tree, "MAX_CORPUS_FILE_BYTES", 8)

    _refused(RefusalReason.INPUT_TOO_LARGE, read_dataset, sandbox)


def test_a_path_that_is_not_a_checkout_is_refused(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    (tmp_path / "file").write_text("x")

    for raw in (tmp_path / "plain", tmp_path / "file", tmp_path / "missing", Path("relative")):
        error = _refused(RefusalReason.UNSAFE_INPUT_PATH, checked_checkout, raw)
        assert error.detail == "corpus"


def test_reading_a_dataset_writes_nothing(sandbox: Path) -> None:
    _empty_dataset(sandbox)
    before = sorted((path, path.stat().st_mtime_ns) for path in sandbox.rglob("*"))

    read_dataset(sandbox)
    require_sandbox(sandbox, ())

    assert sorted((path, path.stat().st_mtime_ns) for path in sandbox.rglob("*")) == before
