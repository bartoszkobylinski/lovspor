"""Walk every ``manifest.json`` revision reachable from a base commit (#871).

The walk is the migration's only source of facts. For each ``doc_id`` it
keeps the last record any revision held and, when the id is absent at the
end, the first revision it went missing in; for each address
(``directory/slug``) it keeps the first holder and every later one, in
order. Nothing is inferred: an id or holder that no revision names does not
appear.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from lovspor.errors import IdentityMigrationError
from lovspor.identity.corpus_git import MANIFEST_PATH, manifest_commits, read_blob

Address = tuple[str, str]
Documents = dict[str, dict[str, object]]


class HolderSpan(BaseModel):
    """One document's tenure on an address: the first and last revision holding it.

    ``assigned_at`` is the first revision's ``generated_at``. A document that
    left the address and came back keeps one span, from its first to its last
    revision there.
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str
    first_commit: str
    last_commit: str
    assigned_at: str


class LastSeen(BaseModel):
    """A document's last record in the walk and where it was seen and lost."""

    model_config = ConfigDict(frozen=True)

    commit: str
    record: dict[str, object]
    missing_since: str | None = None


class ManifestWalk(BaseModel):
    """Everything the manifest history says about identities and addresses."""

    model_config = ConfigDict(frozen=True)

    revisions: list[str]
    last_seen: dict[str, LastSeen]
    holders: dict[str, dict[str, list[HolderSpan]]]

    def position(self, commit: str) -> int:
        """The index of a manifest revision in walk order (oldest is 0)."""
        return self.revisions.index(commit)


@dataclass
class _State:
    previous: set[str] = field(default_factory=set)
    seen: dict[str, tuple[str, dict[str, object]]] = field(default_factory=dict)
    missing_since: dict[str, str] = field(default_factory=dict)
    spans: dict[Address, dict[str, HolderSpan]] = field(default_factory=dict)


def raw_address(doc_id: str, record: object, commit: str) -> Address:
    """``(directory, slug)`` of a raw manifest record; a slug-less one uses its file stem."""
    if not isinstance(record, dict) or not isinstance(record.get("markdown_path"), str):
        raise IdentityMigrationError(f"{commit}: record {doc_id} has no markdown_path")
    path = PurePosixPath(record["markdown_path"])
    slug = record.get("slug")
    return path.parts[0], slug if isinstance(slug, str) and slug else path.stem


def parse_revision(blob: bytes | None, commit: str) -> tuple[str, Documents]:
    """``(generated_at, documents)`` of one manifest revision; refuses an unreadable one."""
    if blob is None:
        raise IdentityMigrationError(f"{commit}: no {MANIFEST_PATH}")
    try:
        data = json.loads(blob.decode("utf-8"))
        generated_at = datetime.fromisoformat(data["generated_at"]).isoformat()
        documents = data["documents"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise IdentityMigrationError(f"{commit}: unreadable {MANIFEST_PATH}: {exc}") from exc
    if not isinstance(documents, dict):
        raise IdentityMigrationError(f"{commit}: {MANIFEST_PATH} documents is not an object")
    return generated_at, documents


def _observe(state: _State, commit: str, generated_at: str, documents: Documents) -> None:
    """Fold one revision into the walk state, ids in sorted order."""
    for doc_id in sorted(documents):
        record = documents[doc_id]
        address = raw_address(doc_id, record, commit)
        state.seen[doc_id] = (commit, record)
        state.missing_since.pop(doc_id, None)
        held = state.spans.setdefault(address, {})
        span = held.get(doc_id)
        if span is None:
            span = HolderSpan(
                doc_id=doc_id, first_commit=commit, last_commit=commit, assigned_at=generated_at
            )
        held[doc_id] = span.model_copy(update={"last_commit": commit})
    for gone in sorted(state.previous - documents.keys()):
        state.missing_since[gone] = commit
    state.previous = set(documents)


def _nest(flat: dict[Address, object]) -> dict[str, dict[str, object]]:
    nested: dict[str, dict[str, object]] = {}
    for (directory, slug), value in sorted(flat.items()):
        nested.setdefault(directory, {})[slug] = value
    return nested


def _result(state: _State, revisions: list[str]) -> ManifestWalk:
    last_seen = {
        doc_id: LastSeen(
            commit=commit, record=record, missing_since=state.missing_since.get(doc_id)
        )
        for doc_id, (commit, record) in sorted(state.seen.items())
    }
    return ManifestWalk(
        revisions=revisions,
        last_seen=last_seen,
        holders=_nest({a: list(spans.values()) for a, spans in state.spans.items()}),  # type: ignore[arg-type]
    )


def walk_manifests(repo: Path, base: str) -> ManifestWalk:
    """Fold every manifest revision reachable from ``base``, oldest first."""
    revisions = manifest_commits(repo, base)
    if not revisions:
        raise IdentityMigrationError(f"{base}: no commit reachable from it touches {MANIFEST_PATH}")
    state = _State()
    for commit in revisions:
        generated_at, documents = parse_revision(read_blob(repo, commit, MANIFEST_PATH), commit)
        _observe(state, commit, generated_at, documents)
    return _result(state, revisions)
