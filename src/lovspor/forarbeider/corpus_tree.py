"""A checkout's ``forarbeider/`` tree, and the disposable synthetic sandbox (#738, PR3B-2).

No Git command runs: this package never starts a subprocess. A checkout is
read file by file through no-follow descriptors, so a symlink or a special
file anywhere in ``forarbeider/`` is refused, never followed, and the tree
must be exactly what its manifest lists.

The sandbox is the only tree this package may write. It is a new, empty
directory outside every repository, made a Git checkout by the skeleton
``git init`` would write (an unborn ``main``, no remote, no commit), with a
marker inside ``.git`` and a fixed placeholder tree standing in for the
Lovdata root. Anything else, a real ``lovverk`` clone above all, is refused.
"""

import errno
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from lovspor.forarbeider.corpus_models import (
    DATASET_ROOT,
    EMPTY_MANIFEST,
    INDEX_PATH,
    MANIFEST_PATH,
    ForarbeiderManifest,
    parse_manifest,
    verify_members,
)
from lovspor.forarbeider.corpus_render import render_index
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import digest
from lovspor.forarbeider.release_store import refused_as
from lovspor.forarbeider.staging import checked_directory

#: The largest file a dataset holds: a rendering of many chapters, never a corpus.
MAX_CORPUS_FILE_BYTES = 32 * 1024 * 1024
MARKER = ".git/lovspor-synthetic-sandbox"
MARKER_TEXT = "lovspor forarbeider synthetic sandbox, version 1: disposable, never a corpus\n"
#: Stand-ins for the Lovdata root and the other datasets, checked unchanged around every write.
BASELINE: dict[str, str] = {
    "README.md": "# Synthetic sandbox\n\nDisposable. Not lovverk. Holds synthetic data only.\n",
    "manifest.json": '{"dataset": "synthetic-root-placeholder", "documents": []}\n',
    "lover/README.md": "Synthetic placeholder. Forarbeider never write here.\n",
    "forskrifter/README.md": "Synthetic placeholder. Forarbeider never write here.\n",
    "lokale-forskrifter/README.md": "Synthetic placeholder. Forarbeider never write here.\n",
}
_GIT_FILES = {
    "HEAD": "ref: refs/heads/main\n",
    "config": "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n",
    "description": "Disposable synthetic sandbox; no remote, never pushed.\n",
}
#: The label of every sandbox refusal; a read inside a check that only answers yes or no
#: never shows it.
SANDBOX = "sandbox"
_GIT_DIRECTORIES = ("objects", "objects/info", "objects/pack", "refs", "refs/heads", "refs/tags")


class Dataset(NamedTuple):
    """The dataset as found: its manifest and the digest of its bytes, ``None`` when absent."""

    manifest: ForarbeiderManifest
    manifest_sha256: str | None


def _unsafe(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.UNSAFE_INPUT_PATH, detail)


def checked_checkout(raw: Path) -> Path:
    """An absolute checkout path that is its own resolved form and holds ``.git``.

    Raises:
        ForarbeiderRefusedError: ``unsafe_input_path``.
    """
    try:
        if not raw.is_absolute() or raw.resolve() != raw:
            raise _unsafe("corpus")
        status = os.lstat(raw)
    except (OSError, RuntimeError) as error:
        raise _unsafe("corpus") from error
    if not stat.S_ISDIR(status.st_mode) or not os.path.lexists(raw / ".git"):
        raise _unsafe("corpus")
    return raw


