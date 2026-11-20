"""FC7b: the trust root for forarbeider policy signatures (#738; D6, D11; §3.1, §9.1).

A signature here proves one thing only: a private key whose public key the
trust root lists, for the signature's namespace and at the stated time, signed
exactly these bytes. It proves nothing about who held the key or what they
understood, and it is never a legal clearance (D10).

The trust root is two files in OpenSSH formats, the same mechanism the
crosswalk approval uses (``ssh-keygen -Y``, #792, #840):

- ``policy-signers`` (allowed_signers): every entry names one role by its
  namespaces, either the owner's key (any of ``lovspor-forarbeider-policy``,
  ``lovspor-forarbeider-limits``, ``lovspor-forarbeider-control``,
  ``lovspor-forarbeider-inventory`` and ``lovspor-forarbeider-quarantine``;
  AR1b added the limits and control namespaces for signed limits and
  stop/resume records, AR2 the inventory
  namespace for the owner-signed document inventory, AR3 the quarantine
  namespace for owner-signed quarantine exceptions) or the bot's
  execution key (``lovspor-forarbeider-release`` and/or
  ``lovspor-forarbeider-publication``), and carries a UTC validity window. A
  key holding both roles, a wildcard principal, an entry without a window, any
  other option or a key listed twice refuses the whole root;
- ``revoked-keys``: public keys no verification accepts.

The engine ships both files under ``trust/``, empty of keys: D6 and D11 are
proposed, not accepted. Only a root whose bytes equal the engine-pinned digest
is ``engine_pinned``; any other directory, such as a test's, is ``unpinned``,
and the authorization layer treats an unpinned root as a blocker whatever its
signatures say. Nothing a caller passes (a key, a principal, a JSON flag) is
an anchor.

Cryptography is OpenSSH's. This module reads the SSHSIG envelope only to name
the key and namespace a signature claims, so a refusal can say why; the
signature itself is checked by ``ssh-keygen -Y verify`` against the root, with
the revocation list and the stated time.
"""

import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, NamedTuple

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.ssh_signature import SshVerification, ssh_verify

POLICY_NAMESPACE = "lovspor-forarbeider-policy"
RELEASE_NAMESPACE = "lovspor-forarbeider-release"
PUBLICATION_NAMESPACE = "lovspor-forarbeider-publication"
LIMITS_NAMESPACE = "lovspor-forarbeider-limits"
CONTROL_NAMESPACE = "lovspor-forarbeider-control"
INVENTORY_NAMESPACE = "lovspor-forarbeider-inventory"
QUARANTINE_NAMESPACE = "lovspor-forarbeider-quarantine"
OWNER_NAMESPACES = frozenset(
    {
        POLICY_NAMESPACE,
        LIMITS_NAMESPACE,
        CONTROL_NAMESPACE,
        INVENTORY_NAMESPACE,
        QUARANTINE_NAMESPACE,
    }
)
BOT_NAMESPACES = frozenset({RELEASE_NAMESPACE, PUBLICATION_NAMESPACE})
TRUST_DIRECTORY = Path(__file__).with_name("trust")
SIGNERS_FILE = "policy-signers"
REVOKED_FILE = "revoked-keys"
#: The digest of the shipped trust root (no keys). Changing the shipped files
#: changes it; only an owner-merged change may update it, with its test.
ENGINE_TRUST_ROOT_SHA256 = "4c81a13a95cbda76081a8267a2204c6fb88c0e532d5d003272a289be3ab38c1d"
MAX_TRUST_FILE_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
_KEY_TYPE = "ssh-ed25519"
_ED25519_KEY_BYTES = 32
_ARMOR_MIN_LINES = 3
_PRINCIPAL = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,63}$")
_OPTIONS = re.compile(r'^([a-z-]+)="([^"]*)"(?:,|$)')
_INSTANT = re.compile(r"^\d{14}Z$")
_GOOD = re.compile(r'^Good "([^"]+)" signature for (\S+) with ED25519 key (SHA256:\S+)$')
_ARMOR = ("-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----")

Role = Literal["owner_policy", "bot_execution"]
Origin = Literal["engine_pinned", "unpinned"]


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, f"trust root: {detail}")


def fingerprint(blob: bytes) -> str:
    """OpenSSH's SHA256 fingerprint of a public key blob."""
    return "SHA256:" + base64.b64encode(sha256(blob).digest()).decode().rstrip("=")


