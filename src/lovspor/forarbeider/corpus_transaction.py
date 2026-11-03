"""One change to the sandbox's ``forarbeider/`` tree, all or nothing (#738, PR3B-2).

The only writer in this package, and only ever called on a sandbox that
:func:`~lovspor.forarbeider.corpus_tree.require_sandbox` accepted. Nothing is
staged in Git, committed or pushed.

A change holds an exclusive lock, stages every new file's bytes and every
replaced or removed file's previous bytes inside ``.git`` (never the
worktree), and writes a journal of the operations before the first one:
documents and removals, then the index, then the manifest. The journal names
its action and the decision behind it, a release plan's digest or a
withdrawal intent's, so the caller can check that decision still holds
before it chooses: :func:`finish` replays the operations forward, a file
already holding its target bytes skipped; :func:`roll_back` puts every file
back as it was. Either way the tree is read back and must be exactly the
manifest's, with the baseline untouched; only then are the journal and the
staged bytes removed. A lock left by a process that died stays until the
owner, having checked nothing runs, removes it.
"""

import json
import os
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lovspor.forarbeider.corpus_leaves import directory, read_leaf, write_leaf
from lovspor.forarbeider.corpus_models import DATASET_ROOT, INDEX_PATH, MANIFEST_PATH
from lovspor.forarbeider.corpus_plan import Draft, dataset_path
from lovspor.forarbeider.corpus_tree import _read_public, read_dataset, require_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import JSON_LAYOUT
from lovspor.forarbeider.release_models import SHA256, digest

LOCK = ".git/lovspor-forarbeider.lock"
JOURNAL = ".git/lovspor-forarbeider-journal.json"
STAGING = ".git/lovspor-forarbeider-staging"
_Digest = Annotated[str, Field(pattern=SHA256)]


