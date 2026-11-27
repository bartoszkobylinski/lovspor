"""AR4a: the journaled, crash-safe writer of one publication proposal (#738; §9.3 step 3).

It follows the B1b transaction (:mod:`lovspor.forarbeider.corpus_transaction`)
and reuses its pieces: the clone's exclusive ``flock`` lock
(:mod:`lovspor.forarbeider.release_lock`, taken by the caller), and staged
bytes reached only through a directory descriptor inside ``.git``, created
``O_EXCL | O_NOFOLLOW`` (:mod:`lovspor.forarbeider.corpus_leaves`). What it adds
is a journal with explicit **preparation states**, written atomically at every
step, so a crash at any point leaves a state recovery can name:

``staging`` (journal written, bytes being staged) → ``staged`` → documents →
``documents_written`` → index and catalogue → ``index_written`` → release
manifest → ``manifest_written`` → ``reading_back`` → ``prepared`` (ready for
publication authorization, which AR4b adds). ``rolling_back`` only goes back.

Rules, as in B1b:

- a file is written or undone only while it holds its old or its new bytes; a
  file edited by hand since is a refusal (``corpus_tree_mismatch``), and the
  journal stays;
- a rollback restores every file from the staged previous bytes, removes the
  directories it left empty, proves every operation's file holds its previous
  bytes, and only then removes the journal and the staging;
- the read-back (:func:`verify_proposal`) proves ``HEAD`` is the base, Git
  reports exactly the proposal's paths (new ``??``, replaced `` M``; nothing
  ignored, staged, deleted or elsewhere), and every file holds its digest.

Nothing here runs ``git add``, commits or pushes.
"""

import os
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from lovspor.forarbeider import publication_clone
from lovspor.forarbeider.corpus_leaves import directory, read_leaf, write_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_models import (
    RELEASE_SIGNATURE_FILE,
    PreparationJournal,
    PreparationState,
    PublicationTransaction,
    operations_of,
    phase_of,
    publication_path,
)
from lovspor.forarbeider.version_models import canonical_text

JOURNAL = ".git/lovspor-publication-journal.json"
STAGING = ".git/lovspor-publication-staging"
_JOURNAL_NAME = Path(JOURNAL).name
_STAGING_NAME = Path(STAGING).name
_TREE_DIRECTORIES = ("documents", "releases")
_PHASES: tuple[tuple[int, PreparationState], ...] = (
    (0, "documents_written"),
    (1, "index_written"),
    (2, "manifest_written"),
)


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _mismatch(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, detail)


def _unsafe(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.UNSAFE_OUTPUT_PATH, detail)


# --- the steps a crash can interrupt (module level, so a test can stand in) -------------------


def replace(source: Path, target: Path) -> None:
    source.replace(target)


def unlink(path: Path) -> None:
    path.unlink()


def write_staged(staging: int, name: str, payload: bytes) -> None:
    write_leaf(staging, name, payload, "staging")


def save(clone: Path, journal: PreparationJournal) -> None:
    """Write ``journal`` atomically: a private ``.next`` file, then a rename over the old one."""
    with directory(clone / ".git", "journal") as git:
        write_leaf(git, f"{_JOURNAL_NAME}.next", canonical_text(journal).encode(), "journal")
        os.replace(f"{_JOURNAL_NAME}.next", _JOURNAL_NAME, src_dir_fd=git, dst_dir_fd=git)


# --- reading -------------------------------------------------------------------------------


def pending_journal(clone: Path) -> PreparationJournal | None:
    """The preparation a run left journaled, read without following a link.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: journal`` for one that is
            unsafe, unreadable, not canonical or not its own transaction's.
    """
    if not os.path.lexists(clone / JOURNAL):
        return None
    try:
        with directory(clone / ".git", "journal") as git:
            raw = read_leaf(git, _JOURNAL_NAME, "journal")
        journal = PreparationJournal.model_validate_json(raw)
    except (OSError, ValidationError, ForarbeiderRefusedError) as error:
        raise _mismatch("journal") from error
    if canonical_text(journal).encode() != raw:
        raise _mismatch("journal")
    return journal