@dataclass(frozen=True)
class SignerEntry:
    """One trusted key: its principal, its one role, its namespaces and window."""

    principal: str
    role: Role
    namespaces: frozenset[str]
    valid_after: datetime
    valid_before: datetime
    key_blob: bytes

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key_blob)


@dataclass(frozen=True)
class TrustRoot:
    """The parsed trust root and the exact bytes it was parsed from."""

    signers: bytes
    revoked: bytes
    entries: tuple[SignerEntry, ...]
    revoked_blobs: frozenset[bytes]

    @property
    def sha256(self) -> str:
        parts = (b"lovspor-forarbeider-trust-root/1", self.signers, self.revoked)
        return sha256(b"\n".join(sha256(part).digest() for part in parts)).hexdigest()

    @property
    def origin(self) -> Origin:
        return "engine_pinned" if self.sha256 == ENGINE_TRUST_ROOT_SHA256 else "unpinned"

    def entry_for(self, blob: bytes) -> SignerEntry | None:
        return next((entry for entry in self.entries if entry.key_blob == blob), None)


def load_trust_root(directory: Path = TRUST_DIRECTORY) -> TrustRoot:
    """The trust root in ``directory``; the engine's own by default.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid``: a file missing, not a
            regular file, too large, or not in the strict format above.
    """
    signers = _read(directory / SIGNERS_FILE)
    revoked = _read(directory / REVOKED_FILE)
    return parse_trust_root(signers, revoked)


def parse_trust_root(signers: bytes, revoked: bytes) -> TrustRoot:
    entries = tuple(_entry(line) for line in _lines(signers))
    blobs = [entry.key_blob for entry in entries]
    if len(set(blobs)) != len(blobs):
        raise _refused("a key is listed twice")
    if len({entry.principal for entry in entries}) != len(entries):
        raise _refused("a principal is listed twice")
    revoked_blobs = frozenset(_public_key(line.split()) for line in _lines(revoked))
    return TrustRoot(signers, revoked, entries, revoked_blobs)


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise _refused(f"{path.name} is not a regular file")
    data = path.read_bytes()
    if len(data) > MAX_TRUST_FILE_BYTES:
        raise _refused(f"{path.name} is too large")
    return data


def _lines(data: bytes) -> list[str]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise _refused("not ASCII") from error
    stripped = (line.strip() for line in text.splitlines())
    return [line for line in stripped if line and not line.startswith("#")]


def _entry(line: str) -> SignerEntry:
    fields = line.split()
    if len(fields) not in (4, 5) or not _PRINCIPAL.match(fields[0]):
        raise _refused("an entry is not <principal> <options> ssh-ed25519 <key>")
    options = _options(fields[1])
    namespaces = frozenset(options["namespaces"].split(","))
    after, before = _instant(options["valid-after"]), _instant(options["valid-before"])
    if after >= before:
        raise _refused("an entry's validity window is empty")
    role = _role(namespaces)
    return SignerEntry(fields[0], role, namespaces, after, before, _public_key(fields[2:]))


def _options(token: str) -> dict[str, str]:
    found: dict[str, str] = {}
    rest = token
    while rest:
        match = _OPTIONS.match(rest)
        if match is None or match.group(1) in found:
            raise _refused("malformed options")
        found[match.group(1)] = match.group(2)
        rest = rest[match.end() :]
    if set(found) != {"namespaces", "valid-after", "valid-before"}:
        raise _refused("an entry needs exactly namespaces, valid-after and valid-before")
    return found


def _instant(value: str) -> datetime:
    if not _INSTANT.match(value):
        raise _refused("a validity time is not YYYYMMDDHHMMSSZ")
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise _refused("a validity time is not a calendar time") from error


def _role(namespaces: frozenset[str]) -> Role:
    if namespaces and namespaces <= OWNER_NAMESPACES:
        return "owner_policy"
    if namespaces and namespaces <= BOT_NAMESPACES:
        return "bot_execution"
    raise _refused("an entry must hold exactly one role: owner policy or bot execution")


