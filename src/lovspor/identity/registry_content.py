"""What an enforced registry says, checked against the corpus it ships with (#911).

``require_registry_history`` holds the registry to every entry committed
since the migration, so the migration commit's own ``slugs.json`` is the
baseline, and nothing else checks it. A hand-built or partial migration
commit could then register any owner and keep it forever. The checks below
are the invariants the #871 plan guarantees and every later sync keeps:

- every owner and every prior holder is a document of the manifest: a
  tombstone is never dropped, so an unknown ``doc_id`` is a forged or phantom
  entry, or a migration that left out its restored tombstones;
- every current record sits on an address it owns: the sync refuses to serve
  one document at another's address, and the plan renames such holders (U1);
- every slug history ``<directory>/history/<slug>.json`` names its address's
  owner: the plan rebuilds each one that does not, and the sync writes slug
  histories for owners only, so a mismatch is a migration without its history
  repairs.

Duplicate keys in ``slugs.json`` are refused where the file is read
(``lovspor.identity.registry.read_registry``): with them, which owner counts
would depend on the parser.
"""

import json
from pathlib import Path, PurePosixPath

from lovspor.errors import IdentitySchemaError
from lovspor.identity.registry import (
    DIRECTORY_OF_DATASET,
    SlugRegistry,
    record_address,
)
from lovspor.identity.schema import REGISTRY_FILENAME
from lovspor.storage.manifest import Manifest

_LISTED = 5


def _problem(name: str, found: list[str]) -> str:
    return f"{name} ({len(found)}, first {found[:_LISTED]})"


def unknown_documents(registry: SlugRegistry, manifest: Manifest) -> list[str]:
    """Every entry whose owner or a prior holder is no document of ``manifest``."""
    found: list[str] = []
    for directory, entries in sorted(registry.slugs.items()):
        for slug, entry in sorted(entries.items()):
            named = [entry.owner, *(holder.doc_id for holder in entry.prior_holders)]
            found += [
                f"{directory}/{slug} names {doc_id}"
                for doc_id in named
                if doc_id not in manifest.documents
            ]
    return found


def misowned_records(registry: SlugRegistry, manifest: Manifest) -> list[str]:
    """Every current record on an address the registry gives another document."""
    found: list[str] = []
    for doc_id, record in sorted(manifest.documents.items()):
        directory, slug = record_address(record)
        owner = registry.owner_of(directory, slug)
        if record.status == "current" and owner != doc_id:
            found.append(f"{directory}/{slug} holds {doc_id} but is owned by {owner}")
    return found


def _history_doc_id(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    value = data.get("doc_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


def misowned_histories(repo: Path, registry: SlugRegistry) -> list[str]:
    """Every slug history that names no document, or another than its address's owner."""
    found: list[str] = []
    for directory in sorted(DIRECTORY_OF_DATASET.values()):
        for path in sorted((repo / directory / "history").glob("*.json")):
            slug = PurePosixPath(path.name).stem
            owner = registry.owner_of(directory, slug)
            named = _history_doc_id(path)
            if named is None or named != owner:
                found.append(f"{directory}/history/{path.name} names {named}, owner {owner}")
    return found


Check = tuple[str, list[str]]


def manifest_checks(registry: SlugRegistry, manifest: Manifest) -> list[Check]:
    """The checks that need the manifest only (the planner reads its base through git)."""
    return [
        ("doc_ids the manifest lacks", unknown_documents(registry, manifest)),
        ("current addresses owned by another document", misowned_records(registry, manifest)),
    ]


def refuse_disagreement(where: str, checks: list[Check]) -> None:
    """Raise ``IdentitySchemaError`` naming every check that found something."""
    problems = [_problem(name, found) for name, found in checks if found]
    if problems:
        raise IdentitySchemaError(
            f"{where}: {REGISTRY_FILENAME} disagrees with the corpus: {'; '.join(problems)}. "
            f"The migration commit must hold what its plan computed: re-plan and re-apply "
            f"with lovspor ops identity-plan (#871, #911)",
        )


def require_registry_content(repo: Path, registry: SlugRegistry, manifest: Manifest) -> None:
    """Refuse a registry the corpus beside it contradicts (see the module docstring)."""
    histories = ("slug histories naming a non-owner", misowned_histories(repo, registry))
    refuse_disagreement(str(repo), [*manifest_checks(registry, manifest), histories])
