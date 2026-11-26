"""AR5d-1: the workspace identity record and its file (#738, #1013; §9.9 AR5).

A publication workspace names itself with one immutable, canonical record,
``<workspace>/workspace-identity.json``:

- a random 256-bit ``workspace_id``, drawn once when the identity is made;
- the ``repository`` it publishes to and the publication ``domain``;
- the identity's version and the time it was made.

The **scope** of the workspace's anchored families is ``workspace:`` followed
by the SHA-256 of the record's exact bytes, so the repository, the domain and
the random id are all bound into every anchor key, and the path is not. The
record is a name, never a credential: nothing here trusts it. It counts only
once the witness has anchored it, as the one record of the
``workspace_identity`` family in that same scope (see
:func:`~lovspor.forarbeider.control_plane_anchor.workspace_scope`).

The file is written once: a private temporary file is filled and flushed, then
hard-linked to the final name, which is never replaced, and the folder is
flushed. A crash leaves either no identity or the whole one, and possibly a
temporary leftover, which is never read as an identity and is removed under
the workspace lock before the next attempt.
"""

import os
import secrets
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal

from pydantic import ValidationError

from lovspor.forarbeider.corpus_leaves import directory, read_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank, canonical_text

IDENTITY_FILE: Final = "workspace-identity.json"
IDENTITY_VERSION: Final = "workspace-identity/1"
DOMAIN: Final = "forarbeider-publication"
SCOPE_PREFIX: Final = "workspace:"
#: Names in a workspace that hold no workspace records: the lock and the identity itself.
_HOUSEKEEPING: Final = frozenset({"workspace.lock", IDENTITY_FILE})
_TEMPORARY: Final = f".{IDENTITY_FILE}.tmp-"
_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


class WorkspaceIdentity(Frozen):
    """The workspace's name: a random id bound to one repository and one publication domain."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_workspace_identity"] = "forarbeider_workspace_identity"
    identity_version: Literal["workspace-identity/1"] = IDENTITY_VERSION
    workspace_id: Digest
    repository: NonBlank
    domain: Literal["forarbeider-publication"] = DOMAIN
    created_at: Instant


def refused(why: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, f"workspace_identity:{why}")


def identity_bytes(identity: WorkspaceIdentity) -> bytes:
    return canonical_text(identity).encode()


def scope_of(raw: bytes) -> str:
    """The anchor scope of the workspace whose identity is exactly ``raw``."""
    return f"{SCOPE_PREFIX}{sha256(raw).hexdigest()}"


def new_identity(repository: str, now: datetime) -> WorkspaceIdentity:
    return WorkspaceIdentity(
        workspace_id=secrets.token_hex(32), repository=repository, created_at=now
    )


def read_identity(workspace: Path) -> bytes | None:
    """The identity file's exact bytes, or ``None`` when there is none.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: workspace_identity:unsafe``
            for a link, a shared or a hard-linked file, or a workspace that is
            not a private directory.
    """
    try:
        with directory(workspace, "workspace") as parent:
            return read_leaf(parent, IDENTITY_FILE, "workspace_identity")
    except FileNotFoundError:
        return None
    except ForarbeiderRefusedError as error:
        raise refused("unsafe") from error


def parse_identity(raw: bytes, repository: str) -> WorkspaceIdentity:
    """The identity in ``raw``, canonical and for ``repository``; anything else is refused.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: workspace_identity:``
            ``unreadable``, ``not_canonical`` or ``repository_mismatch``.
    """
    try:
        identity = WorkspaceIdentity.model_validate_json(raw)
    except ValidationError as error:
        raise refused("unreadable") from error
    if identity_bytes(identity) != raw:
        raise refused("not_canonical")
    if identity.repository != repository:
        raise refused("repository_mismatch")
    return identity


def write_identity(workspace: Path, raw: bytes) -> None:
    """Write the identity once, whole; an existing identity is never replaced.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: workspace_identity:exists``.
        OSError: the write itself failed.
    """
    with directory(workspace, "workspace") as parent:
        temporary = f"{_TEMPORARY}{secrets.token_hex(8)}"
        descriptor = os.open(temporary, _CREATE, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, IDENTITY_FILE, src_dir_fd=parent, dst_dir_fd=parent)
        except FileExistsError as error:
            raise refused("exists") from error
        finally:
            os.unlink(temporary, dir_fd=parent)
        os.fsync(parent)


def _names(parent: int) -> list[str]:
    with os.scandir(parent) as entries:
        return [entry.name for entry in entries]


def remove_leftovers(workspace: Path) -> None:
    """Remove temporary identity files a crash left; call only under the workspace lock."""
    with directory(workspace, "workspace") as parent:
        for name in _names(parent):
            if name.startswith(_TEMPORARY):
                os.unlink(name, dir_fd=parent)


def held_records(workspace: Path) -> tuple[str, ...]:
    """The names in the workspace that are not the lock, the identity or a leftover."""
    with directory(workspace, "workspace") as parent:
        names = _names(parent)
    return tuple(
        sorted(n for n in names if n not in _HOUSEKEEPING and not n.startswith(_TEMPORARY))
    )
