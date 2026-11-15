"""The owner's publication attestation for a lovverk commit (#738, B2).

What the B2 reader can know, kept apart:

1. **A clean, committed local checkout.** The B1a guard proves the clone is a
   trusted lovverk clone on ``main``, clean, with the dataset exactly its
   manifest. That is local state only.
2. **A matching local remote-tracking ref.** ``refs/remotes/origin/main`` is
   a file in the clone that ``git update-ref`` moves offline. It proves
   nothing about the remote and is **not** used: the reader never reads it.
3. **Independently verified remote publication.** Needs the network or a
   signature the remote makes. Neither exists here, so the reader never claims it
   (``remote_verified: false``).

What stands in, explicitly: the **owner's attestation**, made at a terminal
after they saw the commit on lovverk ``main`` (on GitHub, by their own
means). It names that commit, the forarbeider manifest digest at it, the
branch and the repository, and is stored in the owner-only private workspace,
``publications/<commit>.json``, outside the mutable checkout, written once.
``attest`` refuses unless the clone, clean on ``main``, has exactly that
commit as HEAD, so the attestation binds the bytes the owner had in front of
them. It is the owner's word: ``basis: owner_asserted_unverified``,
``identity_authenticated: false``, ``remote_verified: false``. Anyone who can
write the private workspace can write one; that directory is the trust
boundary, as for every other release record.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.apply_preflight import REMOTE, existing_workspace
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_checkout import CheckoutState, verify_checkout
from lovspor.forarbeider.release_commands import ReleaseScope, actor
from lovspor.forarbeider.release_models import SHA256
from lovspor.forarbeider.release_store import read_named, write_record

MAIN: Literal["main"] = "main"
_COMMIT = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_Digest = Annotated[str, Field(pattern=SHA256)]


class PublicationAttestation(BaseModel):
    """The owner's word that this commit, with this dataset, is on lovverk ``main``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    record_type: Literal["forarbeider_publication_attestation"]
    repository: Literal["github.com/bartoszkobylinski/lovverk"]
    branch: Literal["main"]
    commit: Annotated[str, Field(pattern=_COMMIT)]
    manifest_sha256: _Digest | None
    owner: str = Field(min_length=1)
    attested_at: AwareDatetime
    basis: Literal["owner_asserted_unverified"]
    remote_verified: Literal[False]
    identity_authenticated: Literal[False]

    @field_validator("attested_at")
    @classmethod
    def _in_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AttestRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    checkout: Path
    commit: str
    decided: str


def _now() -> datetime:
    return datetime.now(UTC)


def attestation_for(workspace: Path, state: CheckoutState) -> PublicationAttestation:
    """The stored attestation of HEAD, which must name this dataset.

    Raises:
        ForarbeiderRefusedError: ``release_not_published: attestation`` for none,
            ``release_record_corrupt`` for edited bytes, ``release_not_published:
            attestation_mismatch`` for another commit, branch or dataset.
    """
    found = read_named(workspace, "publications", state.head_commit, PublicationAttestation)
    if found is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_PUBLISHED, "attestation")
    expected = (state.head_commit, MAIN, state.base_manifest_sha256)
    if (found.commit, found.branch, found.manifest_sha256) != expected:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_PUBLISHED, "attestation_mismatch")
    return found


def _attestation(state: CheckoutState, owner: tuple[str, datetime]) -> PublicationAttestation:
    return PublicationAttestation(
        schema_version=1,
        record_type="forarbeider_publication_attestation",
        repository=REMOTE,
        branch=MAIN,
        commit=state.head_commit,
        manifest_sha256=state.base_manifest_sha256,
        owner=owner[0],
        attested_at=owner[1],
        basis="owner_asserted_unverified",
        remote_verified=False,
        identity_authenticated=False,
    )


def _checked(ask: AttestRequest) -> tuple[str, datetime]:
    """Raises: ForarbeiderRefusedError: ``release_invalid: commit``, ``invalid_timestamp``."""
    if re.fullmatch(_COMMIT, ask.commit) is None:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_INVALID, "commit")
    handle, at = actor(ask.decided)
    if at > _now():
        raise ForarbeiderRefusedError(RefusalReason.INVALID_TIMESTAMP, "decided")
    return handle, at


def attest_impl(scope: ReleaseScope, ask: AttestRequest) -> str:
    """Record the owner's publication attestation for the clean ``main`` HEAD they name.

    Writes only the private workspace; the same attestation again is a no-op.

    Raises:
        ForarbeiderRefusedError: an invalid commit or moment, the guard's refusal,
            ``release_not_published: commit`` when HEAD is not the commit named,
            or no phrase at a terminal.
    """
    owner = _checked(ask)
    workspace = existing_workspace(scope)
    state = verify_checkout(ask.checkout, MAIN)
    if state.head_commit != ask.commit:
        raise ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_PUBLISHED, "commit")
    existing = read_named(workspace, "publications", ask.commit, PublicationAttestation)
    if existing is not None and existing.manifest_sha256 == state.base_manifest_sha256:
        return _lines(existing, "UNCHANGED")
    record = _attestation(state, owner)
    manifest = (state.base_manifest_sha256 or "absent")[:12]
    phrase = f"attest {ask.commit[:12]} on main of {REMOTE} manifest {manifest}"
    release_commands.require_a_person(phrase, "publication")
    write_record(workspace, "publications", record, ask.commit)
    return _lines(record, "ATTESTED")


def _lines(record: PublicationAttestation, verdict: str) -> str:
    return "\n".join(
        [
            f"{verdict} commit {record.commit} branch {record.branch} {record.repository}",
            f"manifest {record.manifest_sha256 or 'absent'}",
            "basis owner_asserted_unverified remote_verified=false identity_authenticated=false",
        ]
    )
