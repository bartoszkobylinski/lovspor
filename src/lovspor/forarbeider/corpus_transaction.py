"""One change to the sandbox's ``forarbeider/`` tree, all or nothing (#738, PR3B-2).

The only writer in this package, and only ever called on a sandbox that
:func:`~lovspor.forarbeider.corpus_tree.require_sandbox` accepted. Nothing is
staged in Git, committed or pushed.

A change holds an exclusive lock, stages every new file's bytes inside
``.git`` (never the worktree), and writes a journal of the operations before
the first one: documents and removals, then the index, then the manifest,
which is the commit point. Each operation replaces a whole file with
``os.replace``, and a file already holding its target bytes is skipped, so a
change interrupted at any point is finished, not undone, by the next run.
After the last operation the tree is read back and must be exactly the new
manifest's, with the baseline untouched; only then are the journal and the
staged bytes removed. A lock left by a process that died stays until the
owner, having checked nothing runs, removes it.
"""

import json
import os
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lovspor.forarbeider.corpus_models import DATASET_ROOT, INDEX_PATH, MANIFEST_PATH
from lovspor.forarbeider.corpus_plan import Draft, dataset_path
from lovspor.forarbeider.corpus_tree import read_dataset, require_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import JSON_LAYOUT
from lovspor.forarbeider.release_models import SHA256, digest

LOCK = ".git/lovspor-forarbeider.lock"
JOURNAL = ".git/lovspor-forarbeider-journal.json"
STAGING = ".git/lovspor-forarbeider-staging"
_Digest = Annotated[str, Field(pattern=SHA256)]


class Operation(BaseModel):
    """Make ``path`` hold the staged bytes of ``sha256``, or remove it when ``None``.

    The path has the dataset's grammar: a journal is read back from disk, and
    one edited there must not steer a write anywhere else in the sandbox.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: _Digest | None

    @model_validator(mode="after")
    def _in_the_dataset(self) -> Self:
        if not dataset_path(self.path):
            raise ValueError("an operation names a dataset file only")
        return self


class Journal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_corpus_journal"]
    target_manifest_sha256: _Digest
    operations: tuple[Operation, ...] = Field(min_length=2)


def _failed(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.OUTPUT_WRITE_FAILED, detail)


@contextmanager
def locked(sandbox: Path) -> Iterator[None]:
    """One change at a time: the lock is created exclusively and removed on the way out.

    Raises:
        ForarbeiderRefusedError: ``corpus_locked`` while another change holds it.
    """
    lock = sandbox / LOCK
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as error:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_LOCKED, "sandbox") from error
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _operations(draft: Draft) -> tuple[Operation, ...]:
    last = (INDEX_PATH, MANIFEST_PATH)
    documents = sorted(path for path in draft.writes if path not in last)
    ordered = [Operation(path=path, sha256=digest(draft.writes[path])) for path in documents]
    ordered += [Operation(path=path, sha256=None) for path in draft.removes]
    ordered += [Operation(path=path, sha256=digest(draft.writes[path])) for path in last]
    return tuple(ordered)


def _current(target: Path) -> str | None:
    try:
        return digest(target.read_bytes()) if not target.is_symlink() else ""
    except FileNotFoundError:
        return None


def _parent(sandbox: Path, path: str) -> Path:
    """The directory ``path`` goes in, made below the sandbox without following a symlink.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``.
    """
    directory = sandbox
    for part in Path(path).parent.parts:
        directory = directory / part
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ForarbeiderRefusedError(RefusalReason.UNSAFE_OUTPUT_PATH, DATASET_ROOT)
        directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def replace(source: Path, target: Path) -> None:
    source.replace(target)


def _perform(sandbox: Path, operation: Operation) -> None:
    target = sandbox / operation.path
    if _current(target) == operation.sha256:
        return
    if operation.sha256 is None:
        target.unlink()
        return
    staged = sandbox / STAGING / operation.sha256
    payload = staged.read_bytes()
    if digest(payload) != operation.sha256:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "staging")
    _parent(sandbox, operation.path)
    copy = sandbox / STAGING / f"{operation.sha256}.next"
    copy.write_bytes(payload)
    replace(copy, target)


def _run(sandbox: Path, journal: Journal) -> None:
    try:
        for operation in journal.operations:
            _perform(sandbox, operation)
    except OSError as error:
        raise _failed(DATASET_ROOT) from error


def _verify(sandbox: Path, journal: Journal) -> None:
    """The tree read back is the target manifest's, and the baseline is as made.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch`` and the journal stays.
    """
    if read_dataset(require_sandbox(sandbox, ())).manifest_sha256 != journal.target_manifest_sha256:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)


def _clear(sandbox: Path) -> None:
    (sandbox / JOURNAL).unlink(missing_ok=True)
    shutil.rmtree(sandbox / STAGING, ignore_errors=True)


def _journal(sandbox: Path) -> Journal | None:
    path = sandbox / JOURNAL
    if not path.exists():
        return None
    try:
        return Journal.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "journal") from error


def recover(sandbox: Path) -> bool:
    """Finish a change an earlier run left journaled; the caller holds the lock.

    Returns:
        Whether there was one to finish.

    Raises:
        ForarbeiderRefusedError: the tree cannot be brought to the journaled state.
    """
    journal = _journal(sandbox)
    if journal is None:
        return False
    _run(sandbox, journal)
    _verify(sandbox, journal)
    _clear(sandbox)
    return True


def _stage(sandbox: Path, writes: Mapping[str, bytes], journal: Journal) -> None:
    staging = sandbox / STAGING
    try:
        staging.mkdir(mode=0o700, exist_ok=True)
        for payload in writes.values():
            (staging / digest(payload)).write_bytes(payload)
        text = json.dumps(journal.model_dump(mode="json"), **JSON_LAYOUT) + "\n"
        (staging / "journal.next").write_text(text, encoding="utf-8")
        replace(staging / "journal.next", sandbox / JOURNAL)
    except OSError as error:
        raise _failed("staging") from error


def apply(sandbox: Path, draft: Draft) -> None:
    """Make the dataset the draft's; the caller holds the lock and checked the base.

    Raises:
        ForarbeiderRefusedError: ``output_write_failed`` with the journal kept
            for the next run to finish, or ``corpus_tree_mismatch``.
    """
    journal = Journal(
        schema_version=1,
        record_type="forarbeider_corpus_journal",
        target_manifest_sha256=digest(draft.writes[MANIFEST_PATH]),
        operations=_operations(draft),
    )
    _stage(sandbox, draft.writes, journal)
    _run(sandbox, journal)
    _verify(sandbox, journal)
    _clear(sandbox)


def holds(sandbox: Path, expected: Mapping[str, str | None]) -> bool:
    """Whether every path holds the digest given, or is absent for ``None``: a retry is a no-op."""
    return all(_current(sandbox / path) == sha for path, sha in expected.items())
