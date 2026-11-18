"""The publication seal: an SSH-signed tag binding a corpus commit to its attestation.

A git note is unsigned and can be rewritten by anyone who can push the notes
ref, and a signed commit alone would not authenticate the note. The seal is an
annotated tag ``refs/tags/sync/seal-<run>-<attempt>`` on the published corpus
commit whose message embeds the attestation note verbatim; the tag's SSH
signature (namespace ``git``) covers the object id, the tag header and that
message, so one signature binds the commit, its attestation and the publishing
key. ``docs/publication-seal.md`` describes the format and the trust chain.

This module composes the message (no key involved) and verifies a seal with
git's own ``verify-tag`` against a pinned allowed-signers file. Signing is the
sync workflow's separate step, the only one that ever holds the key. The note
ref is never trusted for a verdict: the signed, embedded copy is.

Every git call is a list-argument subprocess, never a shell string.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LovsporError
from lovspor.temporal_attestation import ATTESTATION_NOTES_REF, attestation_note_blob

SEAL_MAGIC = "lovspor-publication-seal v1"
SEAL_TAG_PREFIX = "refs/tags/sync/"
_SIGNATURE_MARKERS = (
    b"-----BEGIN PGP SIGNATURE-----",
    b"-----BEGIN PGP MESSAGE-----",
    b"-----BEGIN SIGNED MESSAGE-----",
    b"-----BEGIN SSH SIGNATURE-----",
)
"""The markers git itself recognises as the start of an in-object signature."""
_GOOD_SSH_SIGNATURE = re.compile(r'^Good "git" signature for (.+) with \S+ key ', re.MULTILINE)


class PublicationSealError(LovsporError):
    """The publication seal could not be read, composed or verified."""


class SealCompositionError(PublicationSealError):
    """HEAD cannot be sealed: no attestation note, or none naming HEAD."""


class SealVerificationError(PublicationSealError):
    """No seal proves the candidate commit; the consumer must hold."""


class AlreadySealed(BaseModel):
    """A ``sync/*`` tag already seals HEAD with the same attestation note."""

    model_config = ConfigDict(frozen=True)

    tag: str


class SealVerdict(BaseModel):
    """An accepted seal: the tag that proved the commit, and who signed it."""

    model_config = ConfigDict(frozen=True)

    tag: str
    signer: str


class _Seal(NamedTuple):
    fields: dict[str, str]
    attestation: bytes

    def binds(self, commit: str, blob: str) -> bool:
        fields = self.fields
        return fields.get("corpus_commit") == commit and fields.get("attestation_blob") == blob


def seal_message(corpus_commit: str, attestation_blob: str, note: bytes) -> bytes:
    """The exact seal message bytes: four header lines, a blank line, the note."""
    header = (
        f"{SEAL_MAGIC}\n"
        f"corpus_commit {corpus_commit}\n"
        f"attestation_ref {ATTESTATION_NOTES_REF}\n"
        f"attestation_blob {attestation_blob}\n\n"
    )
    return header.encode("ascii") + note


def compose_seal(repo: Path, allowed_signers: Path | None = None) -> bytes | AlreadySealed:
    """The seal message for HEAD, or :class:`AlreadySealed` when one exists.

    An existing ``sync/*`` tag on HEAD counts when its message binds HEAD
    and the same attestation blob; with ``allowed_signers`` it must also
    verify against that file, so a forged or revoked seal is sealed again
    instead of ending the run as a no-op. Raises
    :class:`SealCompositionError` when HEAD carries no attestation note, or
    a note with no entry whose ``corpus_commit`` is HEAD.
    """
    head = _resolve_commit(repo, "HEAD", SealCompositionError)
    blob = attestation_note_blob(repo, head)
    if blob is None:
        raise SealCompositionError(f"HEAD {head} has no note on {ATTESTATION_NOTES_REF}")
    note = _git_out(repo, ["cat-file", "blob", blob], SealCompositionError)
    if not names_commit(note, head):
        raise SealCompositionError(f"the attestation note on HEAD {head} has no entry naming it")
    existing = _existing_seal(repo, head, blob, allowed_signers)
    return AlreadySealed(tag=existing) if existing else seal_message(head, blob, note)


def _existing_seal(repo: Path, head: str, blob: str, signers: Path | None) -> str | None:
    for ref, oid in _annotated_sync_tags(repo, head):
        seal = _parse_seal(_tag_message(repo, oid))
        if seal and seal.binds(head, blob) and _verifies(repo, head, signers, ref):
            return ref
    return None


def _verifies(repo: Path, head: str, signers: Path | None, ref: str) -> bool:
    if signers is None:
        return True
    try:
        verify_seal(repo, head, signers, ref)
    except SealVerificationError:
        return False
    return True


def verify_seal(
    repo: Path, candidate: str, allowed_signers_path: Path, tag: str | None = None
) -> SealVerdict:
    """Accept ``candidate`` only if a ``sync/*`` seal tag proves it.

    With ``tag`` only that tag is judged; without it, every ``sync/*`` tag
    pointing at the candidate is tried and the first that passes wins.
    Raises :class:`SealVerificationError` naming why each tag failed.
    """
    signers = _require_signers(allowed_signers_path)
    commit = _resolve_commit(repo, candidate, SealVerificationError)
    refs = [_seal_ref(tag)] if tag else [ref for ref, _ in _sync_tags(repo, commit)]
    if not refs:
        raise SealVerificationError(f"no {SEAL_TAG_PREFIX}* tag points at {commit}")
    failures: list[str] = []
    for ref in refs:
        try:
            return _verify_tag(repo, ref, commit, signers)
        except SealVerificationError as exc:
            failures.append(str(exc))
    raise SealVerificationError("; ".join(failures))


def names_commit(attestation: bytes, commit: str) -> bool:
    """Consumer rule 4: the JSON is a list (or object) with an entry for ``commit``."""
    try:
        parsed = json.loads(attestation)
    except ValueError:
        return False
    entries = parsed if isinstance(parsed, list) else [parsed]
    return any(isinstance(e, dict) and e.get("corpus_commit") == commit for e in entries)


def _verify_tag(repo: Path, ref: str, commit: str, signers: Path) -> SealVerdict:
    oid = _git_out(repo, ["rev-parse", "--verify", "--quiet", ref], SealVerificationError)
    oid_text = oid.decode().strip()
    kind = _git_out(repo, ["cat-file", "-t", oid_text], SealVerificationError).strip()
    if kind != b"tag":
        raise SealVerificationError(f"{ref} is a lightweight tag, not a signed annotated tag")
    headers, message = _tag_object(repo, oid_text)
    if headers.get("object") != commit or headers.get("type") != "commit":
        raise SealVerificationError(f"{ref} does not tag commit {commit}")
    signer = _verify_signature(repo, oid_text, signers, ref)
    _check_message(message, commit, ref)
    return SealVerdict(tag=ref, signer=signer)


def _verify_signature(repo: Path, oid: str, signers: Path, ref: str) -> str:
    config = ["-c", "gpg.ssh.program=ssh-keygen", "-c", f"gpg.ssh.allowedSignersFile={signers}"]
    result = _git(repo, [*config, "verify-tag", oid])
    output = result.stderr.decode(errors="replace")
    match = _GOOD_SSH_SIGNATURE.search(output)
    if result.returncode != 0 or match is None:
        reason = " ".join(output.split()) or f"exit {result.returncode}"
        raise SealVerificationError(f"{ref}: signature not accepted by {signers}: {reason}")
    return match.group(1)


def _check_message(message: bytes, commit: str, ref: str) -> None:
    seal = _parse_seal(message)
    if seal is None:
        raise SealVerificationError(f"{ref}: message is not a {SEAL_MAGIC!r} seal")
    if seal.fields.get("corpus_commit") != commit:
        raise SealVerificationError(f"{ref}: corpus_commit does not name {commit}")
    if not names_commit(seal.attestation, commit):
        raise SealVerificationError(f"{ref}: embedded attestation has no entry for {commit}")


def _parse_seal(message: bytes) -> _Seal | None:
    header, separator, attestation = message.partition(b"\n\n")
    lines = header.decode("utf-8", errors="replace").split("\n")
    if not separator or lines[0] != SEAL_MAGIC:
        return None
    fields = dict(line.partition(" ")[::2] for line in lines[1:])
    return _Seal(fields, attestation)


def _tag_message(repo: Path, oid: str) -> bytes:
    return _tag_object(repo, oid)[1]


def _tag_object(repo: Path, oid: str) -> tuple[dict[str, str], bytes]:
    """The tag's headers and message, exactly the bytes its signature covers."""
    raw = _git_out(repo, ["cat-file", "tag", oid], PublicationSealError)
    header, _, message = raw[: _signature_start(raw)].partition(b"\n\n")
    lines = header.decode("utf-8", errors="replace").split("\n")
    return dict(line.partition(" ")[::2] for line in lines), message


def _signature_start(raw: bytes) -> int:
    """Where git cuts the payload: the LAST line opening a signature block."""
    start, offset = len(raw), 0
    for line in raw.split(b"\n"):
        if line.startswith(_SIGNATURE_MARKERS):
            start = offset
        offset += len(line) + 1
    return start


def _annotated_sync_tags(repo: Path, commit: str) -> list[tuple[str, str]]:
    return [(ref, oid) for ref, oid in _sync_tags(repo, commit) if _is_tag(repo, oid)]


def _is_tag(repo: Path, oid: str) -> bool:
    return _git_out(repo, ["cat-file", "-t", oid], PublicationSealError).strip() == b"tag"


def _sync_tags(repo: Path, commit: str) -> list[tuple[str, str]]:
    """``(refname, object id)`` of every ``sync/*`` tag pointing at ``commit``."""
    args = ["for-each-ref", f"--points-at={commit}", "--format=%(refname) %(objectname)"]
    listed = _git_out(repo, [*args, SEAL_TAG_PREFIX], PublicationSealError).decode()
    pairs = (line.split(" ", 1) for line in listed.splitlines() if line)
    return sorted((ref, oid) for ref, oid in pairs)


def _seal_ref(tag: str) -> str:
    ref = tag if tag.startswith("refs/") else f"refs/tags/{tag}"
    if not ref.startswith(SEAL_TAG_PREFIX):
        raise SealVerificationError(f"{tag} is not a {SEAL_TAG_PREFIX}* tag")
    return ref


def _require_signers(path: Path) -> Path:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SealVerificationError(f"allowed signers file {path} unreadable: {exc}") from exc
    keys = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if not keys:
        raise SealVerificationError(
            f"allowed signers file {path} holds no key: no seal can be accepted until "
            "the owner commits the publication-seal public key"
        )
    return path.resolve()


def _resolve_commit(repo: Path, rev: str, error: type[PublicationSealError]) -> str:
    args = ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"]
    result = _git(repo, args)
    if result.returncode != 0:
        raise error(f"{rev} is not a commit in {repo}")
    return result.stdout.decode().strip()


def _git_out(repo: Path, args: list[str], error: type[PublicationSealError]) -> bytes:
    result = _git(repo, args)
    if result.returncode != 0:
        stderr = " ".join(result.stderr.decode(errors="replace").split())
        raise error(f"git {' '.join(args)} (cwd={repo}) exited {result.returncode}: {stderr}")
    return result.stdout


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        # S603/S607: trusted git command, list args, no shell; bytes, because
        # the note is embedded verbatim and must not pass through a decoder.
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=repo,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise PublicationSealError(f"git could not run in {repo}: {exc}") from exc
