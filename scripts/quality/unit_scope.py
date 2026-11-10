#!/usr/bin/env python
"""Which unit test files a push has to run (issue #864).

The deep gate used to run the whole unit suite (~19k tests, ~15 min on one
core) on every push. With several agent sessions pushing at once on one Mac
that also hosts the CI runners, that took an hour. CI runs the whole suite on
every PR and stays authoritative; locally a push runs the tests its diff can
reach:

- a changed ``tests/unit/test_*.py`` runs itself;
- a changed ``src/lovspor`` module runs every test file that imports it,
  directly or through other modules and test helpers (the static import graph,
  read with ``ast``; a dotted module name in a string counts, for
  ``importlib.import_module`` and monkeypatch targets);
- a changed data file under ``src/lovspor`` counts as a change to its package;
- any other changed file runs the test files that name it.

The whole suite runs when the diff touches shared infrastructure (dependencies,
a conftest, a test helper or fixture, the quality scripts, the hook config),
when a source file does not parse, when a Python change reaches no test, when
git cannot say what changed, and when ``VERIFY_DEEP_FULL=1``.

Usage::

    uv run python scripts/quality/unit_scope.py   # prints the scope
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

_GIT = shutil.which("git") or "git"
_GIT_TIMEOUT = 60
SHARED_FILES = frozenset({"pyproject.toml", "uv.lock", ".pre-commit-config.yaml", "conftest.py"})
SRC = "src/lovspor/"
UNIT = "tests/unit/"
CONFTESTS = ("tests/conftest.py", "tests/unit/conftest.py")


@dataclass(frozen=True)
class Scope:
    full: str | None
    tests: tuple[str, ...]
    changed: int
    python_changed: bool


@dataclass(frozen=True)
class Graph:
    imports: dict[str, set[str]]
    files: dict[str, str]
    broken: frozenset[str]


def full_trigger(path: str) -> str | None:
    """Why a changed path runs the whole suite, or None."""
    name = path.rsplit("/", 1)[-1]
    if name in SHARED_FILES or path.startswith("scripts/quality/"):
        return f"{path} is shared infrastructure"
    if path.startswith("tests/") and not (name.startswith("test_") and name.endswith(".py")):
        return f"{path} is a test helper or fixture"
    return None


def module_name(path: str) -> str | None:
    """The dotted module a repo-relative ``.py`` path defines, for src and unit tests."""
    if not path.endswith(".py") or not path.startswith((SRC, UNIT)):
        return None
    relative = path[len("src/") :] if path.startswith(SRC) else path
    parts = relative[: -len(".py")].split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def package_of(module: str, path: str) -> list[str]:
    parts = module.split(".")
    return parts if path.endswith("__init__.py") else parts[:-1]


def _from_targets(node: ast.ImportFrom, package: list[str]) -> set[str]:
    base = package[: len(package) - node.level + 1] if node.level else []
    stem = ".".join([*base, *([node.module] if node.module else [])])
    return {stem} | {f"{stem}.{alias.name}" for alias in node.names}


def _strings(tree: ast.Module) -> set[str]:
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("lovspor.", "tests.unit."))
    }


def imports_of(tree: ast.Module, package: list[str]) -> set[str]:
    """Every dotted name the module imports or names, with all its parent packages."""
    found = _strings(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found |= _from_targets(node, package)
    return {
        ".".join(name.split(".")[:end]) for name in found for end in range(1, name.count(".") + 2)
    }


def _parse(root: Path, path: str) -> ast.Module | None:
    try:
        return ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
    except (SyntaxError, ValueError, OSError):
        return None


def _python_files(root: Path) -> list[str]:
    found = [*(root / "src" / "lovspor").rglob("*.py"), *(root / "tests" / "unit").rglob("*.py")]
    return sorted(path.relative_to(root).as_posix() for path in found)


def _shared_imports(root: Path) -> set[str]:
    trees = [_parse(root, path) for path in CONFTESTS if (root / path).is_file()]
    return set().union(*(imports_of(tree, []) for tree in trees if tree is not None))


def build_graph(root: Path) -> Graph:
    """Each src and unit-test module's imports; conftest imports reach every test."""
    shared = _shared_imports(root)
    imports: dict[str, set[str]] = {}
    files: dict[str, str] = {}
    broken: set[str] = set()
    for path in _python_files(root):
        module, tree = module_name(path), _parse(root, path)
        if module is None or path.endswith("conftest.py"):
            continue
        if tree is None:
            broken.add(path)
            continue
        extra = shared if path.startswith(UNIT) else set()
        imports[module] = (imports_of(tree, package_of(module, path)) - {module}) | extra
        files[module] = path
    return Graph(imports, files, frozenset(broken))


