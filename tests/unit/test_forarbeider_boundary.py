"""Forarbeider stay offline and out of every existing law path (#738)."""

import ast
from pathlib import Path

import pytest

import lovspor

SOURCE = Path(lovspor.__file__).parent
PACKAGE = SOURCE / "forarbeider"
FORBIDDEN_IN_PACKAGE = {"httpx", "requests", "urllib.request", "socket", "subprocess", "aiohttp"}
LICENCE_WORDS = ("NLOD", "CC0", "CC-BY")

# Every test here reads src/ as text: none can kill a mutant (pyproject [tool.mutmut]).
pytestmark = pytest.mark.structural


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings
    ]


def test_package_cannot_reach_the_network() -> None:
    for path in PACKAGE.glob("*.py"):
        reached = {name for name in _imports(path) if name.split(".")[0] in FORBIDDEN_IN_PACKAGE}
        assert reached == set(), path


def test_no_existing_module_but_the_ops_mount_imports_the_package() -> None:
    importers = {
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        if PACKAGE not in path.parents
        and any(name.startswith("lovspor.forarbeider") for name in _imports(path))
    }
    assert importers == {"ops_cli.py"}


def test_package_states_no_licence_in_any_value() -> None:
    for path in PACKAGE.glob("*.py"):
        for literal in _string_literals(path):
            assert not any(word in literal for word in LICENCE_WORDS), (path, literal)


def _imports_git_reader(path: Path) -> bool:
    return any(name.split(".")[:2] == ["lovspor", "git_read"] for name in _imports(path))


def test_only_the_release_checkout_guard_imports_the_git_reader() -> None:
    """The one process the package may cause is read-only Git, from one module (#738, B1a)."""
    importers = {
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        if _imports_git_reader(path)
    }
    assert importers == {"forarbeider/release_checkout.py"}
