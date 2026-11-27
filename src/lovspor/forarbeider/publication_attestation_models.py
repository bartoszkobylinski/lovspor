"""AR6b: the bot-signed publication attestation and where it is published (#738; §9.3 A8, B11).

**What it attests.** One commit on ``main`` of the one repository, after the
runner verified it from the remote and receipted it ``published_authorized``.
It keeps two kinds of statement apart:

- :class:`RemoteFacts`: what anyone can re-derive from the remote alone, and a
  reader does: the repository, branch and path, the commit and its parent, the
  signature class (and the App on path A), the release manifest's path and
  digest, the published manifest signature's path, digest and bot key, every
  file the commit changed with its SHA-256, and what the published manifest
  names (policy, plan, batch). The reader rebuilds these from the remote and
  requires them equal, field for field;
- :class:`RunnerClaims`: what only the runner's private records say: the
  transaction and attempt, the attempt time, the remote event time as the
  runner read it, the authorization verdict and what it waived, and the
  receipt it rests on (sequence, version and the SHA-256 of its canonical
  bytes). A reader can check them against nothing public; they are reported as
  claims, never as verified.

The attestation holds **no time of its own making**, so the same receipt and
the same remote give the same bytes, and the bot's Ed25519 SSHSIG over them is
the same bytes too: a retry re-creates exactly what a crash left behind.

**No circularity.** It names the published commit, so it cannot be in that
commit (the commit id is the digest of its tree). It lives after it:

**Where it lives.** A create-only tag ``refs/tags/forarbeider-attestation-<commit>``
on the same repository, pointing at a root commit (no parent) whose tree is
exactly two files, :data:`ATTESTATION_FILE` (the canonical bytes) and
:data:`ATTESTATION_SIGNATURE_FILE` (the bot's SSHSIG over them in the namespace
``lovspor-forarbeider-publication``). Discovery is by name: a reader that has
the commit id knows the one tag to read. A tag is never moved or deleted:
path B's push is never forced, and the simulated remote refuses non-fast-forward
updates and deletions; on GitHub a tag ruleset must restrict updates and
deletions of ``forarbeider-attestation-*`` (AR7 verifies it; until then that
is an expectation, not a fact).

**How storage is authenticated.** The bytes authenticate themselves: the
reader verifies the bot's signature against the trust root, and requires the
attestation to name the tag's commit, so an attestation copied under another
commit's tag, or from another repository, is refused. The storage commit is
checked besides: path A, GitHub verified its signature for the pinned App;
path B, the bot key signed it in the publication namespace. The store can
still **withhold**: a missing tag is read as *unattested*, never as attested,
and the publication's own facts then stand without the bot's statement.
"""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from lovspor.forarbeider.policy_authorization_models import Instant
from lovspor.forarbeider.publication_models import (
    REPOSITORY,
    CommitId,
    release_signature_path,
)
from lovspor.forarbeider.version_models import Digest, Frozen, NonBlank
from lovspor.forarbeider.version_release_models import PolicyReference

ATTESTATION_VERSION: Final = "publication-attestation/1"
ATTESTATION_TAG: Final = "refs/tags/forarbeider-attestation-"
ATTESTATION_FILE: Final = "attestation.json"
ATTESTATION_SIGNATURE_FILE: Final = "attestation.sig"
ATTESTATION_FILES: Final = frozenset({ATTESTATION_FILE, ATTESTATION_SIGNATURE_FILE})


def attestation_ref(commit: str) -> str:
    """The one tag that may hold the attestation of ``commit``."""
    return f"{ATTESTATION_TAG}{commit}"


def attestation_message(commit: str) -> str:
    """The storage commit's message: it names the attested commit, nothing else."""
    return f"forarbeider attestation {commit}\n"


class AttestedFile(Frozen):
    path: NonBlank
    sha256: Digest


class RemoteFacts(Frozen):
    """What the remote shows; a reader re-derives every field and requires it equal."""

    repository: Literal["bartoszkobylinski/lovverk-forarbeider"] = REPOSITORY
    branch: Literal["main"] = "main"
    path: Literal["A", "B"]
    signature_class: Literal["github_app", "bot_key"]
    app_id: int | None = Field(ge=1)
    commit: CommitId
    parent: CommitId
    release_manifest_path: NonBlank
    release_manifest_sha256: Digest
    release_manifest_signature_path: NonBlank
    release_manifest_signature_sha256: Digest
    manifest_signer_fingerprint: NonBlank
    files: tuple[AttestedFile, ...] = Field(min_length=3)
    policy: PolicyReference
    plan_id: Digest
    batch_id: NonBlank
    batch_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.path == "A") != (self.signature_class == "github_app"):
            raise ValueError("path A is the github_app class, path B the bot_key class")
        if (self.path == "A") != (self.app_id is not None):
            raise ValueError("path A names its App; path B names none")
        digest = self.release_manifest_sha256
        if self.release_manifest_path != f"releases/{digest}.json":
            raise ValueError("the manifest is named by its digest")
        if self.release_manifest_signature_path != release_signature_path(digest):
            raise ValueError("the signature sits beside the manifest it signs")
        paths = [item.path for item in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("files are sorted by path, once each")
        if not {self.release_manifest_path, self.release_manifest_signature_path} <= set(paths):
            raise ValueError("the manifest and its signature are among the changed files")
        return self


class RunnerClaims(Frozen):
    """What only the runner's private records say; reported, never verified by a reader."""

    transaction_id: Digest
    attempt_id: Digest | None
    attempt_at: Instant | None
    remote_event_at: Instant | None
    authorization: Literal["authorized"]
    authorization_waived: tuple[NonBlank, ...]
    receipt_version: Literal["publication-receipt/2"]
    receipt_sequence: int = Field(ge=1)
    receipt_sha256: Digest
    recovered_from_remote: bool


class PublicationAttestation(Frozen):
    """The bot's statement that one commit is a verified, authorized publication."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_attestation"] = (
        "forarbeider_publication_attestation"
    )
    attestation_version: Literal["publication-attestation/1"] = ATTESTATION_VERSION
    mode: Literal["simulation"] = "simulation"
    remote: RemoteFacts
    claims: RunnerClaims
    #: What no reader can verify from the remote and the trust root alone.
    not_independently_verifiable: tuple[NonBlank, ...] = (
        "ar7_attestation_tag_protection",
        "ar7_repository_settings",
        "authorization_at_publication",
        "budget_settlement",
        "policy_owner_signature",
    )
