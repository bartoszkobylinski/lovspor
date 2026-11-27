"""AR6b: verify a publication from the remote alone, and attest a verified one (#738; §9.3 A8, B11).

**The evidence graph.** Each arrow is a verification, and nothing points back:

1. policy → manifest bytes: the manifest names the policy (its id, version and
   digest); the owner's signature on the policy is not published, so a reader
   reports the reference as the manifest's statement (AR8 verifies it);
2. manifest bytes → manifest signature: the bot's AR6a SSHSIG
   (``lovspor-forarbeider-release``), published beside the manifest in the same
   commit as ``releases/<digest>.sig``. Neither the manifest's digest nor the
   transaction's depends on it;
3. manifest + remote commit → verified receipt: the commit's parent is the
   base the manifest names, its signature class is the configured path's, and
   it changes exactly the files the manifest lists, the manifest and its
   signature (recovery, :mod:`~lovspor.forarbeider.publication_remote_recovery`);
4. verified publication facts → publication attestation: the bot's SSHSIG
   (``lovspor-forarbeider-publication``) over the facts and the receipt it rests
   on, in the commit's own create-only tag, after the commit exists.

**The independent reader** (:func:`verify_publication`) needs only the remote,
the trust root and the expected settings: no clone, no workspace, no receipt.
It finds the commit on ``main``, re-derives every :class:`RemoteFacts` field
from the commit and its manifest, verifies the manifest's published signature,
then reads the commit's attestation tag. Its answer is one of:

- ``attested``: the facts verify, and the bot's attestation verifies and states
  exactly these facts;
- ``unattested``: the facts verify, and there is no attestation (never written,
  withheld or removed). It is **never** reported as attested;
- ``invalid``: a fact fails, or an attestation is there and fails (another
  commit's, another key, another namespace, a fact that differs, bad storage);
- ``not_found``: the commit is not on ``main``, or cannot be read.

What it cannot verify, and says so on every answer: the policy's owner
signature, the authorization at the publication event, the budget settlement
and the AR7 repository settings, the tag protection included. The runner's
claims in an attestation are returned as claims.

**Attesting** (:func:`attest`) happens only after recovery receipted the commit
``published_authorized`` and only after the reader, run now on the remote,
re-derives the very facts the receipt names. The bytes are deterministic, so
attesting is create-only, idempotent and crash-recoverable without a journal:
a tag that already holds exactly these bytes is ``already_attested``; one
holding other bytes, or facts the remote now contradicts, is a conflict and sets
the stop latch. An unknown outcome is read back; nothing is ever republished,
and ``main`` is never touched.
"""

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider import publication_anchors, publication_attempts, publication_receipts
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import (
    PUBLICATION_NAMESPACE,
    RELEASE_NAMESPACE,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_attestation_models import (
    ATTESTATION_FILE,
    ATTESTATION_SIGNATURE_FILE,
    AttestedFile,
    PublicationAttestation,
    RemoteFacts,
    RunnerClaims,
)
from lovspor.forarbeider.publication_attestation_store import (
    Store,
    Stored,
    bare_store,
    github_store,
)
from lovspor.forarbeider.publication_execute import ExecutionRequest, Simulation
from lovspor.forarbeider.publication_execution_models import PublicationSettings
from lovspor.forarbeider.publication_manifest_signature import parsed_manifest
from lovspor.forarbeider.publication_models import (
    RELEASE_FILE,
    ReleaseManifestFile,
    release_signature_path,
)
from lovspor.forarbeider.publication_path_a import headline_for
from lovspor.forarbeider.publication_path_b import Signer
from lovspor.forarbeider.publication_recovery_models import RecoveryReceipt
from lovspor.forarbeider.publication_remote import Reader, RemoteCommit, bare_reader, github_reader
from lovspor.forarbeider.version_models import canonical_text

#: How far back from ``main`` the reader looks for the commit.
REACH_LIMIT: Final = 1024
ProofStatus = Literal["attested", "unattested", "invalid", "not_found"]
AttestStatus = Literal["attested", "already_attested", "attestation_unknown"]


class PublicSource(NamedTuple):
    """The remote and nothing else: ``main`` and the attestation tags."""

    main: Reader
    attestations: Store


class PublicationProof(NamedTuple):
    status: ProofStatus
    commit: str
    facts: RemoteFacts | None
    attestation: PublicationAttestation | None
    reasons: tuple[str, ...]

    @property
    def claims(self) -> RunnerClaims | None:
        """The runner's claims in a verified attestation: reported, never verified."""
        return self.attestation.claims if self.attestation is not None else None


class AttestOutcome(NamedTuple):
    status: AttestStatus
    commit: str
    attestation: PublicationAttestation
    reasons: tuple[str, ...]


def _not_eligible(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.RELEASE_NOT_ELIGIBLE, detail)


class Expected(NamedTuple):
    """What the reader verifies against: the trust root and the expected settings."""

    root: TrustRoot
    settings: PublicationSettings


def public_source(
    environment: Simulation, expected: Expected, builder: tuple[Path, Signer] | None = None
) -> PublicSource:
    """The remote of the configured path, through an adapter proved sandboxed first.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` for a missing or unsandboxed adapter.
    """
    root, settings = expected
    if settings.path == "A":
        if environment.transport is None:
            raise _not_eligible("adapter")
        return PublicSource(
            github_reader(environment.transport, settings),
            github_store(environment.transport, settings),
        )
    if environment.remote is None:
        raise _not_eligible("adapter")
    return PublicSource(
        bare_reader(environment.remote, root, settings),
        bare_store(environment.remote, (root, settings), builder),
    )


# --- the independent reader -------------------------------------------------------------------


def _on_main(main: Reader, commit: str) -> RemoteCommit | None:
    try:
        oid = main.head()
        for _ in range(REACH_LIMIT):
            if oid is None:
                return None
            found = main.commit(oid)
            if found is None:
                return None
            if oid == commit:
                return found
            oid = found.parents[0] if len(found.parents) == 1 else None
    except ForarbeiderRefusedError:
        return None
    return None


def _signed_by_bot(
    signed: SignedBytes, namespace: str, trust: tuple[TrustRoot, str, datetime]
) -> tuple[list[str], str | None]:
    root, principal, at = trust
    check = check_signature(root, signed, namespace, at)
    entry = check.entry
    if not check.verified:
        return [check.reason], None
    if entry is None or entry.role != "bot_execution" or entry.principal != principal:
        return ["not_the_bot"], None
    return [], entry.fingerprint


def _manifest_of(commit: RemoteCommit) -> tuple[str, bytes] | str:
    releases = [path for path in commit.files if RELEASE_FILE.fullmatch(path)]
    raw = commit.files.get(releases[0]) if len(releases) == 1 else None
    if raw is None:
        return "manifest_not_the_one_release_file"
    digest = releases[0][len("releases/") : -len(".json")]
    if sha256(raw).hexdigest() != digest:
        return "manifest_digest_mismatch"
    return digest, raw


def _file_reasons(commit: RemoteCommit, listed: Mapping[str, str], extra: set[str]) -> list[str]:
    if set(commit.files) != set(listed) | extra:
        return ["changed_files_not_the_manifests"]
    if any(sha256(commit.files[p] or b"").hexdigest() != digest for p, digest in listed.items()):
        return ["file_bytes_not_the_manifests"]
    return []


def _manifest_reasons(
    commit: RemoteCommit, manifest: ReleaseManifestFile, digest: str, settings: PublicationSettings
) -> list[str]:
    reasons = []
    if (manifest.repository, manifest.publication.path) != (settings.repository, settings.path):
        reasons.append("manifest_not_the_configured_publication")
    if manifest.publication.base_commit != commit.parents[0]:
        reasons.append("manifest_base_not_the_parent")
    if commit.headline != headline_for(digest):
        reasons.append("message_not_the_manifests")
    listed = {item.path: item.sha256 for item in manifest.files}
    extra = {f"releases/{digest}.json", release_signature_path(digest)}
    return reasons + _file_reasons(commit, listed, extra)


def _published(commit: RemoteCommit) -> tuple[str, bytes, ReleaseManifestFile] | str:
    """The manifest's digest, bytes and record, each proved from the commit, or why not."""
    if len(commit.parents) != 1:
        return "commit_not_one_parent"
    found = _manifest_of(commit)
    if isinstance(found, str):
        return found
    digest, raw = found
    manifest = parsed_manifest(raw)
    return manifest if isinstance(manifest, str) else (digest, raw, manifest)


def derive_facts(
    commit: RemoteCommit, expected: Expected, at: datetime
) -> tuple[RemoteFacts | None, tuple[str, ...]]:
    """Every remote fact of ``commit``, from the commit and its manifest alone, or why not."""
    reasons = [f"commit_signature:{r}" for r in commit.signature_reasons]
    found = _published(commit)
    if isinstance(found, str):
        return None, (*reasons, found)
    digest, raw, manifest = found
    reasons += _manifest_reasons(commit, manifest, digest, expected.settings)
    signature = commit.files.get(release_signature_path(digest)) or b""
    if not signature:
        return None, (*reasons, "manifest_signature_not_published")
    trust = (expected.root, expected.settings.bot_principal, at)
    signer_reasons, fingerprint = _signed_by_bot(
        SignedBytes(raw, signature), RELEASE_NAMESPACE, trust
    )
    reasons += [f"manifest_signature:{r}" for r in signer_reasons]
    if reasons or fingerprint is None:
        return None, tuple(reasons)
    return _facts(commit, manifest, (signature, fingerprint), expected.settings), ()


def _facts(
    commit: RemoteCommit,
    manifest: ReleaseManifestFile,
    signer: tuple[bytes, str],
    settings: PublicationSettings,
) -> RemoteFacts:
    signature, fingerprint = signer
    digest = _digest(commit)
    return RemoteFacts(
        path=settings.path,
        signature_class="github_app" if settings.path == "A" else "bot_key",
        app_id=settings.app.app_id if settings.app is not None else None,
        commit=commit.oid,
        parent=commit.parents[0],
        release_manifest_path=f"releases/{digest}.json",
        release_manifest_sha256=digest,
        release_manifest_signature_path=release_signature_path(digest),
        release_manifest_signature_sha256=sha256(signature).hexdigest(),
        manifest_signer_fingerprint=fingerprint,
        files=_attested_files(commit),
        policy=manifest.policy,
        plan_id=manifest.plan_id,
        batch_id=manifest.batch_id,
        batch_sequence=manifest.batch_sequence,
    )


def _attested_files(commit: RemoteCommit) -> tuple[AttestedFile, ...]:
    return tuple(
        AttestedFile(path=path, sha256=sha256(data or b"").hexdigest())
        for path, data in sorted(commit.files.items())
    )


def _digest(commit: RemoteCommit) -> str:
    (path,) = [p for p in commit.files if RELEASE_FILE.fullmatch(p)]
    return path[len("releases/") : -len(".json")]


def read_attestation(
    stored: Stored,
    facts: RemoteFacts,
    settings: PublicationSettings,
    trust: tuple[TrustRoot, datetime],
) -> tuple[PublicationAttestation | None, tuple[str, ...]]:
    """The stored attestation of ``facts.commit``, verified, or why it is not one."""
    reasons = list(stored.storage_reasons)
    if not stored.attestation or not stored.signature:
        return None, (*reasons, "attestation_incomplete")
    try:
        found = PublicationAttestation.model_validate_json(stored.attestation)
    except ValidationError:
        return None, (*reasons, "attestation_unreadable")
    if canonical_text(found).encode() != stored.attestation:
        reasons.append("attestation_not_canonical")
    root, at = trust
    signed = SignedBytes(stored.attestation, stored.signature)
    signer, _ = _signed_by_bot(signed, PUBLICATION_NAMESPACE, (root, settings.bot_principal, at))
    reasons += [f"attestation_signature:{r}" for r in signer]
    if found.remote.commit != facts.commit:
        reasons.append("attestation_not_this_commit")
    elif found.remote != facts:
        reasons.append("attestation_facts_mismatch")
    return (None, tuple(reasons)) if reasons else (found, ())


def verify_publication(
    source: PublicSource, expected: Expected, commit: str, at: datetime
) -> PublicationProof:
    """The publication of ``commit``, verified from the remote and the trust root alone.

    ``at`` is the time keys are judged at (their validity window and
    revocations; the historical-validity question is #1040).
    """
    found = _on_main(source.main, commit)
    if found is None:
        return PublicationProof("not_found", commit, None, None, ("commit_not_on_main",))
    facts, reasons = derive_facts(found, expected, at)
    if facts is None:
        return PublicationProof("invalid", commit, None, None, reasons)
    stored = source.attestations.read(commit, at)
    if stored is None:
        return PublicationProof("unattested", commit, facts, None, ("attestation_missing",))
    trust = (expected.root, at)
    attestation, problems = read_attestation(stored, facts, expected.settings, trust)
    if attestation is None:
        return PublicationProof("invalid", commit, facts, None, problems)
    return PublicationProof("attested", commit, facts, attestation, ())


# --- attesting --------------------------------------------------------------------------------


def _receipt(request: ExecutionRequest) -> tuple[RecoveryReceipt, bytes]:
    tid = request.inputs.transaction.transaction_id
    held = [
        (r, raw)
        for r, raw in publication_receipts.chain(request.workspace)
        if r.transaction_id == tid
    ]
    published = [(r, raw) for r, raw in held if r.published]
    if len(published) != 1:
        raise _not_eligible("attestation_not_attestable:no_published_receipt")
    receipt, raw = published[0]
    if receipt.classification != "published_authorized":
        raise _not_eligible(f"attestation_not_attestable:{receipt.classification}")
    return receipt, raw


def attestation_of(
    receipt: RecoveryReceipt, raw: bytes, facts: RemoteFacts
) -> PublicationAttestation:
    """The attestation of a ``published_authorized`` receipt and the facts the remote shows."""
    claims = RunnerClaims(
        transaction_id=receipt.transaction_id,
        attempt_id=receipt.attempt_id,
        attempt_at=receipt.attempt_at,
        remote_event_at=receipt.remote_event_at,
        authorization="authorized",
        authorization_waived=receipt.authorization_waived,
        receipt_version=receipt.receipt_version,
        receipt_sequence=receipt.sequence,
        receipt_sha256=sha256(raw).hexdigest(),
        recovered_from_remote=receipt.recovered_from_remote,
    )
    return PublicationAttestation(remote=facts, claims=claims)


def _facts_reasons(receipt: RecoveryReceipt, facts: RemoteFacts) -> list[str]:
    pairs = (
        ("commit", facts.commit, receipt.commit),
        ("parent", facts.parent, receipt.parent),
        ("manifest", facts.release_manifest_sha256, receipt.release_manifest_sha256),
        (
            "manifest_signature",
            facts.release_manifest_signature_sha256,
            receipt.manifest_signature_sha256,
        ),
        ("bot_fingerprint", facts.manifest_signer_fingerprint, receipt.bot_fingerprint),
        ("path", facts.path, receipt.path),
    )
    return [f"receipt_contradicted:{name}" for name, have, want in pairs if have != want]


def _conflict(
    request: ExecutionRequest, reasons: tuple[str, ...], at: datetime
) -> ForarbeiderRefusedError:
    tid = request.inputs.transaction.transaction_id
    publication_attempts.latch(request.workspace, reasons, at, tid)
    publication_anchors.settle(request, "stop_latch")
    return _not_eligible(f"attestation_conflict:{','.join(reasons)}")


def _signed(
    attestation: PublicationAttestation, signer: Signer, request: ExecutionRequest, at: datetime
) -> dict[str, bytes]:
    payload = canonical_text(attestation).encode()
    signature = signer.sign(payload, PUBLICATION_NAMESPACE)
    trust = (request.root, request.inputs.settings.bot_principal, at)
    reasons, _ = _signed_by_bot(SignedBytes(payload, signature), PUBLICATION_NAMESPACE, trust)
    if reasons:
        raise _not_eligible(f"attestation_signature:{','.join(reasons)}")
    return {ATTESTATION_FILE: payload, ATTESTATION_SIGNATURE_FILE: signature}


def _settled(proof: PublicationProof, wanted: PublicationAttestation) -> tuple[str, ...] | None:
    """``()`` when the tag holds exactly ``wanted``; ``None`` when there is none; else why not."""
    if proof.status == "unattested":
        return None
    if proof.status == "attested" and proof.attestation == wanted:
        return ()
    return proof.reasons or ("attestation_not_this_receipts",)


def attest(request: ExecutionRequest, environment: Simulation, signer: Signer) -> AttestOutcome:
    """Publish the bot's attestation of a receipted, re-verified publication; create-only.

    Raises:
        ForarbeiderRefusedError: ``release_not_eligible`` with
            ``attestation_not_attestable:<why>`` (no published receipt, or one not
            ``published_authorized``), ``attestation_facts:<why>`` (the remote does
            not show a verified publication now), ``attestation_signature:<why>``,
            or ``attestation_conflict:<why>`` (latch set: the remote contradicts the
            receipt, or the tag holds another attestation); ``corpus_locked: workspace``.
    """
    with publication_attempts.workspace_lock(request.workspace):
        at = environment.clock()
        receipt, raw = _receipt(request)
        expected = Expected(request.root, request.inputs.settings)
        source = public_source(environment, expected, (request.clone, signer))
        proof = verify_publication(source, expected, receipt.commit, at)
        if proof.facts is None:
            raise _not_eligible(f"attestation_facts:{','.join(proof.reasons)}")
        contradicted = _facts_reasons(receipt, proof.facts)
        if contradicted:
            raise _conflict(request, tuple(contradicted), at)
        wanted = attestation_of(receipt, raw, proof.facts)
        return _created(request, (source, expected), (proof, wanted), (signer, at))


def _created(
    request: ExecutionRequest,
    remote: tuple[PublicSource, Expected],
    found: tuple[PublicationProof, PublicationAttestation],
    signing: tuple[Signer, datetime],
) -> AttestOutcome:
    """The tag holding ``wanted``: found, or created once and read back."""
    source, expected = remote
    proof, wanted = found
    signer, at = signing
    held = _settled(proof, wanted)
    if held is None:
        files = _signed(wanted, signer, request, at)
        written = source.attestations.write(wanted.remote.commit, files, at)
        held = _settled(verify_publication(source, expected, wanted.remote.commit, at), wanted)
        if held is None:
            return AttestOutcome(
                "attestation_unknown", wanted.remote.commit, wanted, written.reasons
            )
        status: AttestStatus = "already_attested" if written.status == "exists" else "attested"
    else:
        status = "already_attested"
    if held:
        raise _conflict(request, held, at)
    return AttestOutcome(status, wanted.remote.commit, wanted, ())
