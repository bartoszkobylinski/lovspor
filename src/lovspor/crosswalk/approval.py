"""Owner authorization of the release-candidate gate, and proof of its output (#792).

`approved_by` in an approval file is text anyone can type; it authenticates
nobody. An approval counts only with a detached SSH signature over its exact
bytes, made in the namespace `lovspor-crosswalk-approval`
(`ssh-keygen -Y sign -f <key> -n lovspor-crosswalk-approval approval.json`, which
writes `approval.json.sig`), by a key listed in the trusted signers file shipped
with this engine (`approval-signers`, OpenSSH allowed_signers format). That file
names no key until the owner adds their own public key in a reviewed change; until
then every approval is refused and no release candidate can be made or described.
This tool never creates, chooses or adds a key.

A release_candidate file is proven, never trusted: it gets a manifest only when
`promote(approved input, signed approval, capture)` reproduces it byte for byte.
A hand-edited `release_state`, an altered history or another approval's output
therefore gets none.
"""

import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from lovspor.crosswalk.cache import MANIFEST
from lovspor.crosswalk.canonical import parse_records
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.release import Approval, promote

NAMESPACE = "lovspor-crosswalk-approval"
TRUSTED_SIGNERS = Path(__file__).with_name("approval-signers")
SIGNATURE_SUFFIX = ".sig"
APPROVAL_FILE = "approval.json"
APPROVED_INPUT_FILE = "approved-input.jsonl"
NO_SIGNER = "no trusted approval signer is configured: release candidates stay blocked"
_SSH_KEYGEN_TIMEOUT = 30
MAX_SIGNATURE_BYTES = 64 * 1024
CARRIED = (
    "the approved input carries release_candidate revisions: this approval cannot "
    "prove an authorization it never gave (proposed A1.5)"
)


def signature_path(approval: Path) -> Path:
    return approval.with_name(approval.name + SIGNATURE_SUFFIX)


def _signer_lines(signers: Path) -> list[str]:
    try:
        text = signers.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CrosswalkError(f"trusted signers unreadable: {signers}") from exc
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _ssh_keygen(args: list[str], stdin: bytes) -> str:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["ssh-keygen", "-Y", *args],  # noqa: S607
            input=stdin,
            capture_output=True,
            timeout=_SSH_KEYGEN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CrosswalkError("approval signature could not be checked: ssh-keygen") from exc
    if done.returncode != 0:
        raise CrosswalkError("approval signature refused: not by a trusted signer")
    return done.stdout.decode(errors="replace")


@dataclass(frozen=True)
class Signer:
    principal: str
    signers_sha256: str


def verify_signature(message: bytes, signature: Path, signers: Path = TRUSTED_SIGNERS) -> Signer:
    """The one trusted principal whose key signed exactly these bytes, or a refusal."""
    if not _signer_lines(signers):
        raise CrosswalkError(NO_SIGNER)
    found = _ssh_keygen(["find-principals", "-s", str(signature), "-f", str(signers)], b"")
    principals = found.split()
    if len(principals) != 1:
        raise CrosswalkError("approval signature refused: not exactly one trusted signer")
    verify = ["verify", "-f", str(signers), "-I", principals[0], "-n", NAMESPACE]
    _ssh_keygen([*verify, "-s", str(signature)], message)
    return Signer(principals[0], sha256(signers.read_bytes()).hexdigest())


@dataclass(frozen=True)
class SignedApproval:
    approval: Approval
    approval_sha256: str
    signature_sha256: str
    signer: Signer


def _open_regular(path: Path, what: str) -> int:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise CrosswalkError(f"{what} is not a regular file: {path}")
    return descriptor


def read_regular(path: Path, limit: int, what: str) -> bytes:
    """A regular file of at most ``limit`` bytes; anything else is refused unread.

    O_NONBLOCK keeps the open of a FIFO from waiting for a writer, and the type is
    judged by fstat on the opened descriptor, so a symlink to a FIFO or a device is
    refused as well and nothing can swap the file between check and read.
    """
    try:
        with os.fdopen(_open_regular(path, what), "rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise CrosswalkError(f"{what} unreadable: {path}") from exc
    if len(data) > limit:
        raise CrosswalkError(f"{what} too large: {path}")
    return data


def _read_signature(signature: Path) -> bytes:
    return read_regular(signature, MAX_SIGNATURE_BYTES, "approval signature")


def signed_approval(
    raw: bytes, approval: Approval, signature: Path, signers: Path = TRUSTED_SIGNERS
) -> SignedApproval:
    """``approval`` parsed from ``raw``, whose exact bytes a trusted signer signed.

    ssh-keygen verifies a private copy of the bytes read here, so the digest the
    manifest records is of exactly the signature that was verified.
    """
    signed = _read_signature(signature)
    with tempfile.TemporaryDirectory(prefix="lovspor-approval-") as scratch:
        copy = Path(scratch) / signature.name
        copy.write_bytes(signed)
        signer = verify_signature(raw, copy, signers)
    return SignedApproval(approval, sha256(raw).hexdigest(), sha256(signed).hexdigest(), signer)


@dataclass(frozen=True)
class CandidateProof:
    """What a release_candidate manifest states about the approval that produced the file."""

    candidate_sha256: str
    capture_manifest_sha256: str
    block: dict[str, str]


def prove_candidates(
    data: bytes, approved_input: bytes, signed: SignedApproval, cache: Path
) -> CandidateProof:
    """Replay the gate: the file must be exactly what this approval makes of the approved input.

    The approved input must be research_only throughout. A candidate revision it
    carries would pass through the gate unchanged and so look proven by this
    approval, which never authorized it.
    """
    if any(r["release_state"] != "research_only" for r in parse_records(approved_input)):
        raise CrosswalkError(CARRIED)
    replayed, _ = promote(approved_input, signed.approval, cache)
    if replayed != data:
        raise CrosswalkError(
            "release candidates are not the gate's output for this approval and input"
        )
    block = {
        "approved_input_sha256": signed.approval.input_sha256,
        "approval_sha256": signed.approval_sha256,
        "signature_sha256": signed.signature_sha256,
        "signature_namespace": NAMESPACE,
        "signer_principal": signed.signer.principal,
        "trusted_signers_sha256": signed.signer.signers_sha256,
    }
    capture = sha256((cache / MANIFEST).read_bytes()).hexdigest()
    return CandidateProof(sha256(data).hexdigest(), capture, block)
