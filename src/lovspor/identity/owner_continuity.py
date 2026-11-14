"""The registry keeps every address owner the corpus published before it (#911).

``require_registry_history`` freezes every entry from the migration commit on,
and ``registry_content`` holds the registry to the manifest and the slug
histories it ships with. Neither looks behind the migration commit: a
migration that gives a published address to another document, and rewrites
that address's slug history and manifest to match, passes both. So the
registry is also held to the owners its base published:

``published_owners``: a current record (200), else a slug history (301/410),
else the address's one removed record, at the migration's base.

A first holder is a historical owner too: a ``threshold-exception`` entry,
where the migration's U1 threshold kept a later holder over the document that
held the address first, is a reassignment of that first holder.

Both are refused unless the reviewed list ``owner_reassignments.json``
(``owner_exceptions``, schema 2) names the change and is approved by its
digest (#921, ADR-0026 draft). A reassignment counts only when the migration's
base is the list's ``reviewed_base``; a threshold exception only when the entry
names the registry's first owner (with its first commit) and served owner,
and its Git evidence holds in the corpus (``owner_evidence``). An unapproved
list, or one edited after approval, lists nothing: every change is refused,
the refusal saying the list is BLOCKED on the owner's approval.
"""

from pathlib import Path

from lovspor.errors import IdentityMigrationError, IdentitySchemaError
from lovspor.identity.owner_evidence import evidence_problems
from lovspor.identity.owner_exceptions import (
    Address,
    Allowed,
    Reviewed,
    ThresholdException,
)
from lovspor.identity.published import migration_base, published_owners
from lovspor.identity.registry import THRESHOLD_EXCEPTION, SlugOwner, SlugRegistry

_LISTED = 5
APPROVE_COMMAND = (
    "lovspor ops identity-exceptions-approve --path {path} --digest {digest} "
    "--by <name> --on <YYYY-MM-DD>"
)


def reassigned(owners: dict[Address, str], registry: SlugRegistry, allowed: Allowed) -> list[str]:
    """Every published address whose registry owner is another, and not as listed."""
    found = []
    for (directory, slug), held in sorted(owners.items()):
        owner = registry.owner_of(directory, slug)
        if owner != held and allowed.get((directory, slug)) != (held, owner):
            found.append(f"{directory}/{slug}: {held} at the base, {owner} in the registry")
    return found


def names_entry(listed: ThresholdException, entry: SlugOwner) -> bool:
    """True when ``listed`` names ``entry``'s served owner, first owner and its first commit."""
    if not entry.prior_holders:
        return False
    return (
        listed.served_owner_doc_id == entry.served_owner_doc_id
        and listed.first_owner_doc_id == entry.first_owner_doc_id
        and listed.evidence.first_owner_first_commit == entry.prior_holders[0].first_commit
    )


def _exception_problems(
    repo: Path, entry: SlugOwner, listed: ThresholdException | None, scope: tuple[str, str | None]
) -> list[str]:
    """``scope`` is ``(u1_threshold, migration base)``."""
    if listed is None or not names_entry(listed, entry):
        return [f"{entry.owner} kept over first holder {entry.first_owner_doc_id}, unlisted"]
    threshold, base = scope
    if base is None:
        return ["a root migration commit has no history to hold its evidence"]
    return evidence_problems(repo, listed, threshold, base)


def threshold_findings(
    repo: Path, registry: SlugRegistry, reviewed: Reviewed, base: str | None
) -> list[str]:
    """Every ``threshold-exception`` entry the approved list does not name, or whose
    listed evidence the corpus contradicts below the migration's ``base``."""
    found = []
    scope = (reviewed.corpus.u1_threshold, base)
    for directory, entries in sorted(registry.slugs.items()):
        for slug, entry in sorted(entries.items()):
            if entry.basis != THRESHOLD_EXCEPTION:
                continue
            listed = reviewed.threshold.get((directory, slug))
            problems = _exception_problems(repo, entry, listed, scope)
            found += [f"{directory}/{slug}: {problem}" for problem in problems]
    return found


def base_changes(reviewed: Reviewed, base: str | None) -> Allowed:
    """The listed reassignments, when ``base`` is the base they were reviewed against."""
    return reviewed.changes if base == reviewed.corpus.reviewed_base else {}


def _list_state(reviewed: Reviewed, base: str | None) -> str:
    if not reviewed.approved:
        command = APPROVE_COMMAND.format(path=reviewed.path, digest=reviewed.digest)
        return f"the list is {reviewed.state}; after review the owner runs `{command}`"
    if base != reviewed.corpus.reviewed_base:
        return (
            f"the list is {reviewed.state}, for base {reviewed.corpus.reviewed_base}, not the "
            f"migration's base {base}: its reassignments do not apply"
        )
    return f"the list is {reviewed.state}"


def require_owner_continuity(repo: Path, registry: SlugRegistry, reviewed: Reviewed) -> None:
    """Refuse ``registry`` when it re-owns an address the migration's base published, or
    keeps a later holder over a first holder, unless the approved list names the change."""
    try:
        base = migration_base(repo)
        owners = {} if base is None else published_owners(repo.resolve(), base)
        found = reassigned(owners, registry, base_changes(reviewed, base))
        found += threshold_findings(repo.resolve(), registry, reviewed, base)
    except IdentityMigrationError as exc:
        raise IdentitySchemaError(f"{repo}: the migration's base cannot be read: {exc}") from exc
    if found:
        raise IdentitySchemaError(
            f"{repo}: the registry changes {len(found)} historical owner(s), first "
            f"{found[:_LISTED]}: an owner the corpus published, and a first holder, is kept "
            f"unless the change is listed in {reviewed.path.name} and approved by its "
            f"digest (#911, #921); {_list_state(reviewed, base)}",
        )
