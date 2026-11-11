"""The private release workspace and the files a preflight reads (#738, PR3B-1).

``<workspace>/<folder>/<sha256>.json``: every record is named by the digest of
its exact bytes and taken by ``os.link``, so it is never replaced and a retry
of the same record changes nothing. A revocation is named by the authorization
it revokes, which is how a preflight finds it without being told.

Every input is read through one open, owner-only, no-follow descriptor, with a
size bound. A path is refused unless it is already its own resolved form, so a
symlink anywhere on the way is refused rather than followed, and unless no
ancestor is a Git checkout.
"""

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import digest, record_text
from lovspor.forarbeider.staging import (
    checked_directory,
    ensure_private_directory,
    private_directory_exists,
    read_private,
    write_observation,
)

#: A record lists ids and digests; even hundreds of chapters stay far below this.
MAX_RECORD_BYTES = 1024 * 1024
#: An evidence file is a note or a scanned letter, not a corpus.
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
#: The bound PR3A puts on a staged chapter result, whose page is at most 16 MiB.
MAX_STAGED_BYTES = 64 * 1024 * 1024

Folder = Literal[
    "dossiers",
    "authorizations",
    "revocations",
    "reports",
    "plans",
    "previews",
    "withdrawal_intents",
    "withdrawals",
    "policies",
    "policy_revocations",
    "batch_plans",
    "batch_confirmations",
    "batch_releases",
    "release_withdrawal_intents",
    "release_withdrawals",
]


@contextmanager
def refused_as(label: str) -> Iterator[None]:
    """A refusal keeps its reason but names the input's role: a path can carry a name.

    Raises:
        ForarbeiderRefusedError: the reason raised inside, with ``label`` as its detail.
    """
    try:
        yield
    except ForarbeiderRefusedError as error:
        raise ForarbeiderRefusedError(error.reason, label) from None


def checked_file(raw: Path, forbidden: Sequence[Path]) -> Path:
    """An absolute path to a file outside every repository, with no symlink on the way.

    Raises:
        ForarbeiderRefusedError: ``unsafe_input_path``, or ``unsafe_output_path``
            for a path inside a repository or a forbidden tree.
    """
    refused = ForarbeiderRefusedError(RefusalReason.UNSAFE_INPUT_PATH, str(raw))
    if not raw.is_absolute():
        raise refused
    try:
        resolved = raw.resolve()
    except (OSError, RuntimeError) as error:
        raise refused from error
    if resolved != raw:
        raise refused
    checked_directory(raw.parent, forbidden)
    return raw


def read_file(raw: Path, forbidden: Sequence[Path], limit: int) -> bytes | None:
    """The bytes at ``raw``, or ``None`` when nothing is there.

    Raises:
        ForarbeiderRefusedError: an unsafe path or file, or one over ``limit``.
    """
    path = checked_file(raw, forbidden)
    if not os.path.lexists(path):
        return None
    return read_private(path, limit)


def read_model[T: BaseModel](raw: Path, forbidden: Sequence[Path], model: type[T]) -> T:
    """A private JSON file of schema ``model``, which must be there.

    Raises:
        ForarbeiderRefusedError: missing, unsafe, too large or not this schema.
    """
    payload = read_file(raw, forbidden, MAX_STAGED_BYTES)
    if payload is None:
        raise ForarbeiderRefusedError(RefusalReason.INPUT_UNREADABLE, str(raw))
    try:
        return model.model_validate_json(payload)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, str(raw)) from error


def open_workspace(raw: Path, forbidden: Sequence[Path]) -> Path:
    """The owner-only workspace directory, created when absent.

    Raises:
        ForarbeiderRefusedError: inside a repository, or reachable by others.
    """
    path = checked_directory(raw, forbidden)
    ensure_private_directory(path)
    return path


def write_record(workspace: Path, folder: Folder, record: BaseModel, name: str = "") -> str:
    """Store ``record`` under its digest, or under ``name``, and return the digest.

    Raises:
        ForarbeiderRefusedError: ``observation_exists`` when ``name`` already
            holds other bytes, or an unsafe path.
    """
    text = record_text(record)
    sha = digest(text.encode())
    directory = workspace / folder
    with refused_as(folder):
        ensure_private_directory(directory)
        write_observation(directory / f"{name or sha}.json", text)
    return sha


def read_named[T: BaseModel](
    workspace: Path, folder: Folder, name: str, model: type[T]
) -> T | None:
    """The record stored as ``name``, or ``None``; its bytes must be exactly as written.

    Raises:
        ForarbeiderRefusedError: ``release_record_corrupt``, or an unsafe path.
    """
    directory = workspace / folder
    path = directory / f"{name}.json"
    with refused_as(name):
        if not private_directory_exists(directory) or not os.path.lexists(path):
            return None
        raw = read_private(path, MAX_RECORD_BYTES)
    try:
        record = model.model_validate_json(raw)
    except ValidationError as error:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_CORRUPT, name) from error
    if record_text(record).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_CORRUPT, name)
    return record


def read_record[T: BaseModel](
    workspace: Path, folder: Folder, sha: str, model: type[T]
) -> T | None:
    """The record whose bytes have digest ``sha``, or ``None`` when it is not stored.

    Raises:
        ForarbeiderRefusedError: ``release_record_corrupt``: the file was edited.
    """
    record = read_named(workspace, folder, sha, model)
    if record is not None and digest(record_text(record).encode()) != sha:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_RECORD_CORRUPT, sha)
    return record


def write_preview(workspace: Path, text: str) -> str:
    """Keep a planned file's bytes, owner-only and named by their digest, for the owner to read.

    Raises:
        ForarbeiderRefusedError: an unsafe workspace, or the name holding other bytes.
    """
    sha = digest(text.encode())
    directory = workspace / "previews"
    with refused_as("previews"):
        ensure_private_directory(directory)
        write_observation(directory / f"{sha}.preview", text)
    return sha
