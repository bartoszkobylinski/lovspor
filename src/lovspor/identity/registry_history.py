"""The registry's own git history as evidence that it only ever grew (#871, N1).

``ensure_append_only`` guards the writer: a sync never replaces ``slugs.json``
with one that drops or re-owns an entry of the file on disk. A commit made
around the writer (a hand edit, a bad merge resolution of the megabyte JSON,
a future writer that skips the guard) is invisible to that guard, because the
file on disk *is* the edited one, and the coverage check sees only addresses
of the current manifest: at lovverk about four registry entries in ten are
not. A dropped entry lets the next sync hand its address to another document.

So at sync start, in ``lovspor audit`` and in the planner, the registry is
checked against every revision of ``slugs.json`` since the migration (the
oldest commit that added ``identity.json``): every entry, as it was first
committed, must still be there unchanged, its owner, ``assigned_at``, basis
and prior holders. The registry checked is the one the caller reads, the
working tree's at sync start and in the audit, so an uncommitted edit is
held to the same rule.

Method: one ``git log`` lists the revisions, chunked ``cat-file --batch``
reads them, each is parsed once, and only the slugs a revision adds are
validated and kept, with the commit that first held them. The verdict on the
history is a function of the commit, so it is memoised per repository and
commit. Measured on a registry of 11 036 entries (2 MB) with 300 later
revisions: 3.5 s cold (about 12 ms a revision, linear), 0.02 s memoised.

The comparison is by content, so:

* a formatting-only rewrite is tolerated: ownership is the content, and the
  next sync writes the canonical form again;
* a drop or rewrite restored to the original before the check is tolerated:
  the documented recovery is "restore the entry and commit", which a refusal
  of every revision that ever went wrong would make impossible without
  rewriting history;
* a revision that cannot be parsed contributes nothing: an entry it held is
  judged by the revisions around it.

A history that cannot be read in full (a shallow clone, a directory that is
not the root of its repository, git failing) fails closed.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from lovspor.errors import IdentityMigrationError, IdentitySchemaError
from lovspor.identity.corpus_git import (
    MANIFEST_PATH,
    commits_touching_since,
    first_commit_adding,
    is_shallow,
    iter_revisions,
    resolve_commit,
    toplevel,
)
from lovspor.identity.registry import (
    CorpusIdentity,
    EnforcedCorpus,
    SlugOwner,
    SlugRegistry,
    corpus_identity,
    require_registry_covers,
)
from lovspor.identity.schema import ENFORCED_IDENTITY_SCHEMA, IDENTITY_FILENAME, REGISTRY_FILENAME
from lovspor.storage.manifest import read_manifest

#: ``directory -> slug -> (the entry as first committed, that commit)``.
FirstEntries = dict[str, dict[str, tuple[SlugOwner, str]]]


def _unreadable(repo: Path, detail: str) -> IdentitySchemaError:
    return IdentitySchemaError(
        f"{repo}: identity schema {ENFORCED_IDENTITY_SCHEMA}, but {detail}; the history of "
        f"{REGISTRY_FILENAME} since the migration cannot be checked for dropped or re-owned "
        f"entries, so refusing to run without slug ownership (#871)",
    )


def _require_full_history(repo: Path) -> None:
    if toplevel(repo).resolve() != repo.resolve():
        raise _unreadable(repo, "it is not the root of a git repository")
    if is_shallow(repo):
        detail = "it is a shallow clone whose history is cut off (`git fetch --unshallow`)"
        raise _unreadable(repo, detail)


def _slug_entries(blob: bytes | None) -> dict[str, dict[str, object]]:
    """``directory -> slug -> raw entry`` of one revision; empty when it cannot be read."""
    try:
        data = json.loads((blob or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    slugs = data.get("slugs") if isinstance(data, dict) else None
    if not isinstance(slugs, dict):
        return {}
    return {directory: entries for directory, entries in slugs.items() if isinstance(entries, dict)}


def _entry(raw: object) -> SlugOwner | None:
    try:
        return SlugOwner.model_validate(raw)
    except ValidationError:
        return None


def _absorb(first: FirstEntries, blob: bytes | None, commit: str) -> None:
    """Keep each valid entry ``blob`` holds that no earlier revision did."""
    for directory, entries in _slug_entries(blob).items():
        held = first.setdefault(directory, {})
        for slug in entries.keys() - held.keys():
            entry = _entry(entries[slug])
            if entry is not None:
                held[slug] = (entry, commit)


@lru_cache(maxsize=8)
def first_entries(repo: Path, commit: str) -> FirstEntries:
    """Every registry entry as first committed since the migration, up to ``commit``.

    ``repo`` resolved and ``commit`` a full sha: the answer is then fixed, and
    memoised. Callers must not mutate it. Empty when no commit added
    ``identity.json``.
    """
    _require_full_history(repo)
    first: FirstEntries = {}
    migration = first_commit_adding(repo, commit, IDENTITY_FILENAME)
    if migration is None:
        return first
    commits = commits_touching_since(repo, commit, migration, REGISTRY_FILENAME)
    blobs = iter_revisions(repo, commits, REGISTRY_FILENAME)
    for revision, blob in zip(commits, blobs, strict=True):
        _absorb(first, blob, revision)
    return first


def _violations(first: FirstEntries, registry: SlugRegistry) -> list[str]:
    found = []
    for directory, entries in first.items():
        held = registry.slugs.get(directory, {})
        for slug, (entry, commit) in entries.items():
            kept = held.get(slug)
            if kept != entry:
                change = "drops" if kept is None else f"rewrites (owner now {kept.owner})"
                found.append(
                    f"{directory}/{slug}: {change} the entry of {entry.owner} from {commit}"
                )
    return sorted(found)


def require_registry_history(repo: Path, registry: SlugRegistry, rev: str = "HEAD") -> None:
    """Refuse ``registry`` unless it keeps every entry committed since the migration, unchanged."""
    try:
        first = first_entries(repo.resolve(), resolve_commit(repo, rev))
    except IdentityMigrationError as exc:
        raise _unreadable(repo, f"its history cannot be read: {exc}") from exc
    found = _violations(first, registry)
    if found:
        raise IdentitySchemaError(
            f"{repo}: {REGISTRY_FILENAME} {found[0]} ({len(found)} entr(y/ies) in all): a "
            f"registry entry is never deleted or rewritten, or the next sync could give its "
            f"address to another document. Restore each entry from the commit named "
            f"(`git -C {repo} show <commit>:{REGISTRY_FILENAME}`) and commit (#871)",
        )


def verified_corpus_identity(repo: Path) -> CorpusIdentity:
    """``corpus_identity``; an enforced registry covers the working-tree manifest and keeps
    every entry its history committed.

    For a clean tree (sync start, the audit): mid-sync the manifest on disk is
    ahead of the registry until the commit that stages both.
    """
    identity = corpus_identity(repo)
    if not isinstance(identity, EnforcedCorpus):
        return identity
    manifest_path = repo / MANIFEST_PATH
    if manifest_path.exists():
        require_registry_covers(identity.registry, read_manifest(manifest_path), str(repo))
    require_registry_history(repo, identity.registry)
    return identity
