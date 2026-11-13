"""Two branches that first add one address with different content are refused (#871, N1, B1b).

"First committed" cannot be read from the order ``git log`` lists two
concurrent branches in: that order follows the merge's parent order, which is
the operator's choice, not the order of publication. A stale clone's sync that
adds ``new-z`` for another document, merged with a ``main`` that published
``new-z`` and resolved to the local side, would otherwise re-own a published
address through a fast-forward push. So an address first added with different
content on two commits neither of which contains the other is refused,
whichever side the merge kept and in whichever parent order. Every case is a
real temporary git repository.
"""

from pathlib import Path

import pytest

from lovspor.errors import IdentitySchemaError
from lovspor.identity.registry import EnforcedCorpus, PriorHolder, SlugOwner, SlugRegistry
from lovspor.identity.registry_history import verified_corpus_identity
from lovspor.identity.schema import IDENTITY_FILENAME, declaration_json
from tests.unit.identity_git_fixtures import commit_all, git
from tests.unit.test_identity_downgrade import FIRST, SECOND, _seeded
from tests.unit.test_identity_registry_history import (
    AT,
    ENTRY,
    OLD,
    THIRD,
    _owner,
    _registry,
    _write,
)


@pytest.fixture
def migrated(tmp_path: Path) -> Path:
    repo = _seeded(tmp_path)
    _write(repo, _registry())
    (repo / IDENTITY_FILENAME).write_text(declaration_json(), encoding="utf-8")
    commit_all(repo, "migration: declare identity schema 1")
    return repo


PUBLISHED = _owner(SECOND)
STALE = _owner(FIRST)
#: Content variants of the stale side's add, each different from ``PUBLISHED``.
VARIANTS: dict[str, SlugOwner] = {
    "owner": STALE,
    "assigned_at": SlugOwner(owner=SECOND, assigned_at="2026-06-02T00:00:00+00:00"),
    "basis": SlugOwner(
        owner=SECOND,
        assigned_at=AT,
        basis="threshold-exception",
        prior_holders=(PriorHolder(doc_id=THIRD, first_commit="a" * 40, last_commit="b" * 40),),
    ),
}


def _with(entry: SlugOwner, slug: str = "new-z") -> SlugRegistry:
    return _registry().assign("forskrifter", slug, entry)


def _commit_on(repo: Path, branch: str, registry: SlugRegistry) -> str:
    git(repo, "checkout", "-q", branch)
    _write(repo, registry)
    return commit_all(repo, f"sync on {branch}")


def _merge(repo: Path, into: str, other: str, kept: SlugRegistry) -> str:
    """Merge ``other`` into ``into``, resolving ``slugs.json`` to ``kept``."""
    git(repo, "checkout", "-q", into)
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "-s", "ours", other)
    _write(repo, kept)
    return commit_all(repo, f"merge {other} into {into}")


def _diverge(repo: Path, stale: SlugOwner) -> tuple[str, str]:
    """``main`` publishes ``new-z`` for SECOND; a stale clone's ``local`` adds it as ``stale``."""
    git(repo, "branch", "local")
    published = _commit_on(repo, "main", _with(PUBLISHED))
    local = _commit_on(repo, "local", _with(stale))
    return published, local


#: (branch merged into, branch merged, which side's ``slugs.json`` is kept).
MERGES = {
    "pull into local, keep local": ("local", "main", "local"),
    "pull into local, keep main": ("local", "main", "main"),
    "local into main, keep local": ("main", "local", "local"),
    "local into main, keep main": ("main", "local", "main"),
}


@pytest.mark.parametrize("merge", sorted(MERGES))
def test_divergent_first_adds_are_refused_whatever_the_merge(migrated: Path, merge: str) -> None:
    published, local = _diverge(migrated, STALE)
    into, other, kept = MERGES[merge]
    _merge(migrated, into, other, _with(STALE if kept == "local" else PUBLISHED))

    with pytest.raises(IdentitySchemaError) as caught:
        verified_corpus_identity(migrated)

    message = str(caught.value)
    for fact in ("forskrifter/new-z", published, local, FIRST, SECOND, "reset --hard"):
        assert fact in message


@pytest.mark.parametrize("point", sorted(ENTRY))
def test_every_entry_point_refuses_the_stale_pull(migrated: Path, point: str) -> None:
    _diverge(migrated, STALE)
    _merge(migrated, "local", "main", _with(STALE))
    call, error = ENTRY[point]

    with pytest.raises(error, match="forskrifter/new-z"):
        call(migrated)


@pytest.mark.parametrize("field", sorted(VARIANTS))
def test_any_field_of_the_entry_makes_the_adds_divergent(migrated: Path, field: str) -> None:
    _diverge(migrated, VARIANTS[field])
    _merge(migrated, "local", "main", _with(VARIANTS[field]))

    with pytest.raises(IdentitySchemaError, match="forskrifter/new-z"):
        verified_corpus_identity(migrated)


@pytest.mark.parametrize("into, other", [("local", "main"), ("main", "local")])
def test_identical_first_adds_on_both_branches_pass(migrated: Path, into: str, other: str) -> None:
    _diverge(migrated, PUBLISHED)
    _merge(migrated, into, other, _with(PUBLISHED))

    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)


def test_a_three_way_history_names_the_divergent_branch(migrated: Path) -> None:
    git(migrated, "branch", "agree")
    git(migrated, "branch", "differ")
    published = _commit_on(migrated, "main", _with(PUBLISHED))
    agree = _commit_on(migrated, "agree", _with(PUBLISHED))
    differ = _commit_on(migrated, "differ", _with(_owner(OLD)))
    _merge(migrated, "main", "agree", _with(PUBLISHED))
    _merge(migrated, "main", "differ", _with(PUBLISHED))

    with pytest.raises(IdentitySchemaError) as caught:
        verified_corpus_identity(migrated)

    message = str(caught.value)
    assert all(fact in message for fact in (differ, OLD, SECOND))
    assert published in message or agree in message


def test_a_side_branch_built_on_the_add_may_still_restore_a_rewrite(migrated: Path) -> None:
    """Not divergent: the side branch contains the commit that first added the entry."""
    published = _commit_on(migrated, "main", _with(PUBLISHED))
    git(migrated, "branch", "side", published)
    _commit_on(migrated, "side", _with(STALE))
    _commit_on(migrated, "side", _with(PUBLISHED))
    _merge(migrated, "main", "side", _with(PUBLISHED))

    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)


def test_unrelated_adds_on_two_branches_pass(migrated: Path) -> None:
    git(migrated, "branch", "local")
    _commit_on(migrated, "main", _with(PUBLISHED, "new-y"))
    _commit_on(migrated, "local", _with(STALE, "new-z"))
    both = _with(PUBLISHED, "new-y").assign("forskrifter", "new-z", STALE)
    _merge(migrated, "local", "main", both)

    assert isinstance(verified_corpus_identity(migrated), EnforcedCorpus)
