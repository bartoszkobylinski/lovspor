"""AR6a: the bot's detached signature on a release manifest, and its one verifier (#738; §9.3).

**What is signed.** Exactly the bytes of ``releases/<sha256>.json`` as AR4a
renders them (canonical JSON of :class:`ReleaseManifestFile`), the bytes whose
SHA-256 names the file and is the transaction's ``release_manifest_sha256``.
Nothing is added, stripped or re-serialized before signing.

**Where the signature lives.** Detached, never inside the manifest and never in
the published tree: the manifest's digest, and so the transaction id, do not
depend on it, so there is no circularity and no digest changes meaning. It is
kept beside the preparation journal in the AR4a clone,
``.git/lovspor-release-manifest.sig``, written atomically by
:func:`sign_release_manifest`. The manifest field ``signature`` keeps its v1
constant: the manifest bytes embed no signature. Publishing the signature with
the commit, so that a reader or an auditor can verify it, is AR6b's evidence
contract; until then only the runner, which holds it, can.

**How it binds.** The manifest names the repository, the path and its base
commit, the plan, the batch and its sequence, the quarantine set, the policy,
every document and every other file. The signature covers all of that. The
transaction cannot be named inside the manifest (the transaction id is the digest
of content that names the manifest), so it is bound the other way: the verifier
requires the signed bytes to be the transaction's manifest by digest and every
field the two share to agree.

**The verifier** (:func:`verify_manifest_signature`) is the only one; the
authorization, the final check before the send and recovery all call it. It
requires, each refusal named: a signature at all; bytes that are the
transaction's manifest, canonical and readable; each binding equal to the
transaction's, the sealed plan's policy and the configured repository; and an
SSHSIG that ``ssh-keygen -Y verify`` accepts for a key the trust root lists as
the configured bot, in ``lovspor-forarbeider-release``, unrevoked and inside its
validity window at the time given (:func:`~lovspor.forarbeider.policy_trust.check_signature`).
No owner key is involved; nothing here invents cryptography.
"""

