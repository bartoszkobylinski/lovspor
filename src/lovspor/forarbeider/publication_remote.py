"""AR4b-2: read the remote history, read-only, for recovery (#738; §9.3a step 1).

What happened is read from the remote ``main`` itself, never from the local
attempt records, an API answer or the local clone:

- **path A**: the in-memory
  :class:`~lovspor.forarbeider.publication_sandbox.SimulatedGitHub`, as GitHub's
  commit and activity APIs would report it: the parent, the message, the files
  the commit changed, whether GitHub verified the signature and for which App,
  and the time of the ref update;
- **path B**: the
  :class:`~lovspor.forarbeider.publication_sandbox.DisposableRemote` bare
  repository, by its own objects and reflog: the parent, the message, the
  changed files read as blobs, the commit's SSH signature checked against the
  trust root at the commit's stated time, and the time of the ref update.

Both adapters are proved sandboxed before anything is read. Nothing here
writes, sends or pushes.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from lovspor.forarbeider import publication_path_b, publication_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import (
    RELEASE_NAMESPACE,
    SignedBytes,
    TrustRoot,
    check_signature,
)
from lovspor.forarbeider.publication_execution_models import PublicationSettings
from lovspor.forarbeider.publication_sandbox import SimulatedGitHub

#: The longest stretch of history recovery reads before calling it unverifiable.
HISTORY_LIMIT = 32


class RemoteCommit(NamedTuple):
    """One commit as the remote reports it; ``files`` maps each changed path to its bytes."""

    oid: str
    parents: tuple[str, ...]
    headline: str
    body: str
    files: dict[str, bytes | None]
    signature_reasons: tuple[str, ...]
    event_at: datetime | None


class Reader(NamedTuple):
    head: Callable[[], str | None]
    commit: Callable[[str], RemoteCommit | None]


class History(NamedTuple):
    """The commits after ``anchor``, oldest first, or why the history is unverifiable."""

    commits: tuple[RemoteCommit, ...]
    broken: str | None


def github_reader(transport: object, settings: PublicationSettings) -> Reader:
    """Path A, through the in-memory endpoint only.

    Raises:
        ForarbeiderRefusedError: ``simulation_transport_not_sandboxed``.
    """
    github = publication_sandbox.verified_transport(transport)
    return Reader(
        lambda: github.head(settings.repository, settings.branch),
        lambda oid: _github_commit(github, settings, oid),
    )


def _github_commit(
    github: SimulatedGitHub, settings: PublicationSettings, oid: str
) -> RemoteCommit | None:
    found = github.commit(oid)
    if found is None:
        return None
    reasons = []
    if found.app is None:
        reasons.append("commit_not_made_by_an_app")
    elif found.app != settings.app:
        reasons.append("commit_not_by_the_pinned_app")
    if not found.verified:
        reasons.append("github_signature_not_verified")
    files: dict[str, bytes | None] = dict(found.changes)
    return RemoteCommit(
        oid, (found.parent,), found.headline, found.body, files, tuple(reasons), found.at
    )


def bare_reader(remote: object, root: TrustRoot, settings: PublicationSettings) -> Reader:
    """Path B, from the disposable bare remote's own objects, never the clone.

    Raises:
        ForarbeiderRefusedError: ``simulation_remote_not_disposable``.
    """
    bare = publication_sandbox.verified_remote(remote)
    return Reader(
        lambda: publication_path_b.remote_head(bare),
        lambda oid: _bare_commit(bare, root, settings, oid),
    )


def _headers(raw: bytes) -> tuple[list[str], datetime | None, str]:
    header, _, message = raw.partition(b"\n\n")
    parents: list[str] = []
    stated: datetime | None = None
    for line in header.split(b"\n"):
        if line.startswith(b"parent "):
            parents.append(line[7:].decode("ascii", "replace"))
        elif line.startswith(b"committer "):
            stated = _stated_time(line)
    return parents, stated, message.decode("utf-8", "replace")


def _stated_time(line: bytes) -> datetime | None:
    parts = line.split()
    try:
        return datetime.fromtimestamp(int(parts[-2]), UTC)
    except (IndexError, ValueError):
        return None


def _changed(bare: Path, parents: list[str], oid: str) -> dict[str, bytes | None]:
    after = publication_path_b.remote_tree(bare, oid)
    before = publication_path_b.remote_tree(bare, parents[0]) if len(parents) == 1 else {}
    changed: dict[str, bytes | None] = {}
    for path in sorted(set(before) | set(after)):
        if before.get(path) == after.get(path):
            continue
        blob = after.get(path)
        changed[path] = (
            None if blob is None else publication_path_b.remote_object(bare, "blob", blob)
        )
    return changed


def _signature_reasons(
    raw: bytes, root: TrustRoot, principal: str, stated: datetime | None
) -> tuple[str, ...]:
    """The bot-key class: a key the root lists for the bot, valid at the stated time.

    A key revoked since is judged as if not revoked here: the revocation carries
    no time, so whether it came before the commit is an authorization question.
    """
    if stated is None:
        return ("commit_time_unreadable",)
    payload, signature = publication_path_b.split_signature(raw)
    unrevoked = TrustRoot(root.signers, root.revoked, root.entries, frozenset())
    check = check_signature(unrevoked, SignedBytes(payload, signature), RELEASE_NAMESPACE, stated)
    if not check.verified:
        return (f"bot_signature:{check.reason}",)
    entry = check.entry
    if entry is None or entry.principal != principal or entry.role != "bot_execution":
        return ("bot_signature:not_the_bot",)
    return ()


def _bare_commit(
    bare: Path, root: TrustRoot, settings: PublicationSettings, oid: str
) -> RemoteCommit | None:
    try:
        raw = publication_path_b.remote_object(bare, "commit", oid)
        parents, stated, message = _headers(raw)
        files = _changed(bare, parents, oid)
    except ForarbeiderRefusedError:
        return None
    headline, _, body = message.partition("\n\n")
    reasons = _signature_reasons(raw, root, settings.bot_principal, stated)
    event = publication_path_b.ref_event(bare, parents[0], oid) if len(parents) == 1 else None
    at = event.at if event is not None else None
    return RemoteCommit(oid, tuple(parents), headline, body, files, reasons, at)


def history(reader: Reader, anchor: str) -> History:
    """The commits after ``anchor`` up to the remote ``main``, oldest first.

    ``broken`` names why the history cannot be trusted: ``main`` unreadable, a
    commit unreadable, a merge, a root or more than :data:`HISTORY_LIMIT`
    commits before ``anchor`` is reached, so ``main`` does not descend from it.
    """
    oid = reader.head()
    found: list[RemoteCommit] = []
    while oid != anchor:
        if oid is None:
            return History((), "remote_main_unreadable")
        commit = reader.commit(oid)
        if commit is None:
            return History((), "remote_commit_unreadable")
        if len(commit.parents) != 1 or len(found) == HISTORY_LIMIT:
            return History((), "remote_main_not_a_descendant_of_the_base")
        found.append(commit)
        oid = commit.parents[0]
    return History(tuple(reversed(found)), None)
