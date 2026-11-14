"""Re-read the Git evidence of a threshold exception from the corpus (#921).

A listed ``threshold-exception`` counts only when the corpus itself shows it,
whatever the list says (ADR-0026 draft):

- every evidence commit and the U1 threshold are in the history of the
  migration's base;
- the first owner holds the address at ``first_owner_first_commit``, and no
  document holds it at any parent of that commit: it is the first holding;
- the served owner holds the address at ``served_owner_first_commit``;
- the served owner is the address's published owner at the U1 threshold
  (``published_owners``: its current record, else its slug history, else its
  one removed record), so a document that came after the threshold never
  qualifies.
"""

from functools import lru_cache
from pathlib import Path

from lovspor.errors import IdentityMigrationError
from lovspor.identity.corpus_git import MANIFEST_PATH, is_ancestor, parents_of, read_blob
from lovspor.identity.owner_exceptions import Address, ThresholdException
from lovspor.identity.published import published_owners
from lovspor.identity.walk import parse_revision, raw_address


@lru_cache(maxsize=64)
def address_holders(repo: Path, commit: str) -> dict[Address, frozenset[str]]:
    """``address -> doc_ids`` of every manifest record at ``commit``, whatever its status;
    empty for a commit without a manifest (memoised)."""
    blob = read_blob(repo, commit, MANIFEST_PATH)
    if blob is None:
        return {}
    _, documents = parse_revision(blob, commit)
    holders: dict[Address, set[str]] = {}
    for doc_id, record in documents.items():
        holders.setdefault(raw_address(doc_id, record, commit), set()).add(doc_id)
    return {address: frozenset(ids) for address, ids in holders.items()}


def _held(repo: Path, commit: str, address: Address) -> frozenset[str]:
    return address_holders(repo, commit).get(address, frozenset())


def _outside_history(repo: Path, listed: ThresholdException, threshold: str, tip: str) -> list[str]:
    commits = {
        "first_owner_first_commit": listed.evidence.first_owner_first_commit,
        "served_owner_first_commit": listed.evidence.served_owner_first_commit,
        "u1_threshold": threshold,
    }
    return [
        f"{name} {commit} is not in the history of {tip}"
        for name, commit in commits.items()
        if not is_ancestor(repo, commit, tip)
    ]


def _first_holding(repo: Path, listed: ThresholdException, address: Address) -> list[str]:
    commit = listed.evidence.first_owner_first_commit
    found = []
    if listed.first_owner_doc_id not in _held(repo, commit, address):
        found.append(f"first owner {listed.first_owner_doc_id} does not hold it at {commit}")
    for parent in parents_of(repo, commit):
        if earlier := _held(repo, parent, address):
            found.append(f"{sorted(earlier)} already held it at {parent}, before {commit}")
    return found


def _served_holding(
    repo: Path, listed: ThresholdException, address: Address, threshold: str
) -> list[str]:
    commit = listed.evidence.served_owner_first_commit
    served = listed.served_owner_doc_id
    found = []
    if served not in _held(repo, commit, address):
        found.append(f"served owner {served} does not hold it at {commit}")
    published = published_owners(repo, threshold).get(address)
    if published != served:
        found.append(f"the owner published at the threshold {threshold} is {published}")
    return found


def evidence_problems(
    repo: Path, listed: ThresholdException, threshold: str, tip: str
) -> list[str]:
    """Every way the corpus contradicts ``listed``'s evidence; empty when it holds."""
    address = (listed.directory, listed.slug)
    try:
        found = _outside_history(repo, listed, threshold, tip)
        if found:
            return found
        return _first_holding(repo, listed, address) + _served_holding(
            repo, listed, address, threshold
        )
    except IdentityMigrationError as exc:
        return [f"its evidence cannot be read: {exc}"]
