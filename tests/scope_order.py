"""Run collected tests with each module and class contiguous.

pytest runs explicitly named node ids in the order they were named, and tears
a module- or class-scoped fixture down whenever the next test leaves its scope.
mutmut 3.8 names a mutant's covering tests from a set, so modules interleave
and the release suite's module fixtures (~1.2 s each) were rebuilt for almost
every test: a surviving mutant ran 300 s of tests against a limit derived from
21 s, and was reported as a timeout instead of a survivor (issue #380).

The sort is stable and keyed on first appearance, so an order that is already
grouped — every ordinary run — comes back unchanged.
"""

from collections.abc import Callable, Hashable, Sequence

import pytest


def grouped_by_scope[T](items: Sequence[T], scopes: Callable[[T], Sequence[Hashable]]) -> list[T]:
    """``items`` reordered so each scope prefix is contiguous, in first-seen order."""
    chains = [tuple(scopes(item)) for item in items]
    first_seen: dict[tuple[Hashable, ...], int] = {}
    for index, chain in enumerate(chains):
        for depth in range(1, len(chain) + 1):
            first_seen.setdefault(chain[:depth], index)
    # The item's own index closes the key: a test sitting directly in a module
    # ranks by where it stood, not ahead of every class in that module.
    keys = [
        [first_seen[chain[:depth]] for depth in range(1, len(chain) + 1)] + [index]
        for index, chain in enumerate(chains)
    ]
    order = sorted(range(len(items)), key=keys.__getitem__)
    return [items[index] for index in order]


def _fixture_scopes(item: pytest.Item) -> list[str]:
    return [node.nodeid for node in item.listchain()[1:-1]]


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    items[:] = grouped_by_scope(items, _fixture_scopes)
