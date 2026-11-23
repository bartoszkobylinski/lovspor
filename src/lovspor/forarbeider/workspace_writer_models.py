"""AR5d-1: the workspace writer's claims and releases, as records (#738, #1013; §9.9 AR5).

``<workspace>/workspace-writer/<sequence>.json`` is an append-only,
hash-linked chain, anchored in the scope of the workspace's identity as the
``workspace_writer`` family (:mod:`~lovspor.forarbeider.workspace_writer`):

- a ``claim`` names a fresh random ``claim_id``, the transaction and attempt it
  is made for, when it was made and the end of its lease;
- a ``release`` names the claim it ends.

What may follow what is checked when the chain is read, as well as when a
record is appended: the first record is a claim; a release ends the claim
just before it; a claim follows a release, or a claim whose lease has run
out (a takeover after a crash); time never runs backwards. Records are created
once, owner-only, and never replaced or removed.
"""

import os
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, ValidationError, model_validator

from lovspor.forarbeider.corpus_leaves import directory, read_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.publication_attempts import create_once
from lovspor.forarbeider.version_models import Digest, Frozen, canonical_text

FOLDER: Final = "workspace-writer"
#: How long a claim excludes every other writer of the identity, unless released first.
LEASE: Final = timedelta(seconds=300)
#: The least lease a claim must have left when its send begins.
SEND_MARGIN: Final = timedelta(seconds=60)


class WriterEvent(Frozen):
    """One claim of the workspace's single writer slot, or the release of one."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_workspace_writer_event"] = (
        "forarbeider_workspace_writer_event"
    )
    sequence: int = Field(ge=1)
    previous_sha256: Digest | None
    kind: Literal["claim", "release"]
    claim_id: Digest
    transaction_id: Digest
    attempt_id: Digest
    at: Instant
    lease_until: Instant | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.sequence == 1) != (self.previous_sha256 is None):
            raise ValueError("only the first event has no previous event")
        if (self.kind == "claim") != (self.lease_until is not None):
            raise ValueError("exactly a claim has a lease")
        if self.lease_until is not None and self.lease_until <= self.at:
            raise ValueError("a lease ends after its claim")
        return self


def _refused() -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "workspace_writer")


def event_bytes(event: WriterEvent) -> bytes:
    return canonical_text(event).encode()


def follows(previous: WriterEvent | None, event: WriterEvent) -> bool:
    """Whether ``event`` may come right after ``previous`` in the chain."""
    if previous is None:
        return event.kind == "claim"
    if event.at < previous.at:
        return False
    if event.kind == "release":
        return previous.kind == "claim" and previous.claim_id == event.claim_id
    lease = previous.lease_until
    return previous.kind == "release" or (lease is not None and event.at >= lease)


def _folder(workspace: Path) -> Path:
    folder = workspace / FOLDER
    if os.path.lexists(folder) and (folder.is_symlink() or not folder.is_dir()):
        raise _refused()
    return folder


def _names(folder: Path) -> list[str]:
    if not os.path.lexists(folder):
        return []
    names = sorted(item.name for item in folder.iterdir())
    if names != [f"{n:06d}.json" for n in range(1, len(names) + 1)]:
        raise _refused()
    return names


def _event(folder: Path, name: str) -> tuple[WriterEvent, bytes]:
    try:
        with directory(folder, "workspace_writer") as parent:
            raw = read_leaf(parent, name, "workspace_writer")
        event = WriterEvent.model_validate_json(raw)
    except (OSError, ValidationError, ForarbeiderRefusedError) as error:
        raise _refused() from error
    if event_bytes(event) != raw:
        raise _refused()
    return event, raw


def chain(workspace: Path) -> list[tuple[WriterEvent, bytes]]:
    """Every event and its exact bytes, in order, each proved linked to and allowed after the last.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: workspace_writer``.
    """
    folder = _folder(workspace)
    found: list[tuple[WriterEvent, bytes]] = []
    for index, name in enumerate(_names(folder), start=1):
        event, raw = _event(folder, name)
        previous = found[-1] if found else None
        link = sha256(previous[1]).hexdigest() if previous else None
        linked = (event.sequence, event.previous_sha256) == (index, link)
        if not linked or not follows(previous[0] if previous else None, event):
            raise _refused()
        found.append((event, raw))
    return found


def head(workspace: Path) -> tuple[int, str | None, WriterEvent | None]:
    """The next sequence, the digest the next event must name, and the newest event."""
    found = chain(workspace)
    if not found:
        return 1, None, None
    return len(found) + 1, sha256(found[-1][1]).hexdigest(), found[-1][0]


def append(workspace: Path, event: WriterEvent) -> None:
    """Persist ``event`` as the next one: linked, and allowed after the newest.

    Raises:
        ForarbeiderRefusedError: ``corpus_tree_mismatch: workspace_writer`` when it is not.
    """
    sequence, previous, newest = head(workspace)
    if (event.sequence, event.previous_sha256) != (sequence, previous):
        raise _refused()
    if not follows(newest, event):
        raise _refused()
    folder = _folder(workspace)
    if not os.path.lexists(folder):
        folder.mkdir(mode=0o700)
    create_once(folder, f"{event.sequence:06d}.json", event_bytes(event))