def _require_parents(clone: Path, path: str) -> None:
    folder = clone
    for part in Path(path).parent.parts:
        folder = folder / part
        if folder.is_symlink() or (os.path.lexists(folder) and not folder.is_dir()):
            raise _unsafe(path)


def current(clone: Path, path: str) -> str | None:
    """The digest ``path`` holds now, ``None`` when absent, ``""`` for anything not a plain file."""
    _require_parents(clone, path)
    target = clone / path
    if not os.path.lexists(target):
        return None
    if target.is_symlink() or not target.is_file():
        return ""
    return _digest(read_file(clone, path))


def read_file(clone: Path, path: str) -> bytes:
    """The bytes of ``path`` in the clone, never through a link.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``.
    """
    _require_parents(clone, path)
    target = clone / path
    try:
        with directory(target.parent, "publication") as parent:
            return read_leaf(parent, target.name, "publication")
    except OSError as error:
        raise _unsafe(path) from error


@contextmanager
def _staging(clone: Path, create: bool = False) -> Iterator[int]:
    with directory(clone / ".git", "staging") as git:
        if create:
            with suppress(FileExistsError):
                os.mkdir(_STAGING_NAME, 0o700, dir_fd=git)
        with directory(Path(_STAGING_NAME), "staging", git) as staging:
            yield staging


def staged(clone: Path, sha: str) -> bytes:
    """The staged bytes of ``sha``, which must hash to it.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: staging``.
    """
    try:
        with _staging(clone) as staging:
            payload = read_leaf(staging, sha, "staging")
    except OSError as error:
        raise _mismatch("staging") from error
    if _digest(payload) != sha:
        raise _mismatch("staging")
    return payload


# --- forward -------------------------------------------------------------------------------


def advance(
    clone: Path, journal: PreparationJournal, state: PreparationState
) -> PreparationJournal:
    moved = journal.model_copy(update={"state": state})
    save(clone, moved)
    return moved


def begin(
    clone: Path, transaction: PublicationTransaction, payloads: Mapping[str, bytes]
) -> PreparationJournal:
    """Journal the transaction, then stage every new and every previous byte string.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path``; ``OSError`` on a failed write.
    """
    journal = PreparationJournal(
        state="staging", transaction=transaction, operations=operations_of(transaction)
    )
    save(clone, journal)
    with _staging(clone, create=True) as staging:
        for sha, payload in sorted(payloads.items()):
            write_staged(staging, sha, payload)
    return advance(clone, journal, "staged")


def _make(clone: Path, path: str, sha: str) -> None:
    target = clone / path
    payload = staged(clone, sha)
    folder = clone
    for part in Path(path).parent.parts:
        folder = folder / part
        folder.mkdir(mode=0o755, exist_ok=True)
    with _staging(clone) as staging:
        write_staged(staging, f"{sha}.next", payload)
    replace(clone / STAGING / f"{sha}.next", target)


def _perform(clone: Path, path: str, sha: str, previous: str | None) -> None:
    found = current(clone, path)
    if found == sha:
        return
    if found != previous:
        raise _mismatch("edited")
    _make(clone, path, sha)


def write_all(clone: Path, journal: PreparationJournal) -> PreparationJournal:
    """Documents, then the index and catalogue, then the release manifest; a state after each."""
    for phase, state in _PHASES:
        for item in journal.operations:
            if phase_of(item.path) == phase:
                _perform(clone, item.path, item.sha256, item.previous_sha256)
        journal = advance(clone, journal, state)
    return journal


