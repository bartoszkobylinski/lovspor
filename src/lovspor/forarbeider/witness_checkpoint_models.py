"""AR7a: the records of an independently pinned witness log (#738, #1037; §9.9 AR7).

- :class:`LogBootstrap` is the owner's approval of one witness log, signed in
  the namespace ``lovspor-forarbeider-log``. Its canonical bytes' digest is the
  **log id**: the log is identified by what the owner approved (repository,
  purpose, a nonce, the witness keys that may sign its checkpoints and the
  authority that pins it), never by a signing key, a namespace or a directory.
  The genesis checkpoint follows from the log id, so approving the bootstrap
  approves the genesis.
- :class:`Checkpoint` is one state of the log: its sequence (the number of
  entries, the tree size), the chained root over every entry, the entry it
  added and the digest of its predecessor. The witness signs it in the
  namespace ``lovspor-forarbeider-checkpoint``.

A signature proves who made a checkpoint, never that it is the newest or the
only one. Freshness and uniqueness come from the authority's pin
(:mod:`~lovspor.forarbeider.witness_checkpoint`).
"""

from hashlib import sha256
from typing import Annotated, Final, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank, canonical_text

BOOTSTRAP_VERSION: Final = "witness-log-bootstrap/1"
CHECKPOINT_VERSION: Final = "witness-checkpoint/1"
#: The one file of an authority commit: the pinned signed checkpoint.
PIN_FILE: Final = "checkpoint.json"
_ROOT_DOMAIN: Final = b"lovspor-witness-log/1\n"

Fingerprint = Annotated[str, StringConstraints(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")]
AuthorityRef = Annotated[
    str, StringConstraints(pattern=r"^refs/heads/witness/[a-z0-9][a-z0-9-]{0,62}$")
]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

Status = Literal[
    "verified",
    "pending_advance",
    "bootstrap_invalid",
    "authority_not_independent",
    "authority_unavailable",
    "authority_not_monotonic",
    "pin_missing",
    "pin_invalid",
    "pin_behind_known",
    "store_unavailable",
    "store_invalid",
    "store_rolled_back",
    "diverged",
    "unpinned_tail",
]
Standing = Literal["current", "stale", "conflicting", "unpinned", "invalid"]


class AuthorityLocator(Frozen):
    """Where the log's latest-state pin lives: one ref of one Git authority."""

    kind: Literal["git_ref"] = "git_ref"
    remote: NonBlank
    ref: AuthorityRef


class LogBootstrap(Frozen):
    """The owner-approved identity of one witness log."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_witness_log_bootstrap"] = "forarbeider_witness_log_bootstrap"
    bootstrap_version: Literal["witness-log-bootstrap/1"] = BOOTSTRAP_VERSION
    repository: NonBlank
    purpose: Slug
    nonce: Digest
    witness_keys: tuple[Fingerprint, ...] = Field(min_length=1)
    authority: AuthorityLocator

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.witness_keys)) != len(self.witness_keys):
            raise ValueError("each witness key is listed once")
        return self


class Checkpoint(Frozen):
    """One state of a witness log."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_witness_checkpoint"] = "forarbeider_witness_checkpoint"
    checkpoint_version: Literal["witness-checkpoint/1"] = CHECKPOINT_VERSION
    log_id: Digest
    sequence: int = Field(ge=0)
    root_sha256: Digest
    entry_sha256: Digest | None
    previous_sha256: Digest | None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        genesis = self.sequence == 0
        if genesis != (self.entry_sha256 is None) or genesis != (self.previous_sha256 is None):
            raise ValueError("only the genesis checkpoint has no entry and no predecessor")
        return self


class SignedCheckpoint(Frozen):
    """A checkpoint and the witness's detached SSH signature over its canonical bytes."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_signed_witness_checkpoint"] = (
        "forarbeider_signed_witness_checkpoint"
    )
    checkpoint: Checkpoint
    signature: NonBlank


def bootstrap_bytes(bootstrap: LogBootstrap) -> bytes:
    return canonical_text(bootstrap).encode()


def log_id_of(bootstrap: LogBootstrap) -> str:
    return sha256(bootstrap_bytes(bootstrap)).hexdigest()


def checkpoint_bytes(checkpoint: Checkpoint) -> bytes:
    """The exact bytes the witness signs and the successor's digest covers."""
    return canonical_text(checkpoint).encode()


def checkpoint_digest(checkpoint: Checkpoint) -> str:
    return sha256(checkpoint_bytes(checkpoint)).hexdigest()


def genesis_root(log_id: str) -> str:
    return sha256(_ROOT_DOMAIN + bytes.fromhex(log_id)).hexdigest()


def chained_root(previous_root: str, entry_sha256: str) -> str:
    return sha256(bytes.fromhex(previous_root) + bytes.fromhex(entry_sha256)).hexdigest()


def genesis(log_id: str) -> Checkpoint:
    """The log's first checkpoint, determined by its id alone."""
    return Checkpoint(
        log_id=log_id,
        sequence=0,
        root_sha256=genesis_root(log_id),
        entry_sha256=None,
        previous_sha256=None,
    )


def successor(previous: Checkpoint, entry_sha256: str) -> Checkpoint:
    """The checkpoint that adds ``entry_sha256`` after ``previous``."""
    return Checkpoint(
        log_id=previous.log_id,
        sequence=previous.sequence + 1,
        root_sha256=chained_root(previous.root_sha256, entry_sha256),
        entry_sha256=entry_sha256,
        previous_sha256=checkpoint_digest(previous),
    )
