"""Forarbeider stay offline and out of every existing law path (#738)."""

import ast
from pathlib import Path

import lovspor

SOURCE = Path(lovspor.__file__).parent
PACKAGE = SOURCE / "forarbeider"
FORBIDDEN_IN_PACKAGE = {"httpx", "requests", "urllib.request", "socket", "subprocess", "aiohttp"}
LICENCE_WORDS = ("NLOD", "CC0", "CC-BY")


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