def _public_key(fields: list[str]) -> bytes:
    if len(fields) not in (2, 3) or fields[0] != _KEY_TYPE:
        raise _refused("only ssh-ed25519 public keys are accepted")
    try:
        blob = base64.b64decode(fields[1], validate=True)
    except ValueError as error:
        raise _refused("a public key is not base64") from error
    parts = _strings(blob, 2)
    if parts is None or parts[0] != _KEY_TYPE.encode() or len(parts[1]) != _ED25519_KEY_BYTES:
        raise _refused("a public key blob is malformed")
    return blob


def _strings(blob: bytes, count: int) -> list[bytes] | None:
    """``count`` SSH wire strings filling ``blob`` exactly, or None."""
    found, at = [], 0
    for _ in range(count):
        if at + 4 > len(blob):
            return None
        size = int.from_bytes(blob[at : at + 4], "big")
        found.append(blob[at + 4 : at + 4 + size])
        at += 4 + size
    return found if at == len(blob) else None


class Envelope(NamedTuple):
    """What an SSHSIG signature claims: its key and namespace. Not a verification."""

    key_blob: bytes
    namespace: str


def envelope(signature: bytes) -> Envelope | None:
    """The key and namespace an armored SSHSIG signature names, or None if malformed."""
    blob = _dearmored(signature)
    if blob is None or not blob.startswith(b"SSHSIG\x00\x00\x00\x01"):
        return None
    fields = _strings(blob[10:], 5)
    if fields is None:
        return None
    try:
        return Envelope(fields[0], fields[1].decode("ascii"))
    except UnicodeDecodeError:
        return None


def _dearmored(signature: bytes) -> bytes | None:
    try:
        lines = signature.decode("ascii").strip().splitlines()
    except UnicodeDecodeError:
        return None
    if len(lines) < _ARMOR_MIN_LINES or (lines[0], lines[-1]) != _ARMOR:
        return None
    try:
        return base64.b64decode("".join(lines[1:-1]), validate=True)
    except ValueError:
        return None


class SignedBytes(NamedTuple):
    """Exact record bytes and the detached SSH signature over them."""

    message: bytes
    signature: bytes


@dataclass(frozen=True)
class SignatureCheck:
    """The outcome of one verification, with the key it names when it names one."""

    verified: bool
    reason: str
    namespace: str | None = None
    entry: SignerEntry | None = None


class _Claim(NamedTuple):
    entry: SignerEntry
    namespace: str
    at: datetime


def check_signature(
    root: TrustRoot, signed: SignedBytes, namespace: str, at: datetime
) -> SignatureCheck:
    """Whether a key the root lists for ``namespace`` at ``at`` signed these bytes.

    Each refusal names its reason; the first that applies decides.
    """
    claimed = envelope(signed.signature) if len(signed.signature) <= MAX_SIGNATURE_BYTES else None
    if claimed is None:
        return SignatureCheck(False, "signature_malformed")
    if claimed.namespace != namespace:
        return SignatureCheck(False, "signature_namespace_mismatch", claimed.namespace)
    entry = root.entry_for(claimed.key_blob)
    refusal = _key_refusal(root, entry, namespace, at)
    if refusal is not None or entry is None:
        return SignatureCheck(False, refusal or "signer_not_in_trust_root", namespace, entry)
    reason = _ssh_verdict(root, signed, _Claim(entry, namespace, at))
    return SignatureCheck(reason == "signature_verified", reason, namespace, entry)


def _key_refusal(
    root: TrustRoot, entry: SignerEntry | None, namespace: str, at: datetime
) -> str | None:
    if not root.entries:
        return "trust_root_has_no_signer"
    if entry is None:
        return "signer_not_in_trust_root"
    if namespace not in entry.namespaces:
        return "signer_role_not_permitted"
    if entry.key_blob in root.revoked_blobs:
        return "signer_key_revoked"
    if not entry.valid_after <= at < entry.valid_before:
        return "signer_key_outside_validity"
    return None


def _ssh_verdict(root: TrustRoot, signed: SignedBytes, claim: _Claim) -> str:
    """``ssh-keygen -Y verify`` against the root, the revocation list and the time."""
    check = SshVerification(
        root.signers, root.revoked, claim.entry.principal, claim.namespace, claim.at
    )
    output = ssh_verify(check, signed.message, signed.signature)
    if output is None:
        return "signature_unverifiable"
    good = _GOOD.match(output.strip())
    expected = (claim.namespace, claim.entry.principal, claim.entry.fingerprint)
    return "signature_verified" if good and good.groups() == expected else "signature_invalid"