def verify_proposal(clone: Path, transaction: PublicationTransaction) -> None:
    """The read-back: ``HEAD`` at the base, Git reports exactly the proposal, every byte its digest.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: head|unexpected_change|proposal_bytes``.
    """
    content = transaction.content
    if publication_clone.head_of(clone) != content.publication.base_commit:
        raise _mismatch("head")
    expected = {(" M" if f.previous_sha256 else "??", f.path) for f in content.files}
    reported = publication_clone.changed_paths(clone)
    if len(reported) != len(expected) or set(reported) != expected:
        raise _mismatch("unexpected_change")
    if any(current(clone, item.path) != item.sha256 for item in content.files):
        raise _mismatch("proposal_bytes")


def read_back(clone: Path, journal: PreparationJournal) -> PreparationJournal:
    journal = advance(clone, journal, "reading_back")
    verify_proposal(clone, journal.transaction)
    return advance(clone, journal, "prepared")


# --- back ----------------------------------------------------------------------------------


def _undo(clone: Path, path: str, sha: str, previous: str | None) -> None:
    found = current(clone, path)
    if found == previous:
        return
    if found != sha:
        raise _mismatch("edited")
    if previous is None:
        unlink(clone / path)
    else:
        _make(clone, path, previous)


def _prune(clone: Path) -> None:
    """Remove the publication directories the rollback left empty; ``rmdir`` never follows."""
    for name in _TREE_DIRECTORIES:
        folder = clone / name
        if folder.is_dir() and not folder.is_symlink():
            with suppress(OSError):
                folder.rmdir()


def roll_back(clone: Path, journal: PreparationJournal) -> None:
    """Put every file back as it was; the journal and staging go only once that is proved.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch`` for a file this
            preparation did not write; the journal stays for the next recovery.
    """
    if journal.state != "rolling_back":
        journal = advance(clone, journal, "rolling_back")
    for item in reversed(journal.operations):
        _undo(clone, item.path, item.sha256, item.previous_sha256)
    _prune(clone)
    if any(current(clone, i.path) != i.previous_sha256 for i in journal.operations):
        raise _mismatch("rollback")
    clear(clone)


def clear(clone: Path) -> None:
    """Remove the journal, then the staging, never following a link."""
    journal = clone / JOURNAL
    if os.path.lexists(journal):
        journal.unlink()
    clear_orphan_staging(clone)


def clear_orphan_staging(clone: Path) -> bool:
    """Remove staging no journal names; whether there was any."""
    staging = clone / STAGING
    if not os.path.lexists(staging):
        return False
    if staging.is_symlink() or not staging.is_dir():
        staging.unlink()
    else:
        shutil.rmtree(staging)
    return True


# --- the base as it was --------------------------------------------------------------------


def _tree_files(clone: Path) -> list[str]:
    """Every file below the publication tree's directories, as clone-relative paths."""
    found = []
    for name in _TREE_DIRECTORIES:
        folder = clone / name
        if folder.is_symlink():
            raise _unsafe(name)
        for root, folders, files in os.walk(folder):
            for leaf in [*folders, *files]:
                found.append(Path(root, leaf).relative_to(clone).as_posix())
    return found


def base_files(clone: Path, journal: PreparationJournal | None) -> dict[str, bytes]:
    """The publication tree at the base: what a pending journal changed is read from its staging.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: base_tree`` for anything
            in ``documents/`` or ``releases/`` that is not a publication file
            or an earlier batch's published manifest signature (AR6b).
    """
    changed = {} if journal is None else {i.path: i.previous_sha256 for i in journal.operations}
    found: dict[str, bytes] = {}
    for path in sorted({*_tree_files(clone), "INDEX.md", "catalogue.json"}):
        if path in changed:
            previous = changed[path]
            if previous is not None:
                found[path] = staged(clone, previous)
            continue
        if not os.path.lexists(clone / path):
            continue
        if RELEASE_SIGNATURE_FILE.fullmatch(path) and path not in changed:
            continue  # AR6b: an earlier batch's published manifest signature, never proposed
        if not publication_path(path):
            raise _mismatch("base_tree")
        found[path] = read_file(clone, path)
    return found