def _read_public(path: Path, shown: str = DATASET_ROOT) -> bytes:
    """A regular file's bytes through one no-follow descriptor, size-bounded.

    Raises:
        ForarbeiderRefusedError: ``unsafe_input_path``, ``input_too_large`` or
            ``input_unreadable``.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        reason = RefusalReason.UNSAFE_INPUT_PATH if error.errno == errno.ELOOP else None
        raise ForarbeiderRefusedError(reason or RefusalReason.INPUT_UNREADABLE, shown) from error
    with os.fdopen(descriptor, "rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise _unsafe(shown)
        if status.st_size > MAX_CORPUS_FILE_BYTES:
            raise ForarbeiderRefusedError(RefusalReason.INPUT_TOO_LARGE, shown)
        return stream.read(MAX_CORPUS_FILE_BYTES + 1)


def _walk(directory: Path, prefix: str) -> dict[str, bytes]:
    """Every file below ``directory``, by its path under the checkout.

    Raises:
        ForarbeiderRefusedError: a symlink, a special file or an unreadable one.
    """
    files: dict[str, bytes] = {}
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            relative = f"{prefix}/{entry.name}"
            mode = os.lstat(entry.path).st_mode
            if stat.S_ISLNK(mode):
                raise _unsafe(DATASET_ROOT)
            if stat.S_ISDIR(mode):
                files.update(_walk(Path(entry.path), relative))
            else:
                files[relative] = _read_public(Path(entry.path), DATASET_ROOT)
    return files


def _dataset_files(checkout: Path) -> dict[str, bytes] | None:
    root = checkout / DATASET_ROOT
    try:
        status = os.lstat(root)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _unsafe(DATASET_ROOT) from error
    if not stat.S_ISDIR(status.st_mode):
        raise _unsafe(DATASET_ROOT)
    return _walk(root, DATASET_ROOT)


def read_dataset(checkout: Path) -> Dataset:
    """The ``forarbeider/`` dataset of a checkout, proved to be exactly what its manifest lists.

    An absent directory is the empty dataset. Reading writes nothing.

    Raises:
        ForarbeiderRefusedError: an unsafe tree, ``manifest_invalid`` or
            ``manifest_incompatible``, or ``corpus_tree_mismatch``.
    """
    files = _dataset_files(checked_checkout(checkout))
    if files is None:
        return Dataset(EMPTY_MANIFEST, None)
    raw = files.pop(MANIFEST_PATH, None)
    if raw is None:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)
    manifest = parse_manifest(raw)
    if files.pop(INDEX_PATH, None) != render_index(manifest).encode():
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, INDEX_PATH)
    verify_members(manifest, files)
    return Dataset(manifest, digest(raw))


def _write_new(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(text.encode())


def create_sandbox(raw: Path, forbidden: Sequence[Path]) -> Path:
    """A new, owner-only, empty Git checkout holding only the synthetic baseline.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``: not absolute, already
            there, or inside a repository or a forbidden tree.
    """
    refused = ForarbeiderRefusedError(RefusalReason.UNSAFE_OUTPUT_PATH, "sandbox")
    if not raw.is_absolute() or os.path.lexists(raw) or raw.parent.resolve() != raw.parent:
        raise refused
    with refused_as("sandbox"):
        checked_directory(raw.parent, forbidden)
    try:
        raw.mkdir(mode=0o700)
        (raw / ".git").mkdir(mode=0o700)
        for name in _GIT_DIRECTORIES:
            (raw / ".git" / name).mkdir(mode=0o700)
        for name, text in {**_GIT_FILES, "lovspor-synthetic-sandbox": MARKER_TEXT}.items():
            _write_new(raw / ".git" / name, text)
        for name, text in BASELINE.items():
            (raw / name).parent.mkdir(mode=0o700, exist_ok=True)
            _write_new(raw / name, text)
    except OSError as error:
        raise ForarbeiderRefusedError(RefusalReason.OUTPUT_WRITE_FAILED, "sandbox") from error
    return raw


def _not_sandbox() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_NOT_SANDBOX, "sandbox")


def require_sandbox(raw: Path, forbidden: Sequence[Path]) -> Path:
    """The sandbox at ``raw``, refused unless it is one this package made and left as made.

    Raises:
        ForarbeiderRefusedError: ``corpus_not_sandbox`` for a real or foreign
            checkout, ``unsafe_input_path`` or ``corpus_tree_mismatch``.
    """
    with refused_as("sandbox"):
        path = checked_checkout(raw)
    if any(tree.resolve() in (path, *path.parents) for tree in forbidden):
        raise _not_sandbox()
    git = path / ".git"
    if not stat.S_ISDIR(os.lstat(git).st_mode) or _shared(path):
        raise _not_sandbox()
    if not _ours(path / MARKER, MARKER_TEXT.encode()) or _has_remote(git / "config"):
        raise _not_sandbox()
    with refused_as("sandbox"):
        _require_baseline(path)
    return path


def _ours(path: Path, expected: bytes) -> bool:
    try:
        return _read_public(path) == expected
    except ForarbeiderRefusedError:
        return False


def _has_remote(config: Path) -> bool:
    """A remote, or any URL, means the checkout can reach somewhere: not a sandbox."""
    try:
        text = _read_public(config)
    except ForarbeiderRefusedError:
        return True
    return b"[remote" in text or b"url" in text


def _shared(path: Path) -> bool:
    status = os.lstat(path)
    return status.st_uid != os.geteuid() or bool(stat.S_IMODE(status.st_mode) & 0o022)


def _require_baseline(path: Path) -> None:
    """Outside ``.git`` and ``forarbeider/``, the tree is the baseline, byte for byte.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch`` or an unsafe entry.
    """
    found: dict[str, bytes] = {}
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name in {".git", DATASET_ROOT}:
                continue
            mode = os.lstat(entry.path).st_mode
            if stat.S_ISLNK(mode):
                raise _unsafe("sandbox")
            if stat.S_ISDIR(mode):
                found.update(_walk(Path(entry.path), entry.name))
            else:
                found[entry.name] = _read_public(Path(entry.path), "sandbox")
    if found != {name: text.encode() for name, text in BASELINE.items()}:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "sandbox")
