"""AR6b: read and create the attestation tags, through the sandboxed adapters only (#738).

One tag per published commit, ``refs/tags/forarbeider-attestation-<commit>``,
naming a root commit of exactly ``attestation.json`` and ``attestation.sig``
(:mod:`~lovspor.forarbeider.publication_attestation_models`):

- **path A**: the in-memory
  :class:`~lovspor.forarbeider.publication_sandbox.SimulatedGitHub`, standing in
  for the Git Data API (blobs, a tree, a commit GitHub signs as the App, then a
  new ref). An existing tag is never moved;
- **path B**: the
  :class:`~lovspor.forarbeider.publication_sandbox.DisposableRemote`; the root
  commit is built in the runner's clone, signed by the bot key in the
  publication namespace, and pushed, never forced, to the new tag.

Reading names what is wrong with the storage itself (who made the storage
commit, whether it is a root, whether it holds exactly the two files and names
the attested commit), separately from whether the attestation is true, which
:mod:`~lovspor.forarbeider.publication_attestation` decides.
"""

import base64
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

from lovspor.forarbeider import publication_path_b, publication_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import (
    PUBLICATION_NAMESPACE,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_attestation_models import (
    ATTESTATION_FILE,
    ATTESTATION_FILES,
    ATTESTATION_SIGNATURE_FILE,
    attestation_message,
    attestation_ref,
)
from lovspor.forarbeider.publication_execution_models import PublicationSettings
from lovspor.forarbeider.publication_path_b import Signer
from lovspor.forarbeider.publication_sandbox import SimulatedGitHub, SimulatedObject


class Stored(NamedTuple):
    """What one attestation tag holds, and what is wrong with its storage (empty when nothing)."""

    attestation: bytes | None
    signature: bytes | None
    storage_reasons: tuple[str, ...]


class Written(NamedTuple):
    status: Literal["created", "exists", "outcome_unknown"]
    reasons: tuple[str, ...]


class Store(NamedTuple):
    """``read(commit, at)``: the tag's content, ``None`` when there is no tag."""

    read: Callable[[str, datetime], Stored | None]
    write: Callable[[str, Mapping[str, bytes], datetime], Written]


_UNREADABLE = Stored(None, None, ("attestation_store_unreadable",))


def _content(files: Mapping[str, bytes], message: str, commit: str) -> Stored:
    reasons = []
    if set(files) != ATTESTATION_FILES:
        reasons.append("storage_files_not_the_attestation")
    if message != attestation_message(commit):
        reasons.append("storage_message_not_the_commits")
    return Stored(
        files.get(ATTESTATION_FILE), files.get(ATTESTATION_SIGNATURE_FILE), tuple(reasons)
    )


# --- path A ------------------------------------------------------------------------------------


def github_store(transport: object, settings: PublicationSettings) -> Store:
    """Path A, through the in-memory endpoint only.

    Raises:
        ForarbeiderRefusedError: ``simulation_transport_not_sandboxed``.
    """
    github = publication_sandbox.verified_transport(transport)
    return Store(
        lambda commit, _at: _github_read(github, settings, commit),
        lambda commit, files, _at: _github_write(github, commit, files),
    )


def _app_reasons(found: SimulatedObject, settings: PublicationSettings) -> list[str]:
    reasons = []
    if found.app is None:
        reasons.append("storage_not_made_by_an_app")
    elif found.app != settings.app:
        reasons.append("storage_not_by_the_pinned_app")
    if not found.verified:
        reasons.append("storage_signature_not_verified")
    return reasons


def _github_read(
    github: SimulatedGitHub, settings: PublicationSettings, commit: str
) -> Stored | None:
    try:
        oid = github.ref(attestation_ref(commit))
        found = github.object(oid) if oid is not None else None
    except Exception:
        return _UNREADABLE
    if oid is None:
        return None
    if found is None:
        return _UNREADABLE
    stored = _content(dict(found.files), found.message, commit)
    return stored._replace(
        storage_reasons=(*_app_reasons(found, settings), *stored.storage_reasons)
    )


def _github_write(github: SimulatedGitHub, commit: str, files: Mapping[str, bytes]) -> Written:
    payload: dict[str, Any] = {
        "ref": attestation_ref(commit),
        "files": {name: base64.b64encode(data).decode("ascii") for name, data in files.items()},
        "message": attestation_message(commit),
    }
    try:
        answer = github.create_ref(payload)
    except Exception:
        return Written("outcome_unknown", ("response_lost",))
    if isinstance(answer, Mapping) and answer.get("message") == "Reference already exists":
        return Written("exists", ("attestation_tag_exists",))
    created = answer.get("object") if isinstance(answer, Mapping) else None
    if isinstance(created, Mapping) and isinstance(created.get("sha"), str):
        return Written("created", ())
    return Written("outcome_unknown", ("response_malformed",))


# --- path B ------------------------------------------------------------------------------------


def bare_store(
    remote: object,
    trust: tuple[TrustRoot, PublicationSettings],
    builder: tuple[Path, Signer] | None = None,
) -> Store:
    """Path B, from the disposable bare remote; ``builder`` (the clone, the bot) only to write.

    Raises:
        ForarbeiderRefusedError: ``simulation_remote_not_disposable``.
    """
    bare = publication_sandbox.verified_remote(remote)
    return Store(
        lambda commit, at: _bare_read(bare, trust, commit, at),
        lambda commit, files, at: _bare_write((bare, builder), commit, files, at),
    )


def _bare_signature(
    raw: bytes, trust: tuple[TrustRoot, PublicationSettings], at: datetime
) -> list[str]:
    root, settings = trust
    payload, signature = publication_path_b.split_signature(raw)
    check = check_signature(root, SignedBytes(payload, signature), PUBLICATION_NAMESPACE, at)
    entry = check.entry
    if not check.verified:
        return [f"storage_signature:{check.reason}"]
    if entry is None or entry.role != "bot_execution" or entry.principal != settings.bot_principal:
        return ["storage_signature:not_the_bot"]
    return []


def _bare_read(
    bare: Path, trust: tuple[TrustRoot, PublicationSettings], commit: str, at: datetime
) -> Stored | None:
    try:
        oid = publication_path_b.remote_ref(bare, attestation_ref(commit))
        if oid is None:
            return None
        raw = publication_path_b.remote_object(bare, "commit", oid)
        tree = publication_path_b.remote_tree(bare, oid)
        files = {
            name: publication_path_b.remote_object(bare, "blob", blob)
            for name, blob in tree.items()
        }
    except ForarbeiderRefusedError:
        return _UNREADABLE
    header, _, message = raw.partition(b"\n\n")
    reasons = _bare_signature(raw, trust, at)
    if any(line.startswith(b"parent ") for line in header.split(b"\n")):
        reasons.append("storage_not_a_root_commit")
    stored = _content(files, message.decode("utf-8", "replace"), commit)
    return stored._replace(storage_reasons=(*reasons, *stored.storage_reasons))


def _bare_write(
    target: tuple[Path, tuple[Path, Signer] | None],
    commit: str,
    files: Mapping[str, bytes],
    at: datetime,
) -> Written:
    bare, builder = target
    if builder is None:
        return Written("outcome_unknown", ("attestation_builder_missing",))
    clone, signer = builder
    try:
        if publication_path_b.remote_ref(bare, attestation_ref(commit)) is not None:
            return Written("exists", ("attestation_tag_exists",))
        made = publication_path_b.attestation_commit(
            clone, files, attestation_message(commit), (signer, at)
        )
    except ForarbeiderRefusedError as error:
        return Written("outcome_unknown", (f"attestation_commit:{error.detail}",))
    sent = publication_path_b.push_attestation(clone, bare, made, attestation_ref(commit))
    if sent.status == "published":
        return Written("created", ())
    if sent.status == "refused":
        return Written("exists", sent.reasons)
    return Written("outcome_unknown", sent.reasons)
