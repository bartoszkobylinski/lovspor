"""Collected tests run with each module and class contiguous, whatever order they were named in.

mutmut 3.8 runs a mutant's covering tests by passing their node ids from a set,
so modules interleave and every module- or class-scoped fixture is rebuilt at
each switch. On PR #374 that turned 21 s of tests into 300 s, and every
surviving mutant of ``_release_vars`` crossed the timeout (issue #380).
"""

import os
import subprocess
import sys
from pathlib import Path

import tests.conftest
from tests import scope_order
from tests.scope_order import grouped_by_scope

REPO_ROOT = Path(__file__).parents[2]

MODULE_SOURCE = """\
import pytest

@pytest.fixture(scope="module")
def built():
    with open("setups.txt", "a") as handle:
        handle.write("{name}\\n")

def test_one(built):
    pass

def test_two(built):
    pass
"""

INTERLEAVED = [
    "test_a.py::test_one",
    "test_b.py::test_one",
    "test_a.py::test_two",
    "test_b.py::test_two",
]

CLASS_SOURCE = """\
import pytest

@pytest.fixture(scope="class")
def built(request):
    with open("setups.txt", "a") as handle:
        handle.write(f"{request.cls.__name__}\\n")

class TestA:
    def test_one(self, built):
        pass

    def test_two(self, built):
        pass

class TestB:
    def test_one(self, built):
        pass

    def test_two(self, built):
        pass
"""

INTERLEAVED_CLASSES = [
    "test_classes.py::TestA::test_one",
    "test_classes.py::TestB::test_one",
    "test_classes.py::TestA::test_two",
    "test_classes.py::TestB::test_two",
]


def _by_chain(chains: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return grouped_by_scope(chains, lambda chain: chain[:-1])


class TestGroupedByScope:
    def test_interleaved_modules_are_made_contiguous_in_first_seen_order(self) -> None:
        chains = [("a", "1"), ("b", "2"), ("a", "3"), ("b", "4")]

        assert _by_chain(chains) == [("a", "1"), ("a", "3"), ("b", "2"), ("b", "4")]

    def test_classes_are_grouped_inside_their_module_not_across_it(self) -> None:
        chains = [("a", "A", "1"), ("b", "2"), ("a", "B", "3"), ("a", "A", "4")]

        assert _by_chain(chains) == [
            ("a", "A", "1"),
            ("a", "A", "4"),
            ("a", "B", "3"),
            ("b", "2"),
        ]

    def test_an_order_that_is_already_grouped_is_left_exactly_as_it_is(self) -> None:
        chains = [("b", "B", "2"), ("b", "B", "1"), ("b", "3"), ("a", "A", "4"), ("a", "5")]

        assert _by_chain(chains) == chains

    def test_no_items_is_no_items(self) -> None:
        assert _by_chain([]) == []


class TestTheRepoSuiteUsesIt:
    def test_the_tests_conftest_carries_the_hook(self) -> None:
        assert (
            tests.conftest.pytest_collection_modifyitems
            is scope_order.pytest_collection_modifyitems
        )


def _setups(tmp_path: Path, *plugin: str) -> list[str]:
    for name in ("a", "b"):
        (tmp_path / f"test_{name}.py").write_text(MODULE_SOURCE.format(name=name))
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *plugin, *INTERLEAVED],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "setups.txt").read_text().split()


def _class_setups(tmp_path: Path, *plugin: str) -> list[str]:
    (tmp_path / "test_classes.py").write_text(CLASS_SOURCE)
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *plugin,
            *INTERLEAVED_CLASSES,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "setups.txt").read_text().split()


class TestNamedNodeIdsInAnInterleavedOrder:
    def test_without_the_hook_each_module_fixture_is_rebuilt_at_every_switch(
        self, tmp_path: Path
    ) -> None:
        assert _setups(tmp_path) == ["a", "b", "a", "b"]

    def test_with_the_hook_each_module_fixture_is_built_once(self, tmp_path: Path) -> None:
        assert _setups(tmp_path, "-p", "tests.scope_order") == ["a", "b"]

    def test_with_the_hook_each_class_fixture_is_built_once(self, tmp_path: Path) -> None:
        assert _class_setups(tmp_path, "-p", "tests.scope_order") == ["TestA", "TestB"]
