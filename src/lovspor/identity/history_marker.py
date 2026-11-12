"""The corpus's git history as independent evidence of its identity schema (#871, N1).

``identity.json`` and ``slugs.json`` are files in the corpus. One commit can
delete both, truncate the registry, or revert the migration that wrote them,
and the two files alone would then read as a corpus that was never migrated.
The evidence that does not live in those files is the history: the commit
that wrote ``identity.json`` stays reachable from the branch the sync pushes,
because that push is a fast-forward. So:

* no ``identity.json`` is the legacy schema 0 only when **no commit reachable
  from HEAD ever added, changed or deleted** ``identity.json`` (one
  ``git log -1``, which stops at the first match);
* a history that cannot answer in full (a shallow clone, a directory that is
  not the root of a git repository, git failing) cannot prove that, and fails
  closed: "cannot tell" is never read as legacy;
* an enforced registry with no entries beside a committed manifest that holds
  documents was truncated, not freshly written.

A rewritten history (a force-push that drops the migration commit) is outside
what this evidence can see: ``docs/data-model.md`` lists that residual.
"""

import json
from pathlib import Path

from lovspor.errors import IdentityMigrationError, IdentitySchemaError
from lovspor.identity.corpus_git import (
    MANIFEST_PATH,
    has_commits,
    is_shallow,
    last_commit_touching,
    read_blob,
    resolve_commit,
    toplevel,
)
from lovspor.identity.schema import (
    ENFORCED_IDENTITY_SCHEMA,
    IDENTITY_FILENAME,
    REGISTRY_FILENAME,
)


def _unproven(repo: Path, detail: str) -> IdentitySchemaError:
    return IdentitySchemaError(
        f"{repo}: no {IDENTITY_FILENAME}, and {detail}; the legacy identity schema needs "
        f"proof that the corpus was never migrated, so refusing to run without slug "
        f"ownership (#871)",
    )


def _readable_history(repo: Path) -> bool:
    """True when ``repo`` holds commits; raise unless its whole history can be read."""
    if toplevel(repo).resolve() != repo.resolve():
        raise _unproven(repo, "it is not the root of a git repository")
    if is_shallow(repo):
        detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
        raise _unproven(repo, detail)
    return has_commits(repo)


def migration_evidence(repo: Path, rev: str = "HEAD") -> str | None:
    """The newest commit reachable from ``rev`` that touched ``identity.json``.

    ``None`` when no commit ever did, including a repository with no commits.
    """
    try:
        if not _readable_history(repo):
            return None
        return last_commit_touching(repo, rev, IDENTITY_FILENAME)
    except IdentityMigrationError as exc:
        raise _unproven(repo, f"cannot read the corpus history: {exc}") from exc


def _restore_source(repo: Path, touched: str) -> str:
    """The commit to restore the declaration from: ``touched``, or its parent when it deleted it."""
    if read_blob(repo, touched, IDENTITY_FILENAME) is not None:
        return touched
    return resolve_commit(repo, f"{touched}^")


def refuse_downgrade(repo: Path, rev: str = "HEAD") -> None:
    """Raise unless no commit reachable from ``rev`` ever touched ``identity.json``.

    The refusal names the commit and the recovery: restore both files from the
    last commit that held them.
    """
    touched = migration_evidence(repo, rev)
    if touched is None:
        return
    try:
        source = _restore_source(repo, touched)
    except IdentityMigrationError as exc:
        raise _unproven(repo, f"the commit that last touched it is unreadable: {exc}") from exc
    raise IdentitySchemaError(
        f"{repo}: no {IDENTITY_FILENAME}, but commit {touched} in the corpus history touched "
        f"it: this corpus was migrated to identity schema {ENFORCED_IDENTITY_SCHEMA} and never "
        f"returns to legacy. Restore the declaration and the registry from the last commit "
        f"that held them, `git -C {repo} checkout {source} -- {IDENTITY_FILENAME} "
        f"{REGISTRY_FILENAME}`, and commit; refusing to run without slug ownership (#871)",
    )


def _document_count(blob: bytes, where: str) -> int:
    try:
        data = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise IdentitySchemaError(f"{where}: unreadable: {exc}") from exc
    documents = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(documents, dict):
        raise IdentitySchemaError(f"{where}: no documents mapping")
    return len(documents)


def committed_documents(repo: Path) -> int:
    """How many records the manifest at HEAD holds; 0 when HEAD has none."""
    where = f"{repo}: HEAD:{MANIFEST_PATH}"
    try:
        blob = read_blob(repo, "HEAD", MANIFEST_PATH)
    except IdentityMigrationError as exc:
        raise IdentitySchemaError(f"{where}: cannot be read: {exc}") from exc
    return 0 if blob is None else _document_count(blob, where)


def refuse_empty_registry(repo: Path, registered: int) -> None:
    """Refuse an enforced registry with no entries while the committed manifest has records.

    Read against HEAD, not the working tree: mid-sync the working-tree manifest
    is ahead of the registry until the commit that stages both.
    """
    if registered:
        return
    documents = committed_documents(repo)
    if documents:
        raise IdentitySchemaError(
            f"{repo}: {REGISTRY_FILENAME} has 0 slugs registered but the committed "
            f"{MANIFEST_PATH} holds {documents} record(s): a registry never loses an entry, "
            f"so it was truncated; restore it from the corpus history (#871)",
        )