class Operation(BaseModel):
    """Make ``path`` hold the staged bytes of ``sha256``, or remove it when ``None``.

    ``previous_sha256`` is what it held before, staged too, ``None`` when it was
    absent: a rollback restores it. The path has the dataset's grammar: a
    journal is read back from disk, and one edited there must not steer a
    write anywhere else in the sandbox.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: _Digest | None
    previous_sha256: _Digest | None

    @model_validator(mode="after")
    def _in_the_dataset(self) -> Self:
        if not dataset_path(self.path):
            raise ValueError("an operation names a dataset file only")
        return self


class Binding(NamedTuple):
    """The decision a change carries out: a release plan's digest, or a withdrawal intent's."""

    action: Literal["release", "withdraw"]
    decision_sha256: str


class Journal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_corpus_journal"]
    action: Literal["release", "withdraw"]
    decision_sha256: _Digest
    target_manifest_sha256: _Digest
    operations: tuple[Operation, ...] = Field(min_length=2)

    @property
    def binding(self) -> Binding:
        return Binding(self.action, self.decision_sha256)


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


def _previous(sandbox: Path, draft: Draft) -> dict[str, bytes]:
    """The bytes every file the draft replaces or removes holds now, read without following."""
    found: dict[str, bytes] = {}
    for path in [*draft.writes, *draft.removes]:
        if os.path.lexists(sandbox / path):
            found[path] = _read_public(sandbox / path)
    return found


def _operations(draft: Draft, previous: Mapping[str, bytes]) -> tuple[Operation, ...]:
    def step(path: str, sha: str | None) -> Operation:
        before = digest(previous[path]) if path in previous else None
        return Operation(path=path, sha256=sha, previous_sha256=before)

    last = (INDEX_PATH, MANIFEST_PATH)
    documents = sorted(path for path in draft.writes if path not in last)
    ordered = [step(path, digest(draft.writes[path])) for path in documents]
    ordered += [step(path, None) for path in draft.removes]
    ordered += [step(path, digest(draft.writes[path])) for path in last]
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


@contextmanager
def _staging(sandbox: Path, create: bool = False) -> Iterator[int]:
    """The staging directory inside ``.git``, opened as a descriptor, never through a link.

    Every staged file is then reached through this descriptor only, so a
    symlink, FIFO or hard link at the directory or at any file in it carries
    no byte out of the sandbox.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``.
    """
    git, name = (sandbox / STAGING).parent, (sandbox / STAGING).name
    with directory(git, "staging") as parent:
        if create:
            with suppress(FileExistsError):
                os.mkdir(name, 0o700, dir_fd=parent)
        with directory(Path(name), "staging", parent) as staging:
            yield staging


def _make(sandbox: Path, path: str, sha: str | None) -> None:
    """Make ``path`` hold the staged bytes of ``sha``, or be absent for ``None``."""
    target = sandbox / path
    if _current(target) == sha:
        return
    if sha is None:
        target.unlink()
        return
    with _staging(sandbox) as staging:
        payload = read_leaf(staging, sha, "staging")
        if digest(payload) != sha:
            raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "staging")
        _parent(sandbox, path)
        write_leaf(staging, f"{sha}.next", payload, "staging")
        replace(sandbox / STAGING / f"{sha}.next", target)


def _perform(sandbox: Path, operation: Operation) -> None:
    _make(sandbox, operation.path, operation.sha256)


def _undo(sandbox: Path, operation: Operation) -> None:
    """Put ``operation``'s file back as it was; only a file this change wrote is touched.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch`` for a file holding
            neither its old nor its new bytes.
    """
    current = _current(sandbox / operation.path)
    if current not in (operation.sha256, operation.previous_sha256):
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, DATASET_ROOT)
    _make(sandbox, operation.path, operation.previous_sha256)


def _prune(sandbox: Path) -> None:
    """Remove the directories a rolled-back change left empty; ``rmdir`` never follows a link."""
    for folder, _, _ in reversed(list(os.walk(sandbox / DATASET_ROOT))):
        with suppress(OSError):
            Path(folder).rmdir()


def _run(sandbox: Path, journal: Journal) -> None:
    try:
        for operation in journal.operations:
            _perform(sandbox, operation)
    except OSError as error:
        raise _failed(DATASET_ROOT) from error


def _verify(sandbox: Path, expected: str | None) -> None:
    """The tree read back is the manifest ``expected``'s, and the baseline is as made.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch`` and the journal stays.
    """
    if read_dataset(require_sandbox(sandbox, ())).manifest_sha256 != expected:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, MANIFEST_PATH)


def _clear(sandbox: Path) -> None:
    (sandbox / JOURNAL).unlink(missing_ok=True)
    shutil.rmtree(sandbox / STAGING, ignore_errors=True)


def pending(sandbox: Path) -> Journal | None:
    """The change an earlier run left journaled, read without following a link.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: journal`` for one that
            is unsafe, unreadable or not a journal.
    """
    path = sandbox / JOURNAL
    if not os.path.lexists(path):
        return None
    try:
        with directory(path.parent, "journal") as parent:
            raw = read_leaf(parent, path.name, "journal")
        return Journal.model_validate_json(raw)
    except (OSError, ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "journal") from error


def finish(sandbox: Path, journal: Journal) -> None:
    """Replay ``journal`` forward; the caller holds the lock and checked its decision.

    Raises:
        ForarbeiderRefusedError: the tree cannot be brought to the journaled state.
    """
    _run(sandbox, journal)
    _verify(sandbox, journal.target_manifest_sha256)
    _clear(sandbox)


def roll_back(sandbox: Path, journal: Journal) -> None:
    """Put the tree back as it was before ``journal``; the caller holds the lock.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch`` for a journal with no
            manifest or a file this change did not write; ``output_write_failed``.
    """
    manifest = next((item for item in journal.operations if item.path == MANIFEST_PATH), None)
    if manifest is None:
        raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "journal")
    try:
        for operation in reversed(journal.operations):
            _undo(sandbox, operation)
        _prune(sandbox)
    except OSError as error:
        raise _failed(DATASET_ROOT) from error
    _verify(sandbox, manifest.previous_sha256)
    _clear(sandbox)


def _stage(sandbox: Path, writes: Mapping[str, bytes], journal: Journal) -> None:
    text = json.dumps(json.loads(journal.model_dump_json()), **JSON_LAYOUT) + "\n"
    try:
        with _staging(sandbox, create=True) as staging:
            for payload in writes.values():
                write_leaf(staging, digest(payload), payload, "staging")
            write_leaf(staging, "journal.next", text.encode(), "staging")
        replace(sandbox / STAGING / "journal.next", sandbox / JOURNAL)
    except OSError as error:
        raise _failed("staging") from error


def apply(sandbox: Path, draft: Draft, binding: Binding) -> None:
    """Make the dataset the draft's; the caller holds the lock and checked the base.

    Raises:
        ForarbeiderRefusedError: ``output_write_failed`` with the journal kept
            for the next run to finish or roll back, or ``corpus_tree_mismatch``.
    """
    previous = _previous(sandbox, draft)
    journal = Journal(
        schema_version=1,
        record_type="forarbeider_corpus_journal",
        action=binding.action,
        decision_sha256=binding.decision_sha256,
        target_manifest_sha256=digest(draft.writes[MANIFEST_PATH]),
        operations=_operations(draft, previous),
    )
    staged = {**{digest(payload): payload for payload in previous.values()}, **draft.writes}
    _stage(sandbox, staged, journal)
    finish(sandbox, journal)


def holds(sandbox: Path, expected: Mapping[str, str | None]) -> bool:
    """Whether every path holds the digest given, or is absent for ``None``: a retry is a no-op."""
    return all(_current(sandbox / path) == sha for path, sha in expected.items())