def reverse_closure(imports: Mapping[str, set[str]], seeds: Iterable[str]) -> set[str]:
    """The seeds and every module that imports one of them, directly or not."""
    importers: dict[str, set[str]] = {}
    for module, names in imports.items():
        for name in names:
            importers.setdefault(name, set()).add(module)
    reached, frontier = set(seeds), list(seeds)
    while frontier:
        for importer in importers.get(frontier.pop(), set()) - reached:
            reached.add(importer)
            frontier.append(importer)
    return reached


def seed_module(path: str) -> str | None:
    """The module a changed src or unit-test path stands for; data files stand for their package."""
    if path.endswith(".py"):
        return module_name(path)
    if path.startswith(SRC):
        return ".".join(path[len("src/") :].split("/")[:-1])
    return None


def _is_test_file(path: str) -> bool:
    return path.startswith(UNIT) and path.rsplit("/", 1)[-1].startswith("test_")


def _named_by(root: Path, tests: Iterable[str], path: str) -> set[str]:
    name = path.rsplit("/", 1)[-1]
    return {test for test in tests if name in (root / test).read_text(encoding="utf-8")}


def _selection(root: Path, graph: Graph, changed: list[str]) -> tuple[set[str], list[str]]:
    """The selected test files, and the paths elsewhere that selected none."""
    seeds = {seed for seed in map(seed_module, changed) if seed is not None}
    reached = reverse_closure(graph.imports, seeds)
    tests = {path for module, path in graph.files.items() if module in reached}
    tests = {path for path in tests if _is_test_file(path)}
    tests |= {path for path in changed if _is_test_file(path) and (root / path).is_file()}
    every_test = [path for path in graph.files.values() if _is_test_file(path)]
    unmapped: list[str] = []
    for path in (p for p in changed if not p.startswith((SRC, UNIT))):
        named = _named_by(root, every_test, path)
        tests |= named
        if not named and path.endswith(".py"):
            unmapped.append(path)
    return tests, unmapped


def _full(changed: list[str], reason: str) -> Scope:
    return Scope(full=reason, tests=(), changed=len(changed), python_changed=True)


def compute_scope(root: Path, changed: list[str]) -> Scope:
    """The test files a set of changed repo-relative paths reaches, or why it is everything."""
    reasons = [reason for reason in map(full_trigger, changed) if reason is not None]
    if reasons:
        return _full(changed, reasons[0])
    graph = build_graph(root)
    broken = sorted(path for path in graph.broken & set(changed) if path.startswith(SRC))
    if broken:
        return _full(changed, f"{broken[0]} does not parse")
    tests, unmapped = _selection(root, graph, changed)
    if unmapped:
        return _full(changed, f"no unit test names {unmapped[0]}")
    python_changed = any(path.endswith(".py") for path in changed)
    if not tests and any(path.startswith(SRC) for path in changed):
        return _full(changed, "no unit test imports the changed source")
    return Scope(None, tuple(sorted(tests)), len(changed), python_changed)


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 -- list argv, no shell
            [_GIT, *args],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def diff_base(root: Path, env: Mapping[str, str]) -> str | None:
    """pre-commit's from-ref, unless absent or all zeros (a branch new on the remote)."""
    from_ref = env.get("PRE_COMMIT_FROM_REF", "")
    if from_ref and from_ref.strip("0"):
        return from_ref
    return _git(root, "merge-base", "HEAD", "origin/main")


def changed_paths(root: Path, env: Mapping[str, str]) -> list[str] | None:
    """The paths the push changes, or None when git cannot say."""
    base = diff_base(root, env)
    head = env.get("PRE_COMMIT_TO_REF") or "HEAD"
    if base is None:
        return None
    out = _git(root, "diff", "--name-only", "--no-renames", f"{base}...{head}")
    return None if out is None else [line for line in out.splitlines() if line]


def scope_for_push(root: Path, env: Mapping[str, str]) -> Scope:
    if env.get("VERIFY_DEEP_FULL") == "1":
        return _full([], "VERIFY_DEEP_FULL=1")
    changed = changed_paths(root, env)
    if changed is None:
        return _full([], "git could not list the changed paths")
    return compute_scope(root, changed)


if __name__ == "__main__":
    print(scope_for_push(Path(__file__).resolve().parents[2], os.environ))
