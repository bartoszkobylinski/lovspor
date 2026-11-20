"""AR4b-1 path B: a bot-signed commit and a fast-forward push to a local bare remote (#738).

**B6, commit.** The proposed files of the AR4a clone go into a *separate* index
(``.git/lovspor-publication-index``, read from the base first), so the clone's
own index and the AR4a read-back stay as they were. Git writes the tree; the
commit object is composed here (tree, parent = the base, the bot as author and
committer at the check time, a message naming the manifest digest), signed by
the caller's **signer** in the namespace ``lovspor-forarbeider-release``, and
the signature is put in the ``gpgsig`` header, where Git keeps commit
signatures. Git stores the object; ``HEAD`` and every ref stay where they were.

**B7, validate the local commit.** It is read back and checked: its parent is
the base, it changes exactly the proposal's paths, every blob is the bytes of
the proposal (by Git's own blob id), and its signature verifies against the
trust root as a key the root lists for the bot's principal, in the release
namespace, at the check time.

**B9, push.** ``git push --porcelain <bare> <commit>:refs/heads/main``, never
forced (:mod:`lovspor.git_publish` has no shape for it), to an absolute local
path only. Afterwards the remote ``main`` is read:

- at the commit: ``published``;
- the push was rejected and ``main`` is not the commit: ``refused``, nothing published;
- anything else: ``outcome_unknown``; recovery (AR4b-2) decides.

The signer is the caller's: in this repository only a test adapter over a
throwaway key exists. No production key is read anywhere.
"""

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Literal, NamedTuple, Protocol

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_trust import (
    RELEASE_NAMESPACE,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_models import PublicationTransaction
from lovspor.forarbeider.publication_path_a import body, headline
from lovspor.git_publish import GitAnswer, GitPublishError, run_git

INDEX = "lovspor-publication-index"
_GPGSIG = b"gpgsig "


class Signer(Protocol):
    """Signs bytes in a namespace as ``principal``; only a test adapter implements it here."""

    @property
    def principal(self) -> str: ...

    def sign(self, payload: bytes, namespace: str) -> bytes: ...


class Sent(NamedTuple):
    status: Literal["published", "refused", "outcome_unknown"]
    commit: str | None
    reasons: tuple[str, ...]


class CommitCheck(NamedTuple):
    """B7: whether the local commit is the proposal, from the base, signed by the bot."""

    commit: str
    verified: bool
    reasons: tuple[str, ...]
    signer_principal: str | None


def _refused(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.CHECKOUT_UNVERIFIABLE, detail)


def _git(clone: Path, argv: tuple[str, ...], stdin: bytes | None = None) -> bytes:
    try:
        answer = run_git(clone, argv, stdin, clone / ".git" / INDEX)
    except GitPublishError as error:
        raise _refused(argv[0]) from error
    if answer.code != 0:
        raise _refused(argv[0])
    return answer.out


def _answer(clone: Path, argv: tuple[str, ...]) -> GitAnswer:
    try:
        return run_git(clone, argv)
    except GitPublishError as error:
        raise _refused(argv[0]) from error


def blob_id(payload: bytes) -> str:
    """Git's id of a blob holding ``payload``."""
    return sha1(b"blob %d\0" % len(payload) + payload, usedforsecurity=False).hexdigest()


def _identity(signer: Signer, at: datetime) -> bytes:
    name = signer.principal
    return f"{name} <{name}@users.noreply.invalid> {int(at.timestamp())} +0000".encode()


def commit_payload(
    tree: str, transaction: PublicationTransaction, signer: Signer, at: datetime
) -> bytes:
    """The commit object without its signature: tree, parent = base, bot, message."""
    who = _identity(signer, at)
    message = f"{headline(transaction)}\n\n{body(transaction)}".encode()
    base = transaction.content.publication.base_commit
    return b"tree %s\nparent %s\nauthor %s\ncommitter %s\n\n%s" % (
        tree.encode(),
        base.encode(),
        who,
        who,
        message,
    )


def signed_object(payload: bytes, signature: bytes) -> bytes:
    """``payload`` with ``signature`` in the ``gpgsig`` header, as Git keeps it."""
    header, _, message = payload.partition(b"\n\n")
    lines = signature.rstrip(b"\n").split(b"\n")
    folded = _GPGSIG + b"\n ".join(lines)
    return header + b"\n" + folded + b"\n\n" + message


def split_signature(raw: bytes) -> tuple[bytes, bytes]:
    """The signed payload and the signature of a commit object; empty signature when none."""
    header, _, message = raw.partition(b"\n\n")
    kept: list[bytes] = []
    signature: list[bytes] = []
    in_signature = False
    for line in header.split(b"\n"):
        if line.startswith(_GPGSIG):
            in_signature = True
            signature.append(line[len(_GPGSIG) :])
        elif in_signature and line.startswith(b" "):
            signature.append(line[1:])
        else:
            in_signature = False
            kept.append(line)
    payload = b"\n".join(kept) + b"\n\n" + message
    return payload, (b"\n".join(signature) + b"\n") if signature else b""


def build_commit(
    clone: Path, transaction: PublicationTransaction, signer: Signer, at: datetime
) -> str:
    """B6: the signed commit of exactly the proposal, stored as an object; no ref moves.

    Raises:
        ForarbeiderRefusedError: ``checkout_unverifiable`` when Git refuses a step.
    """
    _git(clone, ("read-tree", transaction.content.publication.base_commit))
    for item in transaction.content.files:
        _git(clone, ("add", "--", item.path))
    tree = _git(clone, ("write-tree",)).decode("ascii").strip()
    payload = commit_payload(tree, transaction, signer, at)
    signed = signed_object(payload, signer.sign(payload, RELEASE_NAMESPACE))
    return (
        _git(clone, ("hash-object", "-t", "commit", "-w", "--stdin"), signed)
        .decode("ascii")
        .strip()
    )


def _parent(raw: bytes) -> str | None:
    parents = [
        line[7:].decode()
        for line in raw.split(b"\n\n")[0].split(b"\n")
        if line.startswith(b"parent ")
    ]
    return parents[0] if len(parents) == 1 else None


def _blobs(clone: Path, commit: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for record in _git(clone, ("ls-tree", "-r", "-z", commit)).split(b"\0"):
        if record:
            meta, _, path = record.partition(b"\t")
            found[path.decode()] = meta.split()[2].decode()
    return found


def _content_reasons(
    clone: Path, commit: str, transaction: PublicationTransaction, files: Mapping[str, bytes]
) -> list[str]:
    base = transaction.content.publication.base_commit
    raw = _git(clone, ("cat-file", "commit", commit))
    reasons = [] if _parent(raw) == base else ["commit_parent_not_base"]
    changed = _git(clone, ("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", base, commit))
    paths = sorted(p.decode() for p in changed.split(b"\0") if p)
    if paths != sorted(item.path for item in transaction.content.files):
        reasons.append("commit_paths_not_proposal")
    blobs = _blobs(clone, commit)
    if any(blobs.get(item.path) != blob_id(files[item.path]) for item in transaction.content.files):
        reasons.append("commit_bytes_not_proposal")
    return reasons


def verify_commit(
    clone: Path,
    commit: str,
    context: tuple[PublicationTransaction, Mapping[str, bytes]],
    trust: tuple[TrustRoot, str, datetime],
) -> CommitCheck:
    """B7: parent, paths, bytes and the bot's signature in the release namespace."""
    transaction, files = context
    root, principal, at = trust
    reasons = _content_reasons(clone, commit, transaction, files)
    payload, signature = split_signature(_git(clone, ("cat-file", "commit", commit)))
    check = check_signature(root, SignedBytes(payload, signature), RELEASE_NAMESPACE, at)
    signer = check.entry.principal if check.entry is not None else None
    if not check.verified:
        reasons.append(f"bot_signature:{check.reason}")
    elif signer != principal or check.entry is None or check.entry.role != "bot_execution":
        reasons.append("bot_signature:not_the_bot")
    return CommitCheck(commit, not reasons, tuple(reasons), signer)


def remote_head(bare: Path) -> str | None:
    """The commit the bare remote's ``main`` names, ``None`` when it names none."""
    answer = _answer(bare.parent, ("ls-remote", str(bare), "refs/heads/main"))
    if answer.code != 0:
        raise _refused("ls-remote")
    line = answer.out.decode("ascii", "replace").strip()
    return line.split()[0] if line else None


def push(clone: Path, bare: Path, commit: str) -> Sent:
    """B9: the fast-forward push, once, then what the remote's ``main`` proves."""
    try:
        answer = run_git(clone, ("push", "--porcelain", str(bare), f"{commit}:refs/heads/main"))
    except GitPublishError:
        return Sent("outcome_unknown", commit, ("push_could_not_run",))
    try:
        landed = remote_head(bare)
    except ForarbeiderRefusedError:
        return Sent("outcome_unknown", commit, ("remote_unreadable_after_push",))
    if landed == commit:
        return Sent("published", commit, ())
    rejected = answer.code != 0 and b"[rejected]" in answer.out + answer.err
    if rejected:
        return Sent("refused", commit, ("push_rejected_non_fast_forward",))
    return Sent("outcome_unknown", commit, ("remote_not_at_pushed_commit",))