import json
import os
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider import publication_stage
from lovspor.forarbeider.corpus_leaves import directory, read_leaf, write_leaf
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import (
    RELEASE_NAMESPACE,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_models import PublicationTransaction, ReleaseManifestFile
from lovspor.forarbeider.publication_path_b import Signer
from lovspor.forarbeider.version_models import canonical_text
from lovspor.forarbeider.version_release_models import PolicyReference

SIGNATURE = ".git/lovspor-release-manifest.sig"
_NAME = Path(SIGNATURE).name


class ManifestClaim(NamedTuple):
    """What the manifest must be: this transaction's, under this policy, for this bot."""

    transaction: PublicationTransaction
    policy: PolicyReference | None
    repository: str
    principal: str


class ManifestSignature(NamedTuple):
    """The verifier's answer: why it fails, empty when it verifies."""

    reasons: tuple[str, ...]
    signature_sha256: str | None
    fingerprint: str | None

    @property
    def verified(self) -> bool:
        return not self.reasons


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


# --- the detached signature, kept beside the journal ------------------------------------------


def manifest_bytes(clone: Path, transaction: PublicationTransaction) -> bytes:
    """The proposed manifest's bytes as the clone holds them; any other bytes refuse.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: manifest_bytes``.
    """
    content = transaction.content
    try:
        data = publication_stage.read_file(clone, content.release_manifest_path)
    except (OSError, ForarbeiderRefusedError) as error:
        raise _refused("manifest_bytes") from error
    if sha256(data).hexdigest() != content.release_manifest_sha256:
        raise _refused("manifest_bytes")
    return data


def renamed(git: int) -> None:
    """The step that makes the signature visible: one rename (a test may stand in to crash)."""
    os.replace(f"{_NAME}.next", _NAME, src_dir_fd=git, dst_dir_fd=git)


def sign_release_manifest(
    clone: Path, transaction: PublicationTransaction, signer: Signer
) -> bytes:
    """§9.3 step 4: sign the prepared manifest's exact bytes and keep the signature, atomically.

    Only a ``prepared`` journal of exactly this transaction, whose read-back
    still proves every byte, is signed. Signing again replaces the signature.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible: manifest_not_prepared`` or
            ``manifest_bytes``; the read-back's own refusals; ``unsafe_output_path``.
    """
    journal = publication_stage.pending_journal(clone)
    if journal is None or journal.state != "prepared" or journal.transaction != transaction:
        raise _refused("manifest_not_prepared")
    publication_stage.verify_proposal(clone, transaction)
    signature = signer.sign(manifest_bytes(clone, transaction), RELEASE_NAMESPACE)
    with directory(clone / ".git", "manifest_signature") as git:
        write_leaf(git, f"{_NAME}.next", signature, "manifest_signature")
        renamed(git)
    return signature


def held_signature(clone: Path) -> bytes | None:
    """The detached signature the runner keeps, ``None`` when it keeps none.

    Raises:
        ForarbeiderRefusedError: ``unsafe_output_path`` for a link, a file others
            can write or one over the size bound.
    """
    try:
        with directory(clone / ".git", "manifest_signature") as git:
            return read_leaf(git, _NAME, "manifest_signature")
    except FileNotFoundError:
        return None


# --- the one verifier ---------------------------------------------------------------------------


def _parsed(manifest: bytes) -> ReleaseManifestFile | str:
    try:
        found = ReleaseManifestFile.model_validate_json(manifest)
    except ValidationError:
        return "manifest_unreadable"
    if canonical_text(found).encode() != manifest:
        return "manifest_not_canonical"
    return found


def _repository(manifest: bytes) -> object:
    try:
        raw = json.loads(manifest)
    except ValueError:
        return None
    return raw.get("repository") if isinstance(raw, dict) else None


def _binding_reasons(found: ReleaseManifestFile, claim: ManifestClaim) -> list[str]:
    content = claim.transaction.content
    others = tuple(item for item in content.files if item.role != "release_manifest")
    pairs = (
        ("manifest_repository_mismatch", found.repository, claim.repository),
        ("manifest_publication_mismatch", found.publication, content.publication),
        ("manifest_plan_mismatch", found.plan_id, content.plan_id),
        (
            "manifest_batch_mismatch",
            (found.batch_id, found.batch_sequence),
            (content.batch_id, content.batch_sequence),
        ),
        ("manifest_quarantine_mismatch", found.quarantine_sha256, content.quarantine_sha256),
        ("manifest_documents_mismatch", found.documents, content.documents),
        ("manifest_files_mismatch", found.files, others),
    )
    reasons = [reason for reason, have, want in pairs if have != want]
    if claim.policy is None or found.policy != claim.policy:
        reasons.append("manifest_policy_mismatch")
    return reasons


def _content_reasons(manifest: bytes, claim: ManifestClaim) -> list[str]:
    content = claim.transaction.content
    reasons = []
    if sha256(manifest).hexdigest() != content.release_manifest_sha256:
        reasons.append("manifest_digest_not_the_transactions")
    if _repository(manifest) not in (claim.repository, None):
        return [*reasons, "manifest_repository_mismatch"]
    found = _parsed(manifest)
    if isinstance(found, str):
        return [*reasons, found]
    return reasons + _binding_reasons(found, claim)


def verify_manifest_signature(
    claim: ManifestClaim,
    signed: SignedBytes,
    root: TrustRoot,
    at: datetime,
) -> ManifestSignature:
    """Whether ``signed`` is the bot's genuine signature on the transaction's manifest at ``at``."""
    if not signed.signature:
        return ManifestSignature(("manifest_signature_missing",), None, None)
    digest = sha256(signed.signature).hexdigest()
    reasons = _content_reasons(signed.message, claim)
    check = check_signature(root, signed, RELEASE_NAMESPACE, at)
    entry = check.entry
    if not check.verified:
        reasons.append(f"manifest_signature:{check.reason}")
    elif entry is None or entry.role != "bot_execution" or entry.principal != claim.principal:
        reasons.append("manifest_signature:not_the_bot")
    fingerprint = entry.fingerprint if check.verified and entry is not None else None
    return ManifestSignature(tuple(sorted(set(reasons))), digest, fingerprint)


def verify_proposed(
    clone: Path, claim: ManifestClaim, root: TrustRoot, at: datetime
) -> ManifestSignature:
    """The verifier on the proposed bytes: the clone's manifest and the signature held for it."""
    try:
        held = held_signature(clone)
    except ForarbeiderRefusedError:
        return ManifestSignature(("manifest_signature_unreadable",), None, None)
    try:
        manifest = manifest_bytes(clone, claim.transaction)
    except ForarbeiderRefusedError:
        return ManifestSignature(("manifest_bytes_not_the_proposal",), None, None)
    return verify_manifest_signature(claim, SignedBytes(manifest, held or b""), root, at)
